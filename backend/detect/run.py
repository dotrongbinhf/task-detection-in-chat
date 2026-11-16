"""Luồng sự kiện → lượt xử lý → tasks.jsonl + trace.jsonl (mục 2, 4.1, 4.7).

    python -m detect.run --data data/sample [--set gate_mode=shadow] [--mode heuristic|openai] [--no-cache]

Mỗi lượt: hết hạn + user_actions → dựng ngữ cảnh → cổng (tuỳ chọn) → bộ trích (+ kiểm tra target) → Validate → sổ cái.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .config import ROOT, Config, load_config
from .context import BuiltContext, build_context, conversation_key, extend_context
from .dataset import load_dataset, load_prompts
from .extract import parse_extract, target_check, validate_and_apply
from .gate import run_gate
from .ledger import apply_expiry, apply_user_action, is_open
from .llm import LLM, make_llm
from .models import EngineState, ExtractOutput, Message, Mention, Reaction, StreamEvent, UserAction
from .timeexpr import parse, to_iso, ts
from .tokens import set_model


def _sort(events: list[StreamEvent]) -> None:
    events.sort(key=lambda e: (ts(e.at), e.id))


def build_events(messages: list[Message], names: dict[str, str]) -> list[StreamEvent]:
    evs: list[StreamEvent] = []
    for m in messages:
        key = conversation_key(m)
        evs.append(StreamEvent(kind="message", id=m.id, key=key, at=m.created_at, message=m))
        for i, r in enumerate(m.reactions):
            evs.append(StreamEvent(kind="reaction", id=f"{m.id}#r{i + 1}", key=key, at=r.created_at, target_id=m.id,
                                   user_id=r.user_id, user_name=names.get(r.user_id, r.user_id), type=r.type))
    _sort(evs)
    return evs


def init_state(ds: dict[str, Any]) -> EngineState:
    names = {m.user_id: m.display_name for lst in ds["members"].values() for m in lst}
    events = build_events(ds["messages"], names)
    first = events[0].at if events else "2026-11-09T08:00:00+07:00"
    return EngineState(
        dataset=ds["name"], members=ds["members"], events=events,
        simNow=to_iso(parse(first) - timedelta(minutes=1)),
        userActions=sorted([a.model_copy(update={"source": "dataset"}) for a in ds["user_actions"]], key=lambda a: ts(a.at)),
        gold=ds["gold"], hardNegatives=ds["hard_negatives"], hardNegativeTags=ds.get("hard_negative_tags", {}),
        turnLabels=ds.get("turn_labels", []),
    )


def pending_events(state: EngineState) -> list[StreamEvent]:
    done = set(state.processed)
    return [e for e in state.events if e.id not in done]


def next_batch(state: EngineState, cfg: Config) -> Optional[tuple[str, list[StreamEvent]]]:
    """Sự kiện đầu chưa xử lý + các sự kiện cùng conversation_key trong coalesce_seconds."""
    pend = pending_events(state)
    if not pend:
        return None
    first = pend[0]
    return first.key, [e for e in pend if e.key == first.key and ts(e.at) - ts(first.at) <= cfg.coalesce_seconds]


def catch_up(state: EngineState, t: str, cfg: Config) -> tuple[list[dict], list[dict]]:
    """Áp hết hạn + user_actions (dataset) có at ≤ t."""
    exp: list[dict] = []
    acts: list[dict] = []
    while state.userActions and ts(state.userActions[0].at) <= ts(t):
        a = state.userActions.pop(0)
        exp += apply_expiry(state, a.at, cfg)
        acts += apply_user_action(state, a, cfg)
    exp += apply_expiry(state, t, cfg)
    if ts(t) > ts(state.simNow):
        state.simNow = t
    return exp, acts


def flush_actions(state: EngineState, cfg: Config) -> None:
    """Hết luồng tin: áp nốt user_actions còn lại của dataset (tại đúng thời điểm của chúng)."""
    if state.userActions:
        catch_up(state, state.userActions[-1].at, cfg)


def process_turn(state: EngineState, cfg: Config, llm: LLM, prompts: dict[str, str]) -> Optional[dict[str, Any]]:
    nb = next_batch(state, cfg)
    if not nb:
        return None
    key, batch = nb
    state.counters.turn += 1
    turn = state.counters.turn
    at = batch[-1].at
    exp, acts = catch_up(state, at, cfg)
    rec: dict[str, Any] = {"turn": turn, "key": key, "at": at, "new_event_ids": [e.id for e in batch], "coalesced": len(batch) > 1,
                           "skipped": None, "pre": {"expirations": exp, "user_actions": acts}, "context": None, "gate": None,
                           "extract": None, "extract_calls": [], "target_check": None, "ops": [], "events": []}

    def finish(skipped: Optional[str] = None, **extra: Any) -> dict[str, Any]:
        rec["skipped"] = skipped
        state.turns.append(rec)
        state.trace.append({"type": "turn", "at": at, "turn": turn, "key": key, "new": rec["new_event_ids"], "skipped": skipped, **extra})
        return rec

    # reaction chỉ sinh lượt khi tin được thả nằm trong K tin gần nhất hoặc là bằng chứng của task đang mở
    done = set(state.processed)
    recent = {e.id for e in [x for x in state.events if x.kind == "message" and x.key == key and x.id in done][-cfg.window_k:]}
    ev_open = {e.message_id for t in state.tasks if t.conversation_key == key and is_open(t, state) for e in t.evidence}
    batch_ids = {e.id for e in batch}
    kept = [e for e in batch if e.kind == "message" or e.target_id in recent or e.target_id in ev_open or e.target_id in batch_ids]
    dropped = [e.id for e in batch if e not in kept]
    state.processed += [e.id for e in batch]
    if dropped:
        tr = {"type": "reaction_history_only", "at": at, "turn": turn, "ids": dropped}
        state.trace.append(tr)
        rec["events"].append(tr)
    if not kept:
        return finish("reaction_not_relevant")

    embedder = getattr(llm, "embedder", None)
    emb_before = getattr(embedder, "tokens_used", 0)
    ctx = build_context(state, key, kept, cfg, prompts["extract"], prompts["gate"], embedder)
    rec["context"] = {
        "candidates": ctx.candidates, "retrieval": ctx.retrieval, "linked_chain_ids": ctx.chain_ids,
        "window_ids": ctx.window_ids, "reply_chain_ids": ctx.reply_ids, "evidence_ids": ctx.evidence_ids,
        "id_map": ctx.id_map, "task_map": ctx.task_map, "trims": ctx.trims, "tokens": ctx.tokens, "window_k_used": ctx.window_k,
        "prompt": ctx.user,  # khối 1 cố định, không lưu lại mỗi lượt
        "blocks": {**ctx.blocks, "b1": ""},
    }
    for t in ctx.trims:
        state.trace.append({"type": "budget_trim", "at": at, "turn": turn, "trim": t})

    # ---- cổng ----
    g = run_gate(llm, ctx, cfg)
    rec["gate"] = g
    if g and g.get("warning"):
        state.trace.append({"type": "gate_warning", "at": at, "turn": turn, "warning": g["warning"]})
    if g and g["skipped"]:
        state.trace.append({"type": "gate_skip", "at": at, "turn": turn, "p": g["p"]})
        return finish("gate_skip", p=g["p"])

    # ---- bộ trích: lần 1, và lần 2 chỉ khi kiểm tra target kích hoạt ----
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

    # ---- kiểm tra target: UPDATE / CANCEL nhắm task khớp TIN MỚI kém hơn một task ngoài prompt → gọi lần 2 ----
    if cfg.target_check_on_update and any(o.op in ("UPDATE", "CANCEL") for o in out.ops):
        tchecks = target_check(state, ctx, out, cfg, embedder)
        tc_hits = list({h.task.task_id: h for c in tchecks for h in c["_hits"]}.values())
        rec["target_check"] = {"checks": [{k: v for k, v in c.items() if k != "_hits"} for c in tchecks],
                               "triggered": bool(tc_hits), "call": None}
        if tc_hits:
            # đo: câu "nếu không hợp hơn thì giữ lựa chọn cũ" làm LLM trả ops rỗng ở m064 → giữ câu dẫn ngắn
            intro = ("Double-check: NEW_MESSAGES may refer to one of the tasks below (they were not in the prompt above). "
                     "If so, use that task's T<n>.")
            ctx2 = extend_context(state, ctx, tc_hits, cfg, intro)
            state.trace.append({"type": "target_check_triggered", "at": at, "turn": turn,
                                "candidates": [h.task.task_id for h in tc_hits]})
            out2 = call(ctx2, "target_check")
            rec["target_check"]["call"] = len(calls)
            if not out2:
                return finish(calls[-1]["failure"])
            rec["target_check"]["ops_after"] = [f"{o.op} {o.target or ''}".strip() for o in out2.ops]
            cur_ctx, out = ctx2, out2

    ops, events = validate_and_apply(state, cur_ctx, out, cfg, turn, llm.extract_model)
    rec["embed"] = {"model": getattr(embedder, "name", None), "tokens": getattr(embedder, "tokens_used", 0) - emb_before}
    rec["ops"] = ops
    rec["events"] += events
    if cur_ctx is not ctx:
        rec["context"]["task_map_final"] = cur_ctx.task_map
        rec["context"]["id_map_final"] = cur_ctx.id_map
    return finish(None, candidates=[f"{c['local']}={c['task_id']}({c['source']})" for c in ctx.candidates], tokens=ctx.tokens,
                  p=g["p"] if g else None, ops=[o["op"] + ("" if o["accepted"] else "✗") for o in ops], calls=len(calls))


def call_cost(r: dict[str, Any], pricing: dict[str, dict[str, float]]) -> float:
    pr = pricing.get(r["model"], pricing.get("gpt-4.1-mini", {"input": 0, "cached": 0, "output": 0}))
    return ((r["input_tokens"] - r["cached_tokens"]) * pr.get("input", 0) + r["cached_tokens"] * pr.get("cached", 0)
            + r["output_tokens"] * pr.get("output", 0)) / 1e6


# ---------- thao tác từ UI ----------

def add_user_message(state: EngineState, conversation_id: str, author_id: str, text: str, mentions: list[str],
                     reply_to_id: Optional[str], at: str) -> Message:
    conv = state.members.get(conversation_id, [])
    name = lambda u: next((m.display_name for m in conv if m.user_id == u), u)  # noqa: E731
    state.counters.msg += 1
    existing = next((e.message for e in state.events if e.kind == "message" and e.message.conversation_id == conversation_id), None)
    msg = Message(id=f"u{state.counters.msg:03d}", conversation_id=conversation_id,
                  conversation_type=existing.conversation_type if existing else ("dm" if conversation_id.startswith("dm_") else "group"),
                  created_at=at, author_id=author_id, author_name=name(author_id), text=text,
                  mentions=[Mention(user_id=u, name=name(u)) for u in mentions], reply_to_id=reply_to_id, source="user")
    state.events.append(StreamEvent(kind="message", id=msg.id, key=conversation_key(msg), at=at, message=msg))
    _sort(state.events)
    return msg


def add_user_reaction(state: EngineState, target_id: str, user_id: str, type_: str, at: str) -> Optional[StreamEvent]:
    tgt = next((e for e in state.events if e.kind == "message" and e.id == target_id), None)
    if not tgt:
        return None
    tgt.message.reactions.append(Reaction(user_id=user_id, type=type_, created_at=at))
    conv = state.members.get(tgt.message.conversation_id, [])
    ev = StreamEvent(kind="reaction", id=f"{target_id}#r{len(tgt.message.reactions)}", key=tgt.key, at=at, target_id=target_id,
                     user_id=user_id, user_name=next((m.display_name for m in conv if m.user_id == user_id), user_id), type=type_)
    state.events.append(ev)
    _sort(state.events)
    return ev


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
    meta = {"dataset": state.dataset, "mode": llm.mode, "seed": getattr(llm, "seed", None), "gate_model": llm.gate_model, "extract_model": llm.extract_model,
            "config": cfg.model_dump()}
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "state.json").write_text(state.model_dump_json(), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Chạy engine trên một dataset")
    ap.add_argument("--data", default="data/sample")
    ap.add_argument("--split", default=None, help="chỉ chạy một phần của split.json, vd. dev")
    ap.add_argument("--set", action="append", default=[], help="ghi đè config, vd. gate_mode=shadow")
    ap.add_argument("--mode", default="auto", choices=["auto", "openai", "heuristic"])
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--seed", type=int, default=7, help="seed của lời gọi LLM (đo dao động giữa các lần chạy)")
    ap.add_argument("--id", default=None)
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args()
    cfg = load_config(sets=a.set)
    llm = make_llm(a.mode, a.no_cache, a.seed)
    set_model(llm.extract_model)
    state = init_state(load_dataset(a.data, a.split))
    prompts = load_prompts()
    while (t := process_turn(state, cfg, llm, prompts)) is not None:
        if not a.quiet:
            ops = " | ".join(f"{'✓' if o['accepted'] else '✗'}{o['op']}({o['task_id'] or ''}) {o['effect'] or o['reason'] or ''}" for o in t["ops"])
            p = t["gate"]["p"] if t["gate"] else None
            print(f"#{t['turn']:>3} {'+'.join(t['new_event_ids']):<16} {t['skipped'] or ''} "
                  f"tok={t['context']['tokens']['total'] if t['context'] else '-'} p={'-' if p is None else round(p, 2)} {ops}")
    flush_actions(state, cfg)
    run_id = a.id or f"{time.strftime('%Y%m%d_%H%M%S')}_{llm.mode}{'_' + a.split if a.split else ''}"
    run_dir = ROOT / "runs" / run_id
    write_run(state, run_dir, cfg, llm)
    print(f"\n{len(state.tasks)} task, {len(state.turns)} lượt → {run_dir}")
    print(f"Chấm điểm: python -m detect.eval --run runs/{run_id} --data {a.data}")  # eval tự lọc nhãn theo hội thoại đã chạy


if __name__ == "__main__":
    main()
