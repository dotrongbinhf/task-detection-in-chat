"""Đánh giá: ghép task ↔ nhãn, chỉ số + khoảng bootstrap theo hội thoại, chấm op theo lượt, lỗi kèm đường đi, report.md.

    python -m detect.eval --run runs/<id> --data data/eval
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
from pathlib import Path
from typing import Any, Callable, Optional

from .config import ROOT, load_config
from .models import EngineState, GoldTask, PendingChange, Task
from .text import norm
from .timeexpr import ts

BOOT_N = 2000
BOOT_SEED = 7
CLOSED = ("declined", "cancelled", "closed", "expired", "stale")
# nhãn lượt (`kinds`) → nhóm thay đổi; op → nhóm thay đổi (v6 mục 11.4)
KIND_CAT = {"assign": "create", "self_commit": "create", "open_ask": "create", "due": "due", "accept": "assignee",
            "assignee": "assignee", "content": "content", "cancel": "cancel"}
FIELD_CAT = {"assignee_refs": "assignee", "due": "due", "title": "content", "notes": "content", "kind": "content",
             "status": "cancel"}


def wilson(k: int, n: int) -> dict[str, Any]:
    if n == 0:
        return {"k": k, "n": n, "value": None, "lo": 0.0, "hi": 0.0}
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"k": k, "n": n, "value": p, "lo": max(0.0, c - h), "hi": min(1.0, c + h)}


def bootstrap(by_conv: dict[str, tuple[int, int]], n_boot: int = BOOT_N, seed: int = BOOT_SEED) -> dict[str, Any]:
    """Tỉ lệ Σk/Σn + khoảng 2,5–97,5% khi lấy mẫu lại HỘI THOẠI (nhãn cùng hội thoại không độc lập)."""
    k = sum(v[0] for v in by_conv.values())
    n = sum(v[1] for v in by_conv.values())
    out = {"k": k, "n": n, "value": k / n if n else None, "lo": 0.0, "hi": 0.0, "method": "bootstrap"}
    convs = [v for v in by_conv.values() if v[1]]
    if not convs:
        return out
    rng = random.Random(seed)
    vals = []
    for _ in range(n_boot):
        s = [convs[rng.randrange(len(convs))] for _ in convs]
        sn = sum(x[1] for x in s)
        vals.append(sum(x[0] for x in s) / sn)
    vals.sort()
    out["lo"], out["hi"] = vals[int(0.025 * n_boot)], vals[min(n_boot - 1, int(0.975 * n_boot))]
    return out


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


def _words(s: str) -> set[str]:
    return {w for w in re.split(r"[\W_]+", norm(s)) if w}


def assignees_at(t: Task, at: str) -> list[str]:
    """Người nhận của task NGAY TRƯỚC thời điểm `at` (lùi lịch sử trường assignee_refs có sau `at`)."""
    cur = list(t.assignee_ids)
    for h in reversed(t.field_history):
        if h.field != "assignee_refs" or ts(h.at) < ts(at):
            continue
        old = h.old
        cur = [x for x in old.split(",") if x] if isinstance(old, str) else list(old or [])
    return cur


def op_fields(o: dict[str, Any], tr: dict[str, Any]) -> list[str]:
    """Trường mà một op UPDATE đã áp / đề xuất. Lần chạy mới ghi `fields`; lần chạy cũ suy từ sự kiện op_applied."""
    if "fields" in o:
        return list(o["fields"])
    out: list[str] = []
    for e in tr.get("events") or []:
        if e.get("type") == "op_applied" and e.get("op") == "UPDATE" and e.get("task_id") == o.get("task_id"):
            out += [re.sub(r"^đề xuất ", "", a).split(" → ")[0] for a in e.get("changes") or []]
    return list(dict.fromkeys(out))


def op_cats(o: dict[str, Any], tr: dict[str, Any]) -> set[str]:
    """Nhóm thay đổi của một op ĐÃ ÁP; rỗng = op chỉ bổ sung bằng chứng (hoặc bị loại)."""
    if not o.get("accepted"):
        return set()
    if o["op"] == "CREATE":
        return {"create"}
    if o["op"] == "CANCEL":
        return {"cancel"}
    return {FIELD_CAT.get(f, "content") for f in op_fields(o, tr)}


def evaluate(state: EngineState, pricing: dict[str, dict[str, float]] | None = None) -> dict[str, Any]:
    pricing = pricing or load_config().pricing
    # chỉ chấm nhãn của các hội thoại có trong lần chạy (chạy một split, hoặc --data nạp nhãn của cả bộ)
    keys = {e.key for e in state.events}
    ev_ids = {e.id for e in state.events}
    gold: list[GoldTask] = [g for g in state.gold or [] if g.conversation_key in keys]
    hn = [h for h in state.hardNegatives if h in ev_ids]
    tasks = state.tasks
    predicted = [t for t in tasks if t.shown_at]
    msgs = [e for e in state.events if e.kind == "message"]
    author = {e.id: e.message.author_id for e in msgs if e.message}
    items: list[dict[str, Any]] = []
    convs = sorted({g.conversation_key for g in gold} | {t.conversation_key for t in predicted} | {tr["key"] for tr in state.turns})

    # ---- ghép: cùng key, anchor task ∈ supporting hoặc anchor nhãn ∈ bằng chứng task.
    # Khoá: trùng anchor → số tin bằng chứng chung → số người nhận chung → số từ chung giữa hai tiêu đề → tạo sớm hơn.
    # Hai nhãn chung một tin anchor ("Trâm export…, Đức kiểm tra…") được tách nhờ người nhận và tiêu đề (v6 mục 11.2).
    pairs = []
    for g in gold:
        for t in predicted:
            if t.conversation_key != g.conversation_key:
                continue
            ev = {e.message_id for e in t.evidence}
            if not (t.anchor_message_id in g.supporting_message_ids or g.anchor_message_id in ev):
                continue
            v = proposed(t, state.changes)
            pairs.append((g, t, (int(t.anchor_message_id == g.anchor_message_id), len(set(g.supporting_message_ids) & ev),
                                 len(set(v["assignee_ids"]) & set(g.assignee_ids)), len(_words(g.title) & _words(t.title)))))
    pairs.sort(key=lambda p: tuple(-x for x in p[2]) + (ts(p[1].created_at),))
    g_match: dict[str, Task] = {}
    t_match: dict[str, GoldTask] = {}
    for g, t, _ in pairs:
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
            sel = " ".join(f"{c['local']}={c['task_id']}({c['source']})" for c in ctx.get("candidates", [])) or "—"
            ops = ", ".join(o["op"] + ("✓" if o["accepted"] else f"✗({o['reason']})") for o in tr["ops"]) or "ops rỗng"
            trim = f" · trim: {','.join(ctx['trims'])}" if ctx.get("trims") else ""
            skip = f" bỏ: {tr['skipped']}" if tr.get("skipped") else ""
            out.append(f"Lượt {tr['turn']} [{'+'.join(tr['new_event_ids'])}]{skip} · task trong prompt: {sel}{trim} · {ops}")
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
            elif any(not o["accepted"] for tr in related for o in tr["ops"]):
                cls = "4. Validate loại"
            elif any_task and any(tr.get("context") and tr["turn"] > any_task.created_turn
                                  and any_task.task_id in set((tr["context"].get("task_map") or {}).values()) for tr in related):
                cls = f"8. task đúng {any_task.task_id} có trong prompt lần 1 nhưng model nhắm sai hoặc không làm gì"
            elif any_task and any(tr.get("context") and ts(tr["at"]) > ts(any_task.created_at)
                                  and any_task.task_id not in _prompt_tasks(tr) for tr in related):
                cls = f"7. task đúng {any_task.task_id} có trong kho nhưng không vào prompt (cả lần 1 lẫn lần 2)"
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
        orig = g_match.get(gid)
        why = ""
        tr = next((x for x in state.turns if x["turn"] == t.created_turn), None)
        if orig and tr and tr.get("context") and orig.created_turn < t.created_turn:
            if orig.task_id in set((tr["context"].get("task_map") or {}).values()):
                why = f" · {orig.task_id} có trong prompt nhưng LLM vẫn CREATE"
            else:
                why = f" · {orig.task_id} có trong kho nhưng không vào prompt"
        items.append({"kind": "dup", "task_id": t.task_id, "note": f'"{t.title}" trùng nhãn {gid}{why}', "path": turn_path([e.message_id for e in t.evidence])})

    # ---- trường + trích dẫn + nháp đúng hoàn toàn ----
    as_ok = as_n = due_ok = due_n = ev_n = fab = c_ok = c_n = 0
    ev_p: list[float] = []
    ev_r: list[float] = []
    strict: set[str] = set()
    per: dict[str, dict[str, list[int]]] = {}  # hội thoại -> chỉ số -> [k, n]

    def bump(conv: str, metric: str, ok: bool) -> None:
        x = per.setdefault(conv, {}).setdefault(metric, [0, 0])
        x[0] += int(ok)
        x[1] += 1

    for g in tp:
        t = g_match[g.gold_id]
        v = proposed(t, state.changes)
        good = True
        as_n += 1
        a_ok = sorted(v["assignee_ids"]) == sorted(g.assignee_ids)
        as_ok += a_ok
        bump(g.conversation_key, "assignee", a_ok)
        good &= a_ok
        if not a_ok:
            items.append({"kind": "field", "gold_id": g.gold_id, "task_id": t.task_id, "note": f"người nhận {','.join(v['assignee_ids']) or '∅'} ≠ nhãn {','.join(g.assignee_ids)}"})
        if g.due_type == "calendar":
            due_n += 1
            d_ok = v["due_date"] == g.due_date
            due_ok += d_ok
            bump(g.conversation_key, "due", d_ok)
            good &= d_ok
            if not d_ok:
                items.append({"kind": "field", "gold_id": g.gold_id, "task_id": t.task_id, "note": f"hạn {v['due_date'] or v['due_type']} ≠ nhãn {g.due_date}"})
        if g.due_type == "event":
            ev_n += 1
            if any(o["task_id"] == t.task_id and "fabricated_date_blocked" in o["notes"] for tr in state.turns for o in tr["ops"]):
                fab += 1
        if g.final_status == "cancelled":
            c_n += 1
            s_ok = v["status"] == "cancelled"
            c_ok += s_ok
            good &= s_ok
            if not s_ok:
                items.append({"kind": "field", "gold_id": g.gold_id, "task_id": t.task_id, "note": f"nhãn cancelled nhưng task ở {v['status']}"})
        if good:
            strict.add(g.gold_id)
        ev = {e.message_id for e in t.evidence}
        inter = len(ev & set(g.supporting_message_ids))
        ev_p.append(inter / len(ev) if ev else 0)
        ev_r.append(inter / len(g.supporting_message_ids))
        if ev and inter / len(ev) < 0.8:
            items.append({"kind": "evidence", "gold_id": g.gold_id, "task_id": t.task_id, "note": "bằng chứng thừa: " + ", ".join(sorted(ev - set(g.supporting_message_ids)))})
    for g in labeled:
        bump(g.conversation_key, "recall", g.gold_id in g_match)
        bump(g.conversation_key, "strict", g.gold_id in strict)
    for t in predicted:
        bump(t.conversation_key, "precision", t.task_id in t_match)

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

    # ---- chấm op theo lượt (turn_labels.jsonl, v6 mục 11.4) ----
    turn_ops = _turn_op_stats(state, author, per, bump)

    # ---- chi phí ----
    with_ctx = [t for t in state.turns if t.get("context")]
    blocks = sorted({b for t in with_ctx for b in t["context"]["tokens"]} - {"total"}) + ["total"]
    tokens = {b: {"p50": pct([t["context"]["tokens"].get(b, 0) for t in with_ctx], 0.5),
                  "p95": pct([t["context"]["tokens"].get(b, 0) for t in with_ctx], 0.95)} for b in blocks}
    ext = [t for t in state.turns if t.get("extract")]
    cost = in_tok = cached = out_tok = 0.0
    by_call: dict[int, dict[str, float]] = {}
    turn_in: list[float] = []
    for t in ext:
        calls = t.get("extract_calls") or [{**t["extract"], "call": 1}]
        tin = 0
        for e in calls:
            pr = pricing.get(e["model"], pricing.get("gpt-4o-mini", {"input": 0, "cached": 0, "output": 0}))
            c_usd = ((e["input_tokens"] - e["cached_tokens"]) * pr["input"] + e["cached_tokens"] * pr["cached"] + e["output_tokens"] * pr["output"]) / 1e6
            in_tok += e["input_tokens"]
            cached += e["cached_tokens"]
            out_tok += e["output_tokens"]
            cost += c_usd
            tin += e["input_tokens"]
            b = by_call.setdefault(min(int(e.get("call", 1)), 2), {"calls": 0, "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
            b["calls"] += 1
            b["input_tokens"] += e["input_tokens"]
            b["cached_tokens"] += e["cached_tokens"]
            b["output_tokens"] += e["output_tokens"]
            b["cost_usd"] += c_usd
        turn_in.append(tin)
    emb_tok = sum((t.get("embed") or {}).get("tokens", 0) for t in state.turns)
    emb_model = next(((t.get("embed") or {}).get("model") for t in state.turns if (t.get("embed") or {}).get("model")), None)
    emb_cost = emb_tok * pricing.get(emb_model or "", {}).get("input", 0) / 1e6
    cost += emb_cost
    trims: dict[str, int] = {}
    for t in with_ctx:
        for tr in t["context"]["trims"]:
            k = tr.split(" ")[0]
            trims[k] = trims.get(k, 0) + 1
    op_turns = sum(1 for t in ext if any(o["accepted"] for o in t["ops"]))
    n_msgs = len(state.processed) or 1
    batch = [len(t["new_event_ids"]) for t in state.turns]
    index_n = [t["context"]["index_count"] for t in with_ctx if "index_count" in t["context"]]
    detail_turns = sum(1 for t in ext if any(c.get("reason") == "detail_check" for c in t.get("extract_calls") or []))

    cand_stats = _candidate_stats(gold, g_match, tasks, ext, state, author)

    tags = sorted({tag for g in labeled for tag in g.scenario_tags})
    by_tag = [{"tag": tag, "recall": wilson(sum(1 for g in labeled if tag in g.scenario_tags and g.gold_id in g_match),
                                            sum(1 for g in labeled if tag in g.scenario_tags))} for tag in tags]

    def boot(metric: str) -> dict[str, Any]:
        return bootstrap({c: tuple(per.get(c, {}).get(metric, [0, 0])) for c in convs})

    return {
        "hasGold": bool(gold),
        "summary": {
            "strict_correct": boot("strict"),
            "strict_ids": sorted(strict),
            "recall": boot("recall"),
            "recall_ids": sorted(g.gold_id for g in tp),
            "precision": boot("precision"),
            "dup_rate": wilson(len(dups), len(predicted)),
            "hard_negative_fp": wilson(sum(1 for i in hn if any(t.anchor_message_id == i for t in predicted)), len(hn)),
            "declined_recreated": recreated,
            "assignee_acc": boot("assignee"),
            "due_acc": boot("due"),
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
            "false_alarm_turns": boot("false_alarm"),
        },
        "turn_ops": turn_ops,
        "cost": {
            "turns": len(state.turns), "extract_calls": len(ext),
            "op_turn_ratio": wilson(op_turns, len(ext)), "tokens": tokens,
            "input_tokens": in_tok, "cached_tokens": cached, "output_tokens": out_tok,
            "cost_usd": cost, "cost_per_1000_msgs": cost / n_msgs * 1000, "budget_trims": trims, "messages": len(state.processed),
            "by_call": by_call, "turn_input_tokens": {"p50": pct(turn_in, 0.5), "p95": pct(turn_in, 0.95)},
            "cache_ratio": (cached / in_tok) if in_tok else None,
            "embed_tokens": emb_tok, "embed_cost_usd": emb_cost, "embed_model": emb_model,
            "batch_size": {"p50": pct(batch, 0.5), "p95": pct(batch, 0.95)},
            "index_tasks": {"p50": pct(index_n, 0.5), "p95": pct(index_n, 0.95), "max": max(index_n) if index_n else None},
            "detail_check_turns": detail_turns,
        },
        "candidates": cand_stats,
        "by_tag": by_tag,
        "items": items,
        "matches": [{"gold_id": g, "task_id": t.task_id} for g, t in g_match.items()],
    }


def _turn_op_stats(state: EngineState, author: dict[str, str], per: dict, bump: Callable[[str, str, bool], None]) -> dict[str, Any]:
    """Lượt báo nhầm (nhãn 0, có op làm đổi sổ cái), lượt bỏ sót (nhãn 1, không op nào được áp), đúng loại op."""
    if not state.turnLabels:
        return {"available": False}
    by_ev: dict[str, dict[str, Any]] = {}
    for r in state.turnLabels:
        for i in r["new_event_ids"]:
            by_ev[i] = r
    hn_tag = {h: state.hardNegativeTags.get(h, "untagged") for h in state.hardNegatives}
    fa = miss = pos = neg = typed_ok = typed_n = 0
    by_hn: dict[str, list[int]] = {}
    items: list[str] = []
    for tr in state.turns:
        rows = list({id(by_ev[i]): by_ev[i] for i in tr["new_event_ids"] if i in by_ev}.values())
        if not rows:
            continue
        label = max(int(r["label"]) for r in rows)
        kinds = {k for r in rows for k in r.get("kinds") or []}
        cats = set().union(*(op_cats(o, tr) for o in tr["ops"])) if tr["ops"] else set()
        tag = f"Lượt {tr['turn']} [{'+'.join(tr['new_event_ids'])}]"
        if label == 0:
            neg += 1
            bad = bool(cats)
            fa += bad
            bump(tr["key"], "false_alarm", bad)
            for h in tr["new_event_ids"]:
                if h in hn_tag:
                    x = by_hn.setdefault(hn_tag[h], [0, 0])
                    x[0] += bad
                    x[1] += 1
            if bad:
                items.append(f"{tag}: báo nhầm ({', '.join(sorted(cats))})")
            continue
        pos += 1
        if not any(o["accepted"] for o in tr["ops"]):
            miss += 1
            items.append(f"{tag}: bỏ sót ({', '.join(sorted(kinds)) or 'không rõ loại'})")
        elif kinds and cats:
            typed_n += 1
            want = {KIND_CAT[k] for k in kinds if k in KIND_CAT}
            if cats == want:
                typed_ok += 1
            else:
                items.append(f"{tag}: sai loại (nhãn {', '.join(sorted(want))} · op {', '.join(sorted(cats))})")
    return {"available": True, "neg_turns": neg, "pos_turns": pos, "false_alarm": wilson(fa, neg), "missed": wilson(miss, pos),
            "type_correct": wilson(typed_ok, typed_n),
            "false_alarm_by_hn_tag": {k: wilson(v[0], v[1]) for k, v in sorted(by_hn.items())}, "items": items}


# ---------- report.md ----------

def _r(x: dict[str, Any]) -> str:
    if x["value"] is None:
        return "— (n=0)"
    ci = "boot" if x.get("method") == "bootstrap" else "CI95"
    return f"{x['value'] * 100:.0f}% ({x['k']}/{x['n']}, {ci} {x['lo'] * 100:.0f}–{x['hi'] * 100:.0f}%)"


def _p(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


def to_markdown(rep: dict[str, Any], meta: dict[str, Any] | None = None) -> str:
    s, c = rep["summary"], rep["cost"]
    cfg = (meta or {}).get("config", {})
    L = ["# Báo cáo đánh giá", ""]
    if meta:
        L += [f"- Dataset: `{meta.get('dataset')}` · chế độ: `{meta.get('mode')}` · bộ trích: `{meta.get('extract_model')}`"
              f" · seed: `{meta.get('seed', '-')}`", ""]
    L += ["Khoảng `boot` = bootstrap theo hội thoại (2.000 lần, 2,5–97,5%); `CI95` = Wilson (chỉ cho bảng phụ).", "",
          "## Phát hiện", "| Chỉ số | Giá trị | Ngưỡng |", "|---|---|---|",
          f"| **Nháp đúng hoàn toàn** | {_r(s['strict_correct'])} | báo cáo |",
          f"| Recall | {_r(s['recall'])} | ≥ 70% |", f"| Precision | {_r(s['precision'])} | ≥ 60% |",
          f"| Tỉ lệ trùng | {_r(s['dup_rate'])} | ≤ 5% |", f"| Báo nhầm hard negative (task) | {_r(s['hard_negative_fp'])} | báo cáo |",
          f"| Tạo lại task đã từ chối | {s['declined_recreated']} | = 0 |", "",
          "## Trường", "| Chỉ số | Giá trị | Ngưỡng |", "|---|---|---|",
          f"| Người nhận đúng | {_r(s['assignee_acc'])} | ≥ 85% |", f"| Hạn lịch đúng | {_r(s['due_acc'])} | ≥ 80% |",
          f"| Hạn sự kiện bị bịa ngày | {_r(s['event_fabricated'])} | ≤ 5% |", f"| Huỷ đúng | {_r(s['cancel_correct'])} | báo cáo |", ""]
    to = rep.get("turn_ops") or {}
    if to.get("available"):
        L += ["## Op theo lượt (turn_labels)", "| Chỉ số | Giá trị |", "|---|---|",
              f"| Lượt báo nhầm (nhãn 0, op làm đổi sổ cái) | {_r(s['false_alarm_turns'])} |",
              f"| Lượt bỏ sót (nhãn 1, không op nào được áp) | {_r(to['missed'])} |",
              f"| Đúng loại op (lượt dương có op) | {_r(to['type_correct'])} |"]
        for k, v in to["false_alarm_by_hn_tag"].items():
            L.append(f"| — báo nhầm hard negative `{k}` | {_r(v)} |")
        L.append("")
    quiet = cfg.get("quiet_seconds")
    lat = (f"- Độ trễ phát hiện (theo giờ tin): p50 {s['latency_msgs']['p50']} tin / {s['latency_sec']['p50']} s"
           f" · p90 {s['latency_msgs']['p90']} tin / {s['latency_sec']['p90']} s")
    if quiet:
        lat += f" · **độ trễ thật = số đo + quiet_seconds ({quiet:g} s)**"
    L += ["## Trích dẫn & kịp thời",
          f"- Precision bằng chứng: {_p(s['ev_precision'])} (≥ 80%) · Recall bằng chứng: {_p(s['ev_recall'])} (≥ 70%)", lat,
          f"- Số lần nháp bị sửa: p50 {s['draft_updates']['p50']} · p90 {s['draft_updates']['p90']}", "",
          "## Gánh nặng duyệt",
          f"- Đề xuất thay đổi: {s['changes_proposed']} (superseded {s['changes_superseded']}) trên {s['approved_tasks']} task đã duyệt",
          f"- Đề xuất sai: {_r(s['wrong_changes'])} (≤ 20%) · bị Validate loại: {s['repeated_changes'] or 0}",
          f"- Nhãn không người nhận giữ đúng ở candidate: {_r(s['empty_assignee_kept_candidate'])} · bị hiện nhầm: {s['empty_assignee_shown']}", "",
          "## Chi phí",
          f"- Lượt: {c['turns']} · gọi bộ trích: {c['extract_calls']} · lượt có op: {_r(c['op_turn_ratio'])}"
          f" · lượt detail_check: {c.get('detail_check_turns', 0)}",
          f"- Kích thước lượt (số tin): p50 {c['batch_size']['p50']} · p95 {c['batch_size']['p95']}"
          + (f" · task trong TASK_INDEX: p50 {c['index_tasks']['p50']} · p95 {c['index_tasks']['p95']} · max {c['index_tasks']['max']}"
             if c.get("index_tasks", {}).get("max") is not None else ""),
          f"- Token input {c['input_tokens']:.0f} (cache {c['cached_tokens']:.0f}) · output {c['output_tokens']:.0f} · ước tính ${c['cost_usd']:.4f} (${c['cost_per_1000_msgs']:.3f} / 1.000 tin)",
          f"- budget_trim: {c['budget_trims'] or 0}", "", "| Khối | p50 | p95 |", "|---|---|---|"]
    for b, v in c["tokens"].items():
        L.append(f"| {b} | {v['p50']} | {v['p95']} |")
    if c.get("by_call"):
        L += ["", "| Lần gọi | Số lần | Token input | Cache | Output | Tiền |", "|---|---|---|---|---|---|"]
        for k, v in sorted(c["by_call"].items(), key=lambda x: int(x[0])):
            L.append(f"| {k} | {v['calls']} | {v['input_tokens']:.0f} | {v['cached_tokens']:.0f} | {v['output_tokens']:.0f} | ${v['cost_usd']:.4f} |")
        L.append(f"- Token input mỗi lượt (cộng mọi lần gọi): p50 {c['turn_input_tokens']['p50']} · p95 {c['turn_input_tokens']['p95']}"
                 f" · tỉ lệ cache {_p(c.get('cache_ratio'))}")
    cs = rep.get("candidates")
    if cs and cs.get("n_turns"):
        L += ["", "## Tầng chọn task & tầng model",
              f"- **Candidate recall** (task đúng đã có trong kho có trong prompt lần 1): {_r(cs['first_call_recall'])}",
              "", "| Tầng | Số lượt |", "|---|---|"]
        L += [f"| lần 1 · {k} | {v} |" for k, v in cs["by_source"].items() if v]
        L += [f"| lần 2 | {cs['second_found']} |", f"| bỏ lỡ | {cs['missed']} |",
              f"| (không tính: task đã từ chối / đóng) | {cs['closed_excluded']} |", "",
              f"- **Link accuracy** (task đúng có trong prompt lần 1 → model nhắm đúng): {_r(cs['link_accuracy'])}"
              f" · nhắm task khác {cs['link_outcomes']['wrong_target']} · CREATE thay vì UPDATE {cs['link_outcomes']['create_instead']}"
              f" · không làm gì {cs['link_outcomes']['no_op']}",
              f"- Không cần đổi (tin chỉ `accept` của người đã là người nhận — không tính vào mẫu số): {cs['no_change_needed']}"]
        for x in cs["items"]:
            L.append(f"  - {x}")
    L += ["", "## Theo scenario_tags", "| Tag | Recall |", "|---|---|"] + [f"| {x['tag']} | {_r(x['recall'])} |" for x in rep["by_tag"]]
    if to.get("items"):
        L += ["", "## Lượt sai (op theo lượt)"] + [f"- {x}" for x in to["items"]]
    L += ["", "## Lỗi kèm đường đi"]
    bad = [i for i in rep["items"] if i["kind"] != "tp"]
    if not bad:
        L.append("Không có lỗi.")
    for i in bad:
        L.append(f"- **{i['kind'].upper()}** {i.get('gold_id') or ''} {i.get('task_id') or ''}: {i['note']}")
        for p in i.get("path") or []:
            L.append(f"  - {p}")
    return "\n".join(L) + "\n"


def _prompt_tasks(tr: dict[str, Any]) -> set[str]:
    """task_id có trong prompt của lượt (mọi lần gọi)."""
    c = tr.get("context") or {}
    return set((c.get("task_map_final") or c.get("task_map") or {}).values())


def _candidate_stats(gold: list[GoldTask], g_match: dict[str, Task], tasks: list[Task], ext: list[dict],
                     state: EngineState, author: dict[str, str]) -> dict[str, Any]:
    """Tầng chọn task (candidate recall theo tầng) tách khỏi tầng model (link accuracy).

    Đơn vị = lượt chứa tin bằng chứng (`supporting`) của một nhãn mà task đúng ĐÃ có trong kho trước lượt đó.
    Lượt mà tin chỉ là `accept` của người đã là người nhận (vd. "dạ" khi task đã giao đúng người) không cần đổi gì →
    đếm riêng, không tính vào mẫu số link accuracy (v6 mục 11.3)."""
    kinds_of: dict[str, set[str]] = {}
    for r in state.turnLabels:
        for i in r["new_event_ids"]:
            kinds_of.setdefault(i, set()).update(r.get("kinds") or [])
    mentioned = {e.id: {x.user_id for x in e.message.mentions} for e in state.events if e.kind == "message" and e.message}
    by_src: dict[str, int] = {}
    second_found = missed = n = hit1 = closed = no_need = 0
    outcomes = {"correct": 0, "wrong_target": 0, "create_instead": 0, "no_op": 0}
    items: list[str] = []
    for g in gold:
        t = g_match.get(g.gold_id) or next((x for x in tasks if x.anchor_message_id in g.supporting_message_ids), None)
        if not t:
            continue
        for tr in ext:
            if not set(tr["new_event_ids"]) & set(g.supporting_message_ids) or tr["turn"] <= t.created_turn:
                continue
            src = {c["task_id"]: c["source"] for c in (tr.get("context") or {}).get("candidates") or []}.get(t.task_id)
            tag = f"Lượt {tr['turn']} [{'+'.join(tr['new_event_ids'])}] {g.gold_id} → {t.task_id}"
            if not src and t.status in CLOSED and ts(t.status_changed_at) <= ts(tr["at"]):
                closed += 1
                items.append(f"{tag}: task đang {t.status} (không vào prompt theo thiết kế)")
                continue
            kinds = set().union(*(kinds_of.get(i, set()) for i in tr["new_event_ids"]))
            senders = {author.get(i) for i in tr["new_event_ids"]} - {None}
            # "đã là người nhận": theo sổ cái lúc đó, hoặc theo nhãn — tin giao việc đã @mention đúng người đó
            named = set(assignees_at(t, tr["at"])) | (set(g.assignee_ids) & mentioned.get(g.anchor_message_id, set()))
            if kinds and kinds <= {"accept"} and senders and senders <= named:
                no_need += 1
                continue
            n += 1
            if src:
                hit1 += 1
                by_src[src] = by_src.get(src, 0) + 1
                touched = [o for o in tr["ops"] if o.get("task_id") == t.task_id]
                other = [o for o in tr["ops"] if o["op"] in ("UPDATE", "CANCEL") and o.get("task_id") and o["task_id"] != t.task_id]
                created = [o for o in tr["ops"] if o["op"] == "CREATE" and o["accepted"]]
                res = ("correct" if touched else "wrong_target" if other else "create_instead" if created else "no_op")
                outcomes[res] += 1
                if res != "correct":
                    did = ("nhắm " + other[0]["task_id"] if res == "wrong_target" else "CREATE mới" if res == "create_instead"
                           else "không làm gì")
                    items.append(f"{tag}: có trong prompt lần 1 ({src}) nhưng model {did}")
            elif t.task_id in set((tr["context"].get("task_map_final") or {}).values()):
                second_found += 1
                items.append(f"{tag}: không có ở lần 1, vào lần 2")
            else:
                missed += 1
                items.append(f"{tag}: BỎ LỠ (không có ở lần 1 lẫn lần 2)")
    return {
        "n_turns": n, "first_call_recall": wilson(hit1, n), "by_source": by_src, "second_found": second_found,
        "missed": missed, "closed_excluded": closed, "no_change_needed": no_need,
        "link_accuracy": wilson(outcomes["correct"], hit1), "link_outcomes": outcomes, "items": items,
    }


def load_run(run_dir: Path) -> tuple[EngineState, dict[str, Any]]:
    raw = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    for t in raw.get("tasks", []):
        # lần chạy trước C2: `kind` đổi qua chuỗi JSON tự do nên có thể là giá trị lạ ("declined") — v6 schema đã chặn
        if t.get("kind") not in ("assigned", "self_commit", "open_ask"):
            t["kind"] = "assigned"
    state = EngineState.model_validate(raw)
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8")) if (run_dir / "meta.json").exists() else {}
    return state, meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--data", default=None, help="(tuỳ chọn) dataset để lấy nhãn mới nhất")
    a = ap.parse_args()
    run_dir = Path(a.run) if Path(a.run).is_absolute() else ROOT / a.run
    state, meta = load_run(run_dir)
    if a.data:
        from .dataset import apply_labels, load_dataset

        apply_labels(state, load_dataset(a.data))
    rep = evaluate(state)
    (run_dir / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    md = to_markdown(rep, meta)
    (run_dir / "report.md").write_text(md, encoding="utf-8")
    print(md[:3000])


if __name__ == "__main__":
    main()
