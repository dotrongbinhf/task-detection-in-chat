"""Đánh giá (mục 8): ghép task ↔ nhãn, chỉ số + khoảng tin cậy Wilson, danh sách lỗi kèm đường đi, report.md.

    python -m detect.eval --run runs/<id> --data data/sample
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Optional

from .config import ROOT, load_config
from .models import EngineState, GoldTask, PendingChange, Task
from .timeexpr import ts


def wilson(k: int, n: int) -> dict[str, Any]:
    if n == 0:
        return {"k": k, "n": n, "value": None, "lo": 0.0, "hi": 0.0, "enough": False}
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"k": k, "n": n, "value": p, "lo": max(0.0, c - h), "hi": min(1.0, c + h), "enough": n >= 30}


def pct(arr: list[float], q: float) -> Optional[float]:
    if not arr:
        return None
    s = sorted(arr)
    return s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))]


def proposed(t: Task, changes: list[PendingChange]) -> dict[str, Any]:
    """Giá trị hệ thống đề xuất sau cùng: giá trị task + đề xuất pending mới nhất."""
    v = {"assignee_ids": list(t.assignee_ids), "due_type": t.due_type, "due_date": t.due_date, "status": t.status}
    for c in changes:
        if c.task_id != t.task_id or c.status != "pending":
            continue
        if c.field == "assignee_refs":
            v["assignee_ids"] = c.new_value["ids"]
        elif c.field == "due":
            v["due_type"], v["due_date"] = c.new_value.get("due_type"), c.new_value.get("due_date")
        elif c.field == "status":
            v["status"] = "cancelled"
    return v


def evaluate(state: EngineState, pricing: dict[str, dict[str, float]] | None = None) -> dict[str, Any]:
    pricing = pricing or load_config().pricing
    gold: list[GoldTask] = state.gold or []
    tasks = state.tasks
    predicted = [t for t in tasks if t.shown_at]
    msgs = [e for e in state.events if e.kind == "message"]
    items: list[dict[str, Any]] = []

    # ---- ghép: cùng key, anchor task ∈ supporting hoặc anchor nhãn ∈ bằng chứng task; ưu tiên trùng anchor ----
    pairs = []
    for g in gold:
        for t in predicted:
            if t.conversation_key != g.conversation_key:
                continue
            ev = {e.message_id for e in t.evidence}
            if not (t.anchor_message_id in g.supporting_message_ids or g.anchor_message_id in ev):
                continue
            pairs.append((g, t, int(t.anchor_message_id == g.anchor_message_id), len(set(g.supporting_message_ids) & ev)))
    pairs.sort(key=lambda p: (-p[2], -p[3], ts(p[1].created_at)))
    g_match: dict[str, Task] = {}
    t_match: dict[str, GoldTask] = {}
    for g, t, _, _ in pairs:
        if g.gold_id in g_match or t.task_id in t_match:
            continue
        g_match[g.gold_id] = t
        t_match[t.task_id] = g
    paired_ids = {p[1].task_id for p in pairs}
    dups = [t for t in predicted if t.task_id not in t_match and t.task_id in paired_ids]
    fps = [t for t in predicted if t.task_id not in t_match and t.task_id not in paired_ids]

    labeled = [g for g in gold if g.assignee_ids]
    empty = [g for g in gold if not g.assignee_ids]
    tp = [g for g in labeled if g.gold_id in g_match]
    shown_empty = [g for g in empty if g.gold_id in g_match]
    kept_candidate = [g for g in empty if g.gold_id not in g_match and any(t.anchor_message_id == g.anchor_message_id and not t.shown_at for t in tasks)]

    def turn_path(ids: list[str]) -> list[str]:
        out = []
        for tr in state.turns:
            if not set(tr["new_event_ids"]) & set(ids):
                continue
            ctx = tr.get("context") or {}
            sel = " ".join(f"{s['local']}={s['task_id']}({s['score']})" for s in ctx.get("selected", [])) or "—"
            ops = ", ".join(o["op"] + ("✓" if o["accepted"] else f"✗({o['reason']})") for o in tr["ops"]) or "ops rỗng"
            gate = f" · p={tr['gate']['p']:.2f}" if tr.get("gate") and tr["gate"].get("p") is not None else ""
            trim = f" · trim: {','.join(ctx['trims'])}" if ctx.get("trims") else ""
            skip = f" bỏ: {tr['skipped']}" if tr.get("skipped") else ""
            out.append(f"Lượt {tr['turn']} [{'+'.join(tr['new_event_ids'])}]{skip} · task chọn: {sel}{gate}{trim} · {ops}")
        return out

    for g in gold:
        t = g_match.get(g.gold_id)
        if t:
            items.append({"kind": "tp" if g.assignee_ids else "shown_empty", "gold_id": g.gold_id, "task_id": t.task_id,
                          "note": f'"{g.title}" ↔ "{t.title}"' if g.assignee_ids else f"nhãn không có người nhận nhưng {t.task_id} đã hiện nháp (gán bừa?)",
                          "path": turn_path(g.supporting_message_ids)})
        elif g.assignee_ids:
            related = [tr for tr in state.turns if set(tr["new_event_ids"]) & set(g.supporting_message_ids)]
            hidden = next((x for x in tasks if (x.anchor_message_id == g.anchor_message_id or x.anchor_message_id in g.supporting_message_ids) and not x.shown_at), None)
            any_task = next((x for x in tasks if x.anchor_message_id in g.supporting_message_ids), None)
            cls = "3. bộ trích bỏ sót"
            if hidden:
                cls = f"6. phát hiện nhưng chưa hiện ({hidden.task_id} vẫn {hidden.status}) — lỗi gán người nhận"
            elif any(tr.get("skipped") == "gate_skip" for tr in related):
                cls = "5. cổng chặn"
            elif any(not o["accepted"] for tr in related for o in tr["ops"]):
                cls = "4. Validate loại"
            elif any_task and any(tr.get("context") and ts(tr["at"]) > ts(any_task.created_at)
                                  and not any(s["task_id"] == any_task.task_id for s in tr["context"]["selected"]) for tr in related):
                cls = f"1. task liên quan {any_task.task_id} không được chọn vào prompt"
            elif related and related[-1].get("context"):
                last = related[-1]
                in_prompt = set(last["context"]["id_map"].values())
                earlier = [i for i in g.supporting_message_ids if i not in last["new_event_ids"]
                           and any(m.id == i and ts(m.at) < ts(last["at"]) for m in msgs)]
                if any(i not in in_prompt for i in earlier):
                    cls = "2. tin cần thiết nằm ngoài ngữ cảnh"
            items.append({"kind": "fn", "gold_id": g.gold_id, "note": f'"{g.title}" — {cls}', "path": turn_path(g.supporting_message_ids)})
    for t in fps:
        items.append({"kind": "fp", "task_id": t.task_id, "note": f'"{t.title}" (anchor {t.anchor_message_id}) không khớp nhãn nào',
                      "path": turn_path([e.message_id for e in t.evidence])})
    for t in dups:
        gid = next(p[0].gold_id for p in pairs if p[1].task_id == t.task_id)
        items.append({"kind": "dup", "task_id": t.task_id, "note": f'"{t.title}" trùng nhãn {gid}', "path": turn_path([e.message_id for e in t.evidence])})

    # ---- trường + trích dẫn ----
    as_ok = as_n = due_ok = due_n = ev_n = fab = c_ok = c_n = 0
    ev_p: list[float] = []
    ev_r: list[float] = []
    for g in tp:
        t = g_match[g.gold_id]
        v = proposed(t, state.changes)
        as_n += 1
        if sorted(v["assignee_ids"]) == sorted(g.assignee_ids):
            as_ok += 1
        else:
            items.append({"kind": "field", "gold_id": g.gold_id, "task_id": t.task_id, "note": f"người nhận {','.join(v['assignee_ids']) or '∅'} ≠ nhãn {','.join(g.assignee_ids)}"})
        if g.due_type == "calendar":
            due_n += 1
            if v["due_date"] == g.due_date:
                due_ok += 1
            else:
                items.append({"kind": "field", "gold_id": g.gold_id, "task_id": t.task_id, "note": f"hạn {v['due_date'] or v['due_type']} ≠ nhãn {g.due_date}"})
        if g.due_type == "event":
            ev_n += 1
            if any(o["task_id"] == t.task_id and "fabricated_date_blocked" in o["notes"] for tr in state.turns for o in tr["ops"]):
                fab += 1
        if g.final_status == "cancelled":
            c_n += 1
            if v["status"] == "cancelled":
                c_ok += 1
            else:
                items.append({"kind": "field", "gold_id": g.gold_id, "task_id": t.task_id, "note": f"nhãn cancelled nhưng task ở {v['status']}"})
        ev = {e.message_id for e in t.evidence}
        inter = len(ev & set(g.supporting_message_ids))
        ev_p.append(inter / len(ev) if ev else 0)
        ev_r.append(inter / len(g.supporting_message_ids))
        if ev and inter / len(ev) < 0.8:
            items.append({"kind": "evidence", "gold_id": g.gold_id, "task_id": t.task_id, "note": "bằng chứng thừa: " + ", ".join(sorted(ev - set(g.supporting_message_ids)))})

    # ---- kịp thời ----
    lat_m: list[float] = []
    lat_s: list[float] = []
    upd: list[float] = []
    for g in tp:
        t = g_match[g.gold_id]
        am = next((m for m in msgs if m.id == g.anchor_message_id), None)
        if not am or not t.shown_at:
            continue
        lat_s.append(max(0.0, ts(t.shown_at) - ts(am.at)))
        lat_m.append(sum(1 for m in msgs if m.key == g.conversation_key and ts(am.at) < ts(m.at) <= ts(t.shown_at)))
        upd.append(t.draft_update_count)

    # ---- đề xuất thay đổi ----
    wrong = 0
    for c in state.changes:
        g = t_match.get(c.task_id)
        if not g:
            wrong += 1
        elif c.field == "due" and c.new_value.get("due_date") != g.due_date:
            wrong += 1
        elif c.field == "assignee_refs" and sorted(c.new_value["ids"]) != sorted(g.assignee_ids):
            wrong += 1
        elif c.field == "status" and g.final_status != "cancelled":
            wrong += 1
    repeated: dict[str, int] = {}
    for e in state.trace:
        if e.get("type") == "change_rejected":
            repeated[e["reason"]] = repeated.get(e["reason"], 0) + 1

    recreated = 0
    for g in [x for x in gold if "declined_not_recreated" in x.scenario_tags]:
        dec = next((t for t in tasks if t_match.get(t.task_id) and t_match[t.task_id].gold_id == g.gold_id and t.status in ("declined", "closed")), None)
        if dec:
            recreated += sum(1 for t in tasks if t is not dec and t.shown_at and ts(t.created_at) > ts(dec.status_changed_at)
                             and any(p[1] is t and p[0].gold_id == g.gold_id for p in pairs))

    # ---- chi phí ----
    with_ctx = [t for t in state.turns if t.get("context")]
    tokens = {b: {"p50": pct([t["context"]["tokens"][b] for t in with_ctx], 0.5), "p95": pct([t["context"]["tokens"][b] for t in with_ctx], 0.95)}
              for b in ("b1", "b2", "b3", "b4", "b5", "total")}
    ext = [t for t in state.turns if t.get("extract")]
    cost = in_tok = cached = out_tok = 0.0
    for t in ext:
        e = t["extract"]
        pr = pricing.get(e["model"], pricing.get("gpt-4.1-mini", {"input": 0, "cached": 0, "output": 0}))
        in_tok += e["input_tokens"]
        cached += e["cached_tokens"]
        out_tok += e["output_tokens"]
        cost += ((e["input_tokens"] - e["cached_tokens"]) * pr["input"] + e["cached_tokens"] * pr["cached"] + e["output_tokens"] * pr["output"]) / 1e6
    gates = [t for t in state.turns if t.get("gate")]
    for t in gates:
        pr = pricing.get(t["gate"]["model"], pricing.get("gpt-4.1-nano", {"input": 0, "output": 0}))
        cost += ((t["context"]["tokens"]["total"] if t.get("context") else 0) * 0.5 * pr["input"] + pr["output"]) / 1e6  # ước tính
    trims: dict[str, int] = {}
    for t in with_ctx:
        for tr in t["context"]["trims"]:
            k = tr.split(" ")[0]
            trims[k] = trims.get(k, 0) + 1
    op_turns = sum(1 for t in ext if any(o["accepted"] for o in t["ops"]))
    n_msgs = len(state.processed) or 1

    # ---- đường cong cổng (từ lần chạy shadow) ----
    g_turns = [t for t in state.turns if t.get("gate") and t["gate"].get("p") is not None]
    applied = [t for t in g_turns if any(o["accepted"] for o in t["ops"])]
    curve = [{"threshold": th, "recall_vs_ceiling": wilson(sum(1 for t in applied if t["gate"]["p"] >= th), len(applied)),
              "pass_rate": wilson(sum(1 for t in g_turns if t["gate"]["p"] >= th), len(g_turns))}
             for th in (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)]

    tags = sorted({tag for g in labeled for tag in g.scenario_tags})
    by_tag = [{"tag": tag, "recall": wilson(sum(1 for g in labeled if tag in g.scenario_tags and g.gold_id in g_match),
                                            sum(1 for g in labeled if tag in g.scenario_tags))} for tag in tags]
    hn = state.hardNegatives
    return {
        "hasGold": bool(gold),
        "summary": {
            "recall": wilson(len(tp), len(labeled)),
            "precision": wilson(len(tp), len(predicted)),
            "dup_rate": wilson(len(dups), len(predicted)),
            "hard_negative_fp": wilson(sum(1 for i in hn if any(t.anchor_message_id == i for t in predicted)), len(hn)),
            "declined_recreated": recreated,
            "assignee_acc": wilson(as_ok, as_n),
            "due_acc": wilson(due_ok, due_n),
            "event_fabricated": wilson(fab, ev_n),
            "cancel_correct": wilson(c_ok, c_n),
            "ev_precision": sum(ev_p) / len(ev_p) if ev_p else None,
            "ev_recall": sum(ev_r) / len(ev_r) if ev_r else None,
            "latency_msgs": {"p50": pct(lat_m, 0.5), "p90": pct(lat_m, 0.9)},
            "latency_sec": {"p50": pct(lat_s, 0.5), "p90": pct(lat_s, 0.9)},
            "draft_updates": {"p50": pct(upd, 0.5), "p90": pct(upd, 0.9)},
            "changes_proposed": len(state.changes),
            "changes_superseded": sum(1 for c in state.changes if c.status == "superseded"),
            "approved_tasks": sum(1 for t in tasks if "approved" in t.approvals.values()),
            "wrong_changes": wilson(wrong, len(state.changes)),
            "repeated_changes": repeated,
            "empty_assignee_kept_candidate": wilson(len(kept_candidate), len(empty)),
            "empty_assignee_shown": len(shown_empty),
        },
        "cost": {
            "turns": len(state.turns), "extract_calls": len(ext), "gate_calls": len(gates),
            "op_turn_ratio": wilson(op_turns, len(ext)), "tokens": tokens,
            "input_tokens": in_tok, "cached_tokens": cached, "output_tokens": out_tok,
            "cost_usd": cost, "cost_per_1000_msgs": cost / n_msgs * 1000, "budget_trims": trims, "messages": len(state.processed),
        },
        "gate_curve": curve,
        "by_tag": by_tag,
        "items": items,
        "matches": [{"gold_id": g, "task_id": t.task_id} for g, t in g_match.items()],
    }


# ---------- report.md ----------

def _r(x: dict[str, Any]) -> str:
    if x["value"] is None:
        return "— (n=0)"
    s = f"{x['value'] * 100:.0f}% ({x['k']}/{x['n']}, CI95 {x['lo'] * 100:.0f}–{x['hi'] * 100:.0f}%)"
    return s + ("" if x["enough"] else " · chưa đủ để kết luận")


def _p(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def to_markdown(rep: dict[str, Any], meta: dict[str, Any] | None = None) -> str:
    s, c = rep["summary"], rep["cost"]
    L = ["# Báo cáo đánh giá", ""]
    if meta:
        L += [f"- Dataset: `{meta.get('dataset')}` · chế độ: `{meta.get('mode')}` · bộ trích: `{meta.get('extract_model')}` · cổng: `{meta.get('gate_model')}` · gate_mode: `{meta.get('config', {}).get('gate_mode')}`", ""]
    L += ["## Phát hiện", "| Chỉ số | Giá trị | Ngưỡng |", "|---|---|---|",
          f"| Recall | {_r(s['recall'])} | ≥ 70% |", f"| Precision | {_r(s['precision'])} | ≥ 60% |",
          f"| Tỉ lệ trùng | {_r(s['dup_rate'])} | ≤ 5% |", f"| Báo nhầm hard negative | {_r(s['hard_negative_fp'])} | báo cáo |",
          f"| Tạo lại task đã từ chối | {s['declined_recreated']} | = 0 |", "",
          "## Trường", "| Chỉ số | Giá trị | Ngưỡng |", "|---|---|---|",
          f"| Người nhận đúng | {_r(s['assignee_acc'])} | ≥ 85% |", f"| Hạn lịch đúng | {_r(s['due_acc'])} | ≥ 80% |",
          f"| Hạn sự kiện bị bịa ngày | {_r(s['event_fabricated'])} | ≤ 5% |", f"| Huỷ đúng | {_r(s['cancel_correct'])} | báo cáo |", "",
          "## Trích dẫn & kịp thời",
          f"- Precision bằng chứng: {_p(s['ev_precision'])} (≥ 80%) · Recall bằng chứng: {_p(s['ev_recall'])} (≥ 70%)",
          f"- Độ trễ phát hiện: p50 {s['latency_msgs']['p50']} tin / {s['latency_sec']['p50']} s · p90 {s['latency_msgs']['p90']} tin / {s['latency_sec']['p90']} s",
          f"- Số lần nháp bị sửa: p50 {s['draft_updates']['p50']} · p90 {s['draft_updates']['p90']}", "",
          "## Gánh nặng duyệt",
          f"- Đề xuất thay đổi: {s['changes_proposed']} (superseded {s['changes_superseded']}) trên {s['approved_tasks']} task đã duyệt",
          f"- Đề xuất sai: {_r(s['wrong_changes'])} (≤ 20%) · bị Validate loại: {s['repeated_changes'] or 0}",
          f"- Nhãn không người nhận giữ đúng ở candidate: {_r(s['empty_assignee_kept_candidate'])} · bị hiện nhầm: {s['empty_assignee_shown']}", "",
          "## Chi phí",
          f"- Lượt: {c['turns']} · gọi bộ trích: {c['extract_calls']} · gọi cổng: {c['gate_calls']} · lượt có op: {_r(c['op_turn_ratio'])}",
          f"- Token input {c['input_tokens']:.0f} (cache {c['cached_tokens']:.0f}) · output {c['output_tokens']:.0f} · ước tính ${c['cost_usd']:.4f} (${c['cost_per_1000_msgs']:.3f} / 1.000 tin)",
          f"- budget_trim: {c['budget_trims'] or 0}", "", "| Khối | p50 | p95 |", "|---|---|---|"]
    names = {"b1": "1. Hướng dẫn", "b2": "2. Lịch", "b3": "3. Task liên quan", "b4": "4. Lịch sử gần", "b5": "5. Tin mới", "total": "Tổng"}
    for b, v in c["tokens"].items():
        L.append(f"| {names[b]} | {v['p50']} | {v['p95']} |")
    if any(x["pass_rate"]["n"] for x in rep["gate_curve"]):
        L += ["", "## Đường cong cổng (shadow)", "| Ngưỡng | Recall so với trần | Tỉ lệ lượt qua cổng |", "|---|---|---|"]
        L += [f"| {x['threshold']} | {_r(x['recall_vs_ceiling'])} | {_r(x['pass_rate'])} |" for x in rep["gate_curve"]]
    L += ["", "## Theo scenario_tags", "| Tag | Recall |", "|---|---|"] + [f"| {x['tag']} | {_r(x['recall'])} |" for x in rep["by_tag"]]
    L += ["", "## Lỗi kèm đường đi"]
    bad = [i for i in rep["items"] if i["kind"] != "tp"]
    if not bad:
        L.append("Không có lỗi.")
    for i in bad:
        L.append(f"- **{i['kind'].upper()}** {i.get('gold_id') or ''} {i.get('task_id') or ''}: {i['note']}")
        for p in i.get("path") or []:
            L.append(f"  - {p}")
    return "\n".join(L) + "\n"


def load_run(run_dir: Path) -> tuple[EngineState, dict[str, Any]]:
    state = EngineState.model_validate_json((run_dir / "state.json").read_text(encoding="utf-8"))
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8")) if (run_dir / "meta.json").exists() else {}
    return state, meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--data", default=None, help="(tuỳ chọn) dataset để lấy gold mới nhất")
    a = ap.parse_args()
    run_dir = Path(a.run) if Path(a.run).is_absolute() else ROOT / a.run
    state, meta = load_run(run_dir)
    if a.data:
        from .dataset import load_dataset

        ds = load_dataset(a.data)
        state.gold, state.hardNegatives = ds["gold"], ds["hard_negatives"]
    rep = evaluate(state)
    (run_dir / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    md = to_markdown(rep, meta)
    (run_dir / "report.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
