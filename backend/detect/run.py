"""Luồng tin → lượt xử lý → tasks.jsonl + trace.jsonl.

    python -m detect.run --data data/eval --split dev [--jobs 4] [--seed 7] [--no-cache] [--set key=value]

Mỗi lượt: hết hạn + user_actions → dựng ngữ cảnh (3 tầng task) → bộ trích (+ detail_check) → Validate → sổ cái.
`--jobs N`: mỗi hội thoại chạy trong một EngineState riêng trên một luồng rồi gộp (v6 mục 8.4); kết quả không phụ thuộc
thứ tự chạy vì id task / đề xuất đánh theo hội thoại và thời điểm hết hạn là đúng lúc hết TTL.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .config import ROOT, Config, load_config
from .context import BuiltContext, build_context, conversation_key, detail_check_targets, extend_context
from .dataset import load_dataset, load_prompts
from .extract import parse_extract, validate_and_apply
from .ledger import apply_expiry, apply_user_action
from .llm import LLM, make_llm
from .models import EngineState, ExtractOutput, Message, Mention, Reaction, StreamEvent, UserAction
from .timeexpr import parse, to_iso, ts
from .tokens import set_model


def _sort(events: list[StreamEvent]) -> None:
    events.sort(key=lambda e: (ts(e.at), e.id))


def build_events(messages: list[Message]) -> list[StreamEvent]:
    """Chỉ tin nhắn sinh sự kiện; reaction in kèm dòng tin trong prompt (v6 mục 4)."""
    evs = [StreamEvent(kind="message", id=m.id, key=conversation_key(m), at=m.created_at, message=m) for m in messages]
    _sort(evs)
    return evs


def conv_index(messages: list[Message], members: dict[str, Any]) -> dict[str, int]:
    """conversation_id → thứ tự (theo tin đầu tiên, rồi id) — dùng trong id task `K<thứ tự>.<n>`."""
    first: dict[str, tuple[float, str]] = {}
    for m in messages:
        k = (ts(m.created_at), m.conversation_id)
        if m.conversation_id not in first or k < first[m.conversation_id]:
            first[m.conversation_id] = k
    order = sorted(first, key=lambda c: first[c]) + sorted(c for c in members if c not in first)
    return {c: i + 1 for i, c in enumerate(order)}


def init_state(ds: dict[str, Any]) -> EngineState:
    events = build_events(ds["messages"])
    first = events[0].at if events else "2026-11-09T08:00:00+07:00"
    return EngineState(
        dataset=ds["name"], members=ds["members"], events=events,
        simNow=to_iso(parse(first) - timedelta(minutes=1)),
        userActions=sorted([a.model_copy(update={"source": "dataset"}) for a in ds["user_actions"]], key=lambda a: ts(a.at)),
        gold=ds["gold"], hardNegatives=ds["hard_negatives"], hardNegativeTags=ds.get("hard_negative_tags", {}),
        turnLabels=ds.get("turn_labels", []), convIndex=ds.get("conv_index") or conv_index(ds["messages"], ds["members"]),
    )


def pending_events(state: EngineState) -> list[StreamEvent]:
    done = set(state.processed)
    return [e for e in state.events if e.id not in done]


def next_batch(state: EngineState, cfg: Config) -> Optional[tuple[str, list[StreamEvent]]]:
    """Đợi hội thoại yên (v6 mục 3): sự kiện đầu chưa xử lý + các tin kế tiếp CÙNG conversation_key chừng nào khoảng cách
    tới tin liền trước ≤ quiet_seconds, cả lượt ≤ max_wait_seconds và ≤ max_batch_messages tin."""
    pend = pending_events(state)
    if not pend:
        return None
    first = pend[0]
    batch = [first]
    for e in pend[1:]:
        if e.key != first.key:
            continue
        if (ts(e.at) - ts(batch[-1].at) > cfg.quiet_seconds or ts(e.at) - ts(first.at) > cfg.max_wait_seconds
                or len(batch) >= cfg.max_batch_messages):
            break
        batch.append(e)
    return first.key, batch


def _waits_for_message(state: EngineState, a: UserAction) -> bool:
    """Thao tác duyệt (dataset) trỏ tới tin chưa được xử lý: tin đó đang nằm trong một lượt chưa chạy xong (đợi hội thoại
    yên) → hoãn tới ngay sau lượt chứa tin ấy ("duyệt khi nháp hiện ra"). Thao tác trỏ thẳng task_id / change_id (UI) không hoãn."""
    return bool(a.message_id) and not a.task_id and not a.change_id and a.message_id not in set(state.processed)


def catch_up(state: EngineState, t: str, cfg: Config) -> tuple[list[dict], list[dict]]:
    """Áp hết hạn + user_actions (dataset) có at ≤ t, trừ thao tác còn đợi tin của nó được xử lý."""
    exp: list[dict] = []
    acts: list[dict] = []
    while True:
        a = next((x for x in state.userActions if ts(x.at) <= ts(t) and not _waits_for_message(state, x)), None)
        if a is None:
            break
        state.userActions.remove(a)
        exp += apply_expiry(state, a.at, cfg)
        acts += apply_user_action(state, a, cfg)
    exp += apply_expiry(state, t, cfg)
    if ts(t) > ts(state.simNow):
        state.simNow = t
    return exp, acts


def apply_deferred(state: EngineState, at: str, cfg: Config) -> list[dict]:
    """Ngay sau một lượt: áp các thao tác đã đến hạn (at ≤ thời điểm lượt) mà trước đó phải đợi tin của nó, tại thời điểm lượt."""
    acts: list[dict] = []
    for a in [x for x in state.userActions if ts(x.at) <= ts(at) and x.message_id in set(state.processed)]:
        state.userActions.remove(a)
        acts += apply_user_action(state, a.model_copy(update={"at": at}), cfg)
    return acts


def end_time(state: EngineState) -> str:
    """Mốc cuối của dataset: tin hoặc thao tác duyệt muộn nhất."""
    times = [e.at for e in state.events] + [a.at for a in state.userActions] + [state.simNow]
    return max(times, key=ts)


def flush_actions(state: EngineState, cfg: Config, until: Optional[str] = None) -> None:
    """Hết luồng tin: áp nốt user_actions còn lại và hết hạn tới `until` (mặc định: hành động cuối)."""
    if until or state.userActions:
        catch_up(state, until or state.userActions[-1].at, cfg)


def process_turn(state: EngineState, cfg: Config, llm: LLM, prompts: dict[str, str]) -> Optional[dict[str, Any]]:
    nb = next_batch(state, cfg)
    if not nb:
        return None
    key, batch = nb
    state.counters.turn += 1
    turn = state.counters.turn
    at = batch[-1].at
    exp, acts = catch_up(state, at, cfg)
    rec: dict[str, Any] = {"turn": turn, "key": key, "at": at, "new_event_ids": [e.id for e in batch], "batch_size": len(batch),
                           "skipped": None, "pre": {"expirations": exp, "user_actions": acts}, "context": None,
                           "extract": None, "extract_calls": [], "detail_check": None, "ops": [], "events": []}

    def finish(skipped: Optional[str] = None, **extra: Any) -> dict[str, Any]:
        rec["skipped"] = skipped
        rec["post"] = {"user_actions": apply_deferred(state, at, cfg)}
        state.turns.append(rec)
        state.trace.append({"type": "turn", "at": at, "turn": turn, "key": key, "new": rec["new_event_ids"], "skipped": skipped, **extra})
        return rec

    state.processed += [e.id for e in batch]
    embedder = getattr(llm, "embedder", None)
    emb_before = getattr(embedder, "tokens_used", 0)
    ctx = build_context(state, key, batch, cfg, prompts["extract"], embedder)
    rec["context"] = {
        "candidates": ctx.candidates, "retrieval": ctx.retrieval, "linked_chain_ids": ctx.chain_ids,
        "window_ids": ctx.window_ids, "reply_chain_ids": ctx.reply_ids, "evidence_ids": ctx.evidence_ids,
        "id_map": ctx.id_map, "task_map": ctx.task_map, "trims": ctx.trims, "tokens": ctx.tokens, "window_k_used": ctx.window_k,
        "index_count": ctx.index_count,
        "prompt": ctx.user,  # khối 1 cố định, không lưu lại mỗi lượt
        "blocks": {**ctx.blocks, "b1": ""},
    }
    for t in ctx.trims:
        state.trace.append({"type": "budget_trim", "at": at, "turn": turn, "trim": t})

    # ---- bộ trích: lần 1, và lần 2 chỉ khi detail_check kích hoạt (tối đa 2 lần gọi mỗi lượt) ----
    calls: list[dict[str, Any]] = []
    rec["extract_calls"] = calls

    def call(c: BuiltContext, reason: str) -> Optional[ExtractOutput]:
        """Một lần gọi bộ trích (retry khi JSON hỏng / lỗi mạng). Ghi token, tiền, latency, raw của lần gọi."""
        parsed, err, last, attempts, latency = None, None, None, 0, 0
        while attempts <= cfg.llm_max_retries:
            try:
                last = llm.extract(c, attempts)
            except Exception as e:  # noqa: BLE001
                err = str(e)
                attempts += 1
                continue
            attempts += 1
            latency += last.latency_ms
            if last.refusal:
                break
            parsed, err = parse_extract(last.raw)
            if parsed:
                break
        r: dict[str, Any] = {
            "call": len(calls) + 1, "reason": reason, "model": llm.extract_model, "raw": last.raw if last else "",
            "attempts": attempts, "latency_ms": latency, "input_tokens": last.input_tokens if last else 0,
            "output_tokens": last.output_tokens if last else 0, "cached_tokens": last.cached_tokens if last else 0,
            "disk_cache": last.disk_cache if last else False, "prompt_tokens_est": c.tokens.get("total", 0),
            "extra_block": c.extra_block, "extra_tokens": c.tokens.get("b6", 0),
            "error": None if parsed else (f"refusal: {last.refusal}" if last and last.refusal else err),
        }
        r["cost_usd"] = call_cost(r, cfg.pricing)
        calls.append(r)
        rec["extract"] = {**r, "calls": len(calls)}
        if not parsed:
            kind = "refusal" if last and last.refusal else ("parse_failure" if last else "llm_error")
            r["failure"] = kind
            state.trace.append({"type": kind, "at": at, "turn": turn, "call": r["call"], "error": r["error"]})
        return parsed

    out = call(ctx, "initial")
    if not out:
        return finish(calls[-1]["failure"])  # lượt bị bỏ không làm mất tin: tin vẫn nằm trong LỊCH SỬ GẦN của lượt sau
    cur_ctx = ctx

    # ---- detail_check: UPDATE thay notes / title trên task chỉ có ở <TASK_INDEX> → gọi lần 2 kèm chi tiết task đó ----
    dc = detail_check_targets(ctx, out)
    if dc:
        ctx2 = extend_context(state, ctx, dc, cfg)
        state.trace.append({"type": "detail_check", "at": at, "turn": turn, "tasks": dc})
        rec["detail_check"] = {"tasks": dc, "ops_before": [f"{o.op} {o.target or ''}".strip() for o in out.ops]}
        out2 = call(ctx2, "detail_check")
        if not out2:
            return finish(calls[-1]["failure"])
        rec["detail_check"]["ops_after"] = [f"{o.op} {o.target or ''}".strip() for o in out2.ops]
        cur_ctx, out = ctx2, out2

    ops, events = validate_and_apply(state, cur_ctx, out, cfg, turn, llm.extract_model)
    rec["embed"] = {"model": getattr(embedder, "name", None), "tokens": getattr(embedder, "tokens_used", 0) - emb_before}
    rec["ops"] = ops
    rec["events"] += events
    if cur_ctx is not ctx:
        rec["context"]["task_map_final"] = cur_ctx.task_map
        rec["context"]["id_map_final"] = cur_ctx.id_map
    return finish(None, candidates=[f"{c['local']}={c['task_id']}({c['source']})" for c in ctx.candidates], tokens=ctx.tokens,
                  ops=[o["op"] + ("" if o["accepted"] else "✗") for o in ops], calls=len(calls))


def call_cost(r: dict[str, Any], pricing: dict[str, dict[str, float]]) -> float:
    pr = pricing.get(r["model"], pricing.get("gpt-4o-mini", {"input": 0, "cached": 0, "output": 0}))
    return ((r["input_tokens"] - r["cached_tokens"]) * pr.get("input", 0) + r["cached_tokens"] * pr.get("cached", 0)
            + r["output_tokens"] * pr.get("output", 0)) / 1e6


def run_all(state: EngineState, cfg: Config, llm: LLM, prompts: dict[str, str], until: Optional[str] = None,
            on_turn: Any = None) -> EngineState:
    """Chạy hết luồng tin của một state, rồi áp nốt thao tác duyệt + hết hạn tới `until`."""
    until = until or end_time(state)
    while (t := process_turn(state, cfg, llm, prompts)) is not None:
        if on_turn:
            on_turn(t)
    flush_actions(state, cfg, until)
    return state


# ---------- chạy song song theo hội thoại (v6 mục 8.4) ----------

def split_by_conversation(ds: dict[str, Any]) -> list[dict[str, Any]]:
    idx = conv_index(ds["messages"], ds["members"])
    out = []
    for conv in sorted(idx, key=idx.get):
        msgs = [m for m in ds["messages"] if m.conversation_id == conv]
        if not msgs:
            continue
        ids = {m.id for m in msgs}
        out.append({**ds, "messages": msgs, "members": {conv: ds["members"].get(conv, [])},
                    "gold": [g for g in ds["gold"] or [] if g.conversation_key.split(";")[0] == conv] or None,
                    "hard_negatives": [h for h in ds["hard_negatives"] if h in ids],
                    "user_actions": [a for a in ds["user_actions"] if a.message_id.split("#")[0] in ids],
                    "turn_labels": [r for r in ds.get("turn_labels", []) if r["conversation_key"].split(";")[0] == conv],
                    "conv_index": idx})
    return out


def merge_states(full: EngineState, parts: list[EngineState]) -> EngineState:
    """Nối tasks / changes / turns / trace của các state con; đánh lại số lượt theo thời gian (như khi chạy tuần tự)."""
    first_at = {e.id: e.at for e in full.events}
    turns = sorted(((p, t) for p in parts for t in p.turns),
                   key=lambda x: (ts(first_at[x[1]["new_event_ids"][0]]), x[1]["new_event_ids"][0]))
    renum: dict[tuple[int, int], int] = {}
    for i, (p, t) in enumerate(turns, 1):
        renum[(id(p), t["turn"])] = i
    for p in parts:
        for t in p.turns:
            new = renum[(id(p), t["turn"])]
            for e in t.get("events") or []:
                if "turn" in e:
                    e["turn"] = new
            t["turn"] = new
        for e in p.trace:
            if "turn" in e and (id(p), e["turn"]) in renum:
                e["turn"] = renum[(id(p), e["turn"])]
        for t in p.tasks:
            t.created_turn = renum.get((id(p), t.created_turn), t.created_turn)
    full.tasks = [t for p in parts for t in p.tasks]
    full.changes = [c for p in parts for c in p.changes]
    full.turns = [t for _, t in turns]
    full.trace = sorted((e for p in parts for e in p.trace), key=lambda e: ts(e["at"]) if e.get("at") else 0)
    full.processed = [e.id for e in full.events if any(e.id in set(p.processed) for p in parts)]
    full.userActions = []
    full.simNow = max((p.simNow for p in parts), key=ts, default=full.simNow)
    full.counters.turn = len(full.turns)
    full.counters.task = sum(p.counters.task for p in parts)
    full.counters.change = sum(p.counters.change for p in parts)
    for p in parts:
        full.counters.task_by_conv.update(p.counters.task_by_conv)
        full.counters.change_by_conv.update(p.counters.change_by_conv)
    return full


def run_dataset(ds: dict[str, Any], cfg: Config, llm: LLM, prompts: dict[str, str], jobs: int = 4,
                on_turn: Any = None) -> EngineState:
    full = init_state(ds)
    until = end_time(full)
    if jobs <= 1:
        return run_all(full, cfg, llm, prompts, until, on_turn)
    subs = [init_state(d) for d in split_by_conversation(ds)]
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        parts = list(ex.map(lambda s: run_all(s, cfg, llm, prompts, until, on_turn), subs))
    return merge_states(full, parts)


# ---------- thao tác từ UI ----------

def add_user_message(state: EngineState, conversation_id: str, author_id: str, text: str, mentions: list[str],
                     reply_to_id: Optional[str], at: str) -> Message:
    conv = state.members.get(conversation_id, [])
    name = lambda u: next((m.display_name for m in conv if m.user_id == u), u)  # noqa: E731
    state.counters.msg += 1
    existing = next((e.message for e in state.events if e.message and e.message.conversation_id == conversation_id), None)
    msg = Message(id=f"u{state.counters.msg:03d}", conversation_id=conversation_id,
                  conversation_type=existing.conversation_type if existing else ("dm" if conversation_id.startswith("dm_") else "group"),
                  created_at=at, author_id=author_id, author_name=name(author_id), text=text,
                  mentions=[Mention(user_id=u, name=name(u)) for u in mentions], reply_to_id=reply_to_id, source="user")
    state.events.append(StreamEvent(kind="message", id=msg.id, key=conversation_key(msg), at=at, message=msg))
    state.convIndex.setdefault(conversation_id, len(state.convIndex) + 1)
    _sort(state.events)
    return msg


def add_user_reaction(state: EngineState, target_id: str, user_id: str, type_: str, at: str) -> bool:
    """Reaction chỉ gắn vào tin (hiện ở dòng tin trong prompt các lượt sau), không sinh lượt (v6 mục 4)."""
    tgt = next((e for e in state.events if e.message and e.id == target_id), None)
    if not tgt:
        return False
    tgt.message.reactions.append(Reaction(user_id=user_id, type=type_, created_at=at))
    return True


def advance_time(state: EngineState, hours: float, cfg: Config) -> None:
    to = to_iso(parse(state.simNow) + timedelta(hours=hours))
    exp, _ = catch_up(state, to, cfg)
    state.trace.append({"type": "time_advanced", "at": to, "expirations": len(exp)})


def ui_user_action(state: EngineState, a: UserAction, cfg: Config) -> list[dict]:
    a = a.model_copy(update={"at": state.simNow, "source": "ui"})
    apply_expiry(state, a.at, cfg)
    return apply_user_action(state, a, cfg)


def now_plus(state: EngineState, seconds: float) -> str:
    done = set(state.processed)
    base = max([ts(state.simNow)] + [ts(e.at) for e in state.events if e.id in done])
    return to_iso(datetime.fromtimestamp(base + seconds).astimezone(parse(state.simNow).tzinfo))


# ---------- CLI ----------

def write_run(state: EngineState, run_dir: Path, cfg: Config, llm: LLM) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "tasks.jsonl", "w", encoding="utf-8") as f:
        for t in state.tasks:
            f.write(t.model_dump_json() + "\n")
    with open(run_dir / "changes.jsonl", "w", encoding="utf-8") as f:
        for c in state.changes:
            f.write(c.model_dump_json() + "\n")
    with open(run_dir / "trace.jsonl", "w", encoding="utf-8") as f:
        for tr in state.turns:
            f.write(json.dumps({"type": "turn_detail", **tr}, ensure_ascii=False) + "\n")
        for e in state.trace:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    meta = {"dataset": state.dataset, "mode": llm.mode, "seed": getattr(llm, "seed", None), "extract_model": llm.extract_model,
            "config": cfg.model_dump()}
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "state.json").write_text(state.model_dump_json(), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Chạy engine trên một dataset")
    ap.add_argument("--data", default="data/eval")
    ap.add_argument("--split", default=None, help="chỉ chạy một phần của split.json, vd. dev")
    ap.add_argument("--set", action="append", default=[], help="ghi đè config, vd. window_k=8")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--seed", type=int, default=7, help="seed của lời gọi LLM (đo dao động giữa các lần chạy)")
    ap.add_argument("--jobs", type=int, default=4, help="số hội thoại chạy song song")
    ap.add_argument("--id", default=None)
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args()
    cfg = load_config(sets=a.set)
    try:
        llm = make_llm(a.no_cache, a.seed)
    except RuntimeError as e:
        raise SystemExit(str(e)) from e
    set_model(llm.extract_model)

    def show(t: dict[str, Any]) -> None:
        if a.quiet:
            return
        ops = " | ".join(f"{'✓' if o['accepted'] else '✗'}{o['op']}({o['task_id'] or ''}) {o['effect'] or o['reason'] or ''}" for o in t["ops"])
        print(f"{t['key']:<12} {'+'.join(t['new_event_ids']):<28} {t['skipped'] or ''} "
              f"tok={t['context']['tokens']['total'] if t['context'] else '-'} {ops}", flush=True)

    state = run_dataset(load_dataset(a.data, a.split), cfg, llm, load_prompts(), a.jobs, show)
    run_id = a.id or f"{time.strftime('%Y%m%d_%H%M%S')}_{llm.mode}{'_' + a.split if a.split else ''}"
    run_dir = ROOT / "runs" / run_id
    write_run(state, run_dir, cfg, llm)
    print(f"\n{len(state.tasks)} task, {len(state.turns)} lượt → {run_dir}")
    print(f"Chấm điểm: python -m detect.eval --run runs/{run_id} --data {a.data}")  # eval tự lọc nhãn theo hội thoại đã chạy


if __name__ == "__main__":
    main()
