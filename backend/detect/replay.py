"""Chạy nhanh khi đang chỉnh prompt / code: chỉ gọi LLM thật ở các lượt đáng xem, các lượt khác phát lại đầu ra cũ.

    python -m detect.replay --base runs/<lần chạy nền> --data data/eval --split dev [--neg 40] [--id my_try]

Engine vẫn chạy đủ mọi lượt bằng code hiện tại (sổ cái, ngữ cảnh, Validate dựng lại thật). Chỉ khác chỗ gọi bộ trích:
- lượt được chọn = lượt có việc (`turn_labels.jsonl` label = 1) + hard negative + `--neg` lượt không có việc lấy ngẫu nhiên
  → gọi LLM thật (có cache đĩa);
- lượt còn lại → trả lại JSON mà lần chạy nền đã nhận, sau khi dịch `#n` / `T<n>` của prompt cũ sang prompt mới
  (tin theo message_id, task theo tin anchor của task).

Giới hạn: lượt không được chọn giữ hành vi của lần chạy nền, nên thay đổi làm hỏng một lượt không có việc ngoài mẫu sẽ
không thấy. Trước khi chốt, chạy đủ: `python -m detect.run --split dev` hoặc cả bộ.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from pathlib import Path
from typing import Any

from .config import ROOT, load_config
from .context import BuiltContext
from .dataset import load_dataset, load_prompts, resolve_dir
from .eval import load_run
from .llm import ExtractResult, GateResult, _localize, make_llm
from .models import EngineState
from .run import flush_actions, init_state, process_turn, write_run
from .tokens import set_model

EMPTY = '{"message_type": "no_change", "ops": []}'


def pick_turns(data_dir: Path, event_ids: set[str], n_neg: int, seed: int) -> dict[str, str]:
    """{id sự kiện đầu của lượt: lý do chọn} — lượt có việc, hard negative, và n_neg lượt không có việc."""
    p = data_dir / "turn_labels.jsonl"
    if not p.exists():
        raise SystemExit(f"{data_dir} không có turn_labels.jsonl (cần nhãn theo lượt để chọn lượt)")
    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if r["new_event_ids"][0] in event_ids]
    out = {r["new_event_ids"][0]: "có việc" for r in rows if r["label"] == 1}
    out.update({r["new_event_ids"][0]: "hard negative" for r in rows if r.get("hard") and r["label"] == 0})
    rest = sorted(r["new_event_ids"][0] for r in rows if r["new_event_ids"][0] not in out)
    out.update({i: "không có việc (mẫu)" for i in random.Random(seed).sample(rest, min(n_neg, len(rest)))})
    return out


class ReplayLLM:
    """Bọc LLM thật: lượt được chọn → gọi thật; lượt khác → phát lại đầu ra của lần chạy nền."""

    def __init__(self, real: Any, state: EngineState, base: EngineState, selected: dict[str, str]):
        self.real, self.state, self.selected = real, state, selected
        self.mode, self.gate_model, self.extract_model = real.mode, real.gate_model, real.extract_model
        self.embedder = getattr(real, "embedder", None)
        self.base_turns = {i: tr for tr in base.turns for i in tr["new_event_ids"]}
        self.base_anchor = {t.task_id: t.anchor_message_id for t in base.tasks}
        self.real_calls = self.replayed = 0

    def gate(self, ctx: BuiltContext) -> GateResult:
        return self.real.gate(ctx)

    def extract(self, ctx: BuiltContext, attempt: int) -> ExtractResult:
        ids = [m.id for m in ctx.new_msgs]
        if any(i in self.selected for i in ids):
            self.real_calls += 1
            return self.real.extract(ctx, attempt)
        self.replayed += 1
        tr = next((self.base_turns[i] for i in ids if i in self.base_turns), None)
        calls = (tr or {}).get("extract_calls") or []
        if not calls:
            return ExtractResult(EMPTY)
        c = calls[min(ctx.call, len(calls)) - 1]
        bctx = tr["context"] or {}
        final = c["call"] > 1
        id_map = (bctx.get("id_map_final") if final else None) or bctx.get("id_map") or {}
        task_map = (bctx.get("task_map_final") if final else None) or bctx.get("task_map") or {}
        return ExtractResult(_localize(self._to_real_ids(c["raw"], id_map, task_map), ctx))

    def _to_real_ids(self, raw: str, id_map: dict[str, str], task_map: dict[str, str]) -> str:
        """`#n` → `@message_id`, `T<n>` → `@task_id hiện tại` (task có cùng tin anchor với task của lần chạy nền)."""
        try:
            out = json.loads(raw)
        except json.JSONDecodeError:
            return raw

        def msg(local: Any) -> Any:
            m = re.match(r"\s*(#\d+)", local or "")
            return f"@{id_map[m.group(1)]}" if m and m.group(1) in id_map else local

        def task(local: Any) -> Any:
            anchor = self.base_anchor.get(task_map.get((local or "").strip(), ""))
            cur = next((t for t in self.state.tasks if t.anchor_message_id == anchor), None) if anchor else None
            return f"@{cur.task_id}" if cur else local

        def speaker(s: str) -> str:
            return re.sub(r"SPEAKER:(#\d+)", lambda m: f"SPEAKER:{msg(m.group(1))}", s)

        for op in out.get("ops", []):
            op["target"] = task(op.get("target"))
            op["anchor"] = msg(op.get("anchor"))
            op["assignee_refs"] = [speaker(r) for r in op.get("assignee_refs") or []] or op.get("assignee_refs")
            for e in op.get("evidence") or []:
                e["msg"] = msg(e.get("msg"))
            for ch in op.get("changes") or []:
                ch["evidence_msg"] = msg(ch.get("evidence_msg"))
                ch["value"] = speaker(ch.get("value") or "")
        return json.dumps(out, ensure_ascii=False)


def _ops(tr: dict[str, Any] | None, tasks: dict[str, str]) -> str:
    """Tóm tắt ops của một lượt: `CREATE(anchor)`, `UPDATE(anchor task)`, ✗ khi bị loại."""
    if not tr:
        return "—"
    if tr.get("skipped"):
        return f"bỏ: {tr['skipped']}"
    return ", ".join(f"{o['op']}{'' if o['accepted'] else '✗'}({tasks.get(o.get('task_id') or '', o.get('target') or '')})"
                     for o in tr["ops"]) or "∅"


def main() -> None:
    ap = argparse.ArgumentParser(description="Chỉ gọi LLM thật ở lượt đáng xem, phát lại các lượt khác")
    ap.add_argument("--base", required=True, help="lần chạy nền, vd. runs/eval51_4omini")
    ap.add_argument("--data", default="data/eval")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--neg", type=int, default=40, help="số lượt không có việc lấy ngẫu nhiên để canh báo nhầm")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--mode", default="auto", choices=["auto", "openai", "heuristic"])
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--id", default=None)
    a = ap.parse_args()
    cfg = load_config(sets=a.set)
    real = make_llm(a.mode, a.no_cache)
    set_model(real.extract_model)
    base_dir = Path(a.base) if Path(a.base).is_absolute() else ROOT / a.base
    base, _ = load_run(base_dir)
    state = init_state(load_dataset(a.data, a.split))
    selected = pick_turns(resolve_dir(a.data), {e.id for e in state.events}, a.neg, a.seed)
    llm = ReplayLLM(real, state, base, selected)
    prompts = load_prompts()
    t0 = time.time()
    while process_turn(state, cfg, llm, prompts) is not None:
        pass
    flush_actions(state, cfg)

    run_id = a.id or f"{time.strftime('%Y%m%d_%H%M%S')}_replay_{a.split}"
    run_dir = ROOT / "runs" / run_id
    write_run(state, run_dir, cfg, real)
    meta_p = run_dir / "meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    meta["replay"] = {"base": a.base, "split": a.split, "selected": len(selected), "neg": a.neg, "seed": a.seed,
                      "real_calls": llm.real_calls, "replayed": llm.replayed}
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # so lượt được chọn: lần chạy nền ↔ lần này
    anchor_of = lambda tasks: {t.task_id: t.anchor_message_id for t in tasks}  # noqa: E731
    base_by = {tr["new_event_ids"][0]: tr for tr in base.turns}
    new_by = {tr["new_event_ids"][0]: tr for tr in state.turns}
    lines = [f"# Phát lại `{a.split}` trên nền `{a.base}`", "",
             f"- Gọi LLM thật: {llm.real_calls} lần · phát lại: {llm.replayed} lần · {time.time() - t0:.0f} s",
             "- Task ghi theo tin anchor. ✗ = op bị Validate loại.", "",
             "| Lượt (sự kiện đầu) | Vì sao chọn | Lần chạy nền | Lần này |", "|---|---|---|---|"]
    changed = 0
    for i, why in sorted(selected.items(), key=lambda x: x[0]):
        b, n = _ops(base_by.get(i), anchor_of(base.tasks)), _ops(new_by.get(i), anchor_of(state.tasks))
        if b != n:
            changed += 1
            lines.append(f"| {i} | {why} | {b} | **{n}** |")
    lines.insert(3, f"- Lượt được chọn có kết quả khác lần chạy nền: {changed}/{len(selected)} (bảng chỉ liệt kê lượt khác)")
    (run_dir / "replay.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:5]))
    print(f"→ {run_dir / 'replay.md'}")
    print(f"Chấm điểm: python -m detect.eval --run runs/{run_id} --data {a.data}")


if __name__ == "__main__":
    main()
