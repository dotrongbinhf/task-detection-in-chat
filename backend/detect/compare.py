"""Ghép report của nhiều lần chạy thành một bảng; cột đầu là lần chạy gate_mode=shadow.

    python -m detect.compare runs/*
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

from .eval import evaluate, load_run

ROWS = [
    ("Recall", lambda r: r["summary"]["recall"]),
    ("Precision", lambda r: r["summary"]["precision"]),
    ("Tỉ lệ trùng", lambda r: r["summary"]["dup_rate"]),
    ("Người nhận đúng", lambda r: r["summary"]["assignee_acc"]),
    ("Hạn lịch đúng", lambda r: r["summary"]["due_acc"]),
    ("Đề xuất sai", lambda r: r["summary"]["wrong_changes"]),
    ("Lượt có op", lambda r: r["cost"]["op_turn_ratio"]),
    ("Tạo lại task đã từ chối", lambda r: r["summary"]["declined_recreated"]),
    ("Lượt có tìm kiếm", lambda r: (r.get("search") or {}).get("search_turns", "—")),
    ("Lượt gọi lần 2", lambda r: (r.get("search") or {}).get("second_call_turns", "—")),
    ("— do LLM tìm / kiểm tra target / kiểm trùng", lambda r: "{} / {} / {}".format(r["search"]["second_call_by"].get("search", 0), r["search"]["second_call_by"].get("target_check", 0), r["search"]["second_call_by"].get("dedup", 0)) if (r.get("search") or {}).get("mode_linked") else "—"),
    ("Lượt nhắc việc ngoài ngữ cảnh → tìm thấy / tìm sai / bỏ lỡ tìm kiếm / không cần", lambda r: "{need_search} → {search_found} / {search_wrong} / {search_missed} / {search_not_needed}".format(**r["search"]) if (r.get("search") or {}).get("mode_linked") else "— (scored không tìm)"),
    ("Kiểm trùng: kích hoạt / bỏ CREATE / vẫn CREATE", lambda r: "{dedup_triggered} / {dedup_dropped} / {dedup_still_create}".format(**r["search"]) if (r.get("search") or {}).get("mode_linked") else "—"),
    # ---- v5 mục 10 ----
    ("Candidate recall (task đúng trong prompt lần 1)", lambda r: _cs(r, lambda c: c["first_call_recall"])),
    ("— qua linked / matched / recent", lambda r: _cs(r, lambda c: "{linked} / {matched} / {recent}".format(**{"linked": 0, "matched": 0, "recent": 0, **c["by_source"]}))),
    ("— không có ở lần 1 → vào lần 2 / bỏ lỡ hẳn", lambda r: _cs(r, lambda c: f"{sum(c['second_by'].values())} ({', '.join(f'{k} {v}' for k, v in c['second_by'].items()) or '—'}) / {c['missed']}")),
    ("Link accuracy (task đúng trong prompt → nhắm đúng)", lambda r: _cs(r, lambda c: c["link_accuracy"])),
    ("— nhắm task khác / CREATE thay UPDATE / không làm gì", lambda r: _cs(r, lambda c: "{wrong_target} / {create_instead} / {no_op}".format(**c["link_outcomes"]))),
    ("Lần gọi 2 theo lý do: llm_flag / target_check / dedup", lambda r: _cs(r, lambda c: f"{c['second_call_by'].get('llm_flag', 0)} / {c['second_call_by'].get('target_check', 0)} / {c['second_call_by'].get('dedup', 0)}")),
    ("LLM cắm cờ old_task_not_in_prompt (bị bỏ)", lambda r: _cs(r, lambda c: f"{c['llm_flag_turns']} ({c['llm_flag_ignored']})")),
    ("Chặn cứng: tổng / đúng / sai", lambda r: _cs(r, lambda c: f"{c['hard_blocks']} / {c['hard_ok']} / {c['hard_bad']}")),
    ("target_not_linked: op / link đúng / nhắm đúng", lambda r: _cs(r, lambda c: f"{c['target_not_linked']} / {c['tnl_linked_correct']} / {c['tnl_target_correct']}")),
]


def _cs(r, f):
    c = r.get("candidates") or {}
    return f(c) if c.get("n_turns") is not None and (r.get("search") or {}).get("mode_linked") else "—"


def fmt(x) -> str:
    if isinstance(x, dict) and "value" in x:
        return "—" if x["value"] is None else f"{x['value'] * 100:.0f}% ({x['k']}/{x['n']})"
    return str(x)


DATA = None


def main() -> None:
    """python -m detect.compare "runs/cmp_*" [--data data/sample] [--out runs/compare.md]"""
    global DATA
    args = sys.argv[1:]
    out = "runs/compare.md"
    if "--out" in args:
        i = args.index("--out")
        out = args[i + 1]
        del args[i: i + 2]
    if "--data" in args:
        from .dataset import load_dataset

        i = args.index("--data")
        DATA = load_dataset(args[i + 1])
        del args[i: i + 2]
    paths = [Path(p) for a in args for p in glob.glob(a)] or [p for p in Path("runs").iterdir()]
    runs = []
    for p in paths:
        if not (p / "state.json").exists():
            continue
        state, meta = load_run(p)
        if DATA:
            state.gold, state.hardNegatives = DATA["gold"], DATA["hard_negatives"]
        runs.append((p.name, meta, evaluate(state)))
    # cột đầu: gate_mode = none, context_mode = retrieve_first (tài liệu v5 mục 10); rồi các chế độ cũ, shadow sau cùng
    order = {"retrieve_first": 0, "linked_recent_search": 1, "scored": 2}
    runs.sort(key=lambda r: (r[1].get("config", {}).get("gate_mode") != "none",
                             order.get(r[1].get("config", {}).get("context_mode", "scored"), 3), r[0]))
    if not runs:
        print("Không có lần chạy nào (cần runs/<id>/state.json).")
        return
    head = "| Chỉ số | " + " | ".join(f"{n}<br>{m.get('extract_model')} · {m.get('config', {}).get('context_mode', 'scored')} · gate {m.get('config', {}).get('gate_mode')}" for n, m, _ in runs) + " |"
    lines = [head, "|---|" + "---|" * len(runs)]
    for name, f in ROWS:
        lines.append(f"| {name} | " + " | ".join(fmt(f(r)) for _, _, r in runs) + " |")
    lines.append("| Token prompt lần 1 p50 (ước tính) | " + " | ".join(str(r["cost"]["tokens"]["total"]["p50"]) for _, _, r in runs) + " |")
    lines.append("| Khối 1 hướng dẫn (token) | " + " | ".join(str(r["cost"]["tokens"]["b1"]["p50"]) for _, _, r in runs) + " |")
    lines.append("| Khối 3a / 3b p50 | " + " | ".join(f"{r['cost']['tokens'].get('b3a', {}).get('p50')} / {r['cost']['tokens'].get('b3b', {}).get('p50')}" for _, _, r in runs) + " |")
    lines.append("| Token input/lượt p50 · p95 (mọi lần gọi, OpenAI đếm) | " + " | ".join(
        f"{(r['cost'].get('turn_input_tokens') or {}).get('p50')} · {(r['cost'].get('turn_input_tokens') or {}).get('p95')}" for _, _, r in runs) + " |")
    lines.append("| Lần gọi bộ trích (lần 1 + lần 2) | " + " | ".join(
        " + ".join(str(v["calls"]) for _, v in sorted((r["cost"].get("by_call") or {}).items(), key=lambda x: int(x[0]))) or "—" for _, _, r in runs) + " |")
    lines.append("| Tiền lần 1 / lần 2 | " + " | ".join(
        " / ".join(f"${v['cost_usd']:.4f}" for _, v in sorted((r["cost"].get("by_call") or {}).items(), key=lambda x: int(x[0]))) or "—" for _, _, r in runs) + " |")
    lines.append("| Tỉ lệ token được cache | " + " | ".join(
        "—" if r["cost"].get("cache_ratio") is None else f"{r['cost']['cache_ratio'] * 100:.0f}%" for _, _, r in runs) + " |")
    lines.append("| Tìm kiếm · embedding (token, tiền) | " + " | ".join(
        f"{m.get('config', {}).get('search_mode', 'keyword') if m.get('config', {}).get('context_mode') != 'scored' else '—'} · {r['cost'].get('embed_tokens', 0)} tok, ${r['cost'].get('embed_cost_usd', 0):.4f}" for _, m, r in runs) + " |")
    lines.append("| Tổng tiền | " + " | ".join(f"${r['cost']['cost_usd']:.4f}" for _, _, r in runs) + " |")
    lines.append("| Chi phí / 1.000 tin | " + " | ".join(f"${r['cost']['cost_per_1000_msgs']:.3f}" for _, _, r in runs) + " |")
    md = "\n".join(lines) + "\n"
    print(md)
    Path(out).write_text(md, encoding="utf-8")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
