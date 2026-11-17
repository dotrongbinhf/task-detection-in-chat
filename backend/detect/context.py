"""Dựng ngữ cảnh cho bộ trích: code chọn task, LLM phán.

Prompt lần 1 = khối 1 hướng dẫn · `Conversation type` + <MEMBERS> · <CALENDAR> · <LINKED_TASKS> (task có tin bằng chứng được
TIN MỚI reply) · <TASK_DETAILS> (vài task có khả năng liên quan nhất, chi tiết đầy đủ) · <TASK_INDEX> (mọi task mở còn lại,
một dòng một task) · <RECENT_HISTORY> (K tin) · <NEW_MESSAGES>. `T<n>` đánh liên tục qua ba khối (v6 mục 6).
Lần gọi 2 (`detail_check`, hiếm) = nguyên prompt lần 1 + <TASK_DETAILS source="detail_check"> cho task chỉ có ở index.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from .config import Config
from .ledger import pending_for
from .models import EngineState, Member, Message, PendingChange, StreamEvent, Task
from .timeexpr import calendar14, fmt_day_time, fmt_time, local_date, parse, to_iso, ts, vi_weekday
from .tokens import count_tokens, truncate_tokens

REACTION_EMOJI = {"like": "👍", "love": "❤️", "heart": "❤️", "haha": "😆", "wow": "😮", "sad": "😢", "angry": "😠", "ok": "👌", "check": "✅"}
LINKED_HEADER = ("The sender of NEW_MESSAGES replied directly to an evidence message of the task below. "
                 "Assume NEW_MESSAGES are about this task unless their content clearly refers to something else.")
INDEX_HEADER = "Other open tasks, one line each (use them to recognise a task; UPDATE / CANCEL may target any T<n>):"
FINAL_CALL_NOTE = "This is the final call: return ops only."
OUTSIDE = "(message outside context)"
INDEX_MIN, DETAILS_MIN, K_MIN = 10, 1, 5


def conversation_key(m: Message) -> str:
    return f"{m.conversation_id};{m.thread_root_id}" if m.thread_root_id else m.conversation_id


@dataclass
class PromptMsg:
    local: str
    id: str
    kind: str  # message
    at: str
    author_id: str
    author_name: str
    text: str
    mentions: list[dict]
    reply_to_id: Optional[str]
    target_id: Optional[str]
    reaction_type: Optional[str]
    section: str  # evidence | reply | window | new | detail_check
    reactions: list[str] = field(default_factory=list)  # "👍 Nam" — reaction có created_at ≤ thời điểm lượt (v6 mục 4)


@dataclass
class PromptTask:
    local: str
    task: Task
    source: str  # linked | details | index | detail_check
    pending: list[PendingChange]
    evidence_shown: list[str]
    why: str = ""

    def public(self) -> dict[str, Any]:
        t = self.task
        return {"local": self.local, "task_id": t.task_id, "title": t.title, "status": t.status, "source": self.source,
                "why": self.why, "evidence_shown": self.evidence_shown}


@dataclass
class BuiltContext:
    key: str
    conv_type: str
    members: list[Member]
    messages: list[PromptMsg]
    new_msgs: list[PromptMsg]
    history: list[PromptMsg]
    tasks: list[PromptTask]
    id_map: dict[str, str]
    rev_map: dict[str, str]
    task_map: dict[str, str]
    blocks: dict[str, str]
    system: str
    user: str
    tokens: dict[str, int]
    trims: list[str]
    window_k: int
    window_ids: list[str]
    reply_ids: list[str]
    evidence_ids: list[str]
    turn_at: str
    retrieval: dict[str, Any] = field(default_factory=dict)  # văn bản truy vấn (embedding), tin reply được nối
    chain_ids: list[str] = field(default_factory=list)       # tin mà TIN MỚI reply (đi ngược chuỗi)
    index_count: int = 0                                     # số dòng trong <TASK_INDEX>
    call: int = 1                      # lần gọi bộ trích trong lượt (1 hoặc 2)
    cache_key: Optional[str] = None    # prompt_cache_key gửi OpenAI
    extra_block: str = ""              # khối <TASK_DETAILS source="detail_check"> (chỉ ở lần gọi 2)

    @property
    def candidates(self) -> list[dict[str, Any]]:
        return [p.public() for p in self.tasks]


def _to_prompt_msg(e: StreamEvent, section: str, until: Optional[str] = None,
                   names: Optional[dict[str, str]] = None) -> PromptMsg:
    """Tin nhắn → dòng prompt. Reaction không phải sự kiện: in kèm dòng tin, chỉ reaction có created_at ≤ `until`."""
    m = e.message
    assert m is not None
    text = m.text + ("".join(f" [file: {a}]" for a in m.attachments) if m.attachments else "")
    names = names or {}
    reacts = [f"{REACTION_EMOJI.get(r.type, r.type)} {names.get(r.user_id, r.user_id)}" for r in m.reactions
              if until is None or ts(r.created_at) <= ts(until)]
    return PromptMsg("", m.id, "message", m.created_at, m.author_id, m.author_name, text,
                     [x.model_dump() for x in m.mentions], m.reply_to_id, None, None, section, reacts)


def _fmt_msg(m: PromptMsg, prev_at: Optional[str], rev_map: dict[str, str], max_tokens: int) -> str:
    full = not prev_at or local_date(prev_at) != local_date(m.at) or ts(m.at) - ts(prev_at) > 3600
    stamp = fmt_day_time(m.at) if full else fmt_time(m.at)
    line = f"{m.local} [{stamp}] {m.author_name} ({m.author_id}): {truncate_tokens(m.text, max_tokens)}"
    if m.mentions:
        line += " (" + ", ".join(f"@{x['name']}={x['user_id']}" for x in m.mentions) + ")"
    if m.reply_to_id:
        line += f" ↩{rev_map.get(m.reply_to_id, OUTSIDE)}"
    if m.reactions:
        line += " [" + ", ".join(m.reactions) + "]"
    return line


def _fmt_list(lst: list[PromptMsg], rev_map: dict[str, str], max_tokens: int) -> str:
    lines, prev = [], None
    for m in lst:
        lines.append(_fmt_msg(m, prev, rev_map, max_tokens))
        prev = m.at
    return "\n".join(lines)


def _due_en(due_type: str, due_date: Optional[str], due_text: Optional[str]) -> str:
    """Hạn trong prompt: ngày ISO + thứ (khớp khối <CALENDAR>), sự kiện giữ nguyên chữ trong chat."""
    if due_type == "calendar" and due_date:
        return f"{due_date} ({vi_weekday(date.fromisoformat(due_date).isoweekday())})"
    if due_type == "event":
        return f'event: "{due_text or "?"}"'
    return "none"


def _who(t: Task, members: list[Member]) -> str:
    name_of = {m.user_id: m.display_name for m in members}
    return ", ".join(f"{name_of.get(u, u)} ({u})" for u in t.assignee_ids) or "(none)"


def _task_head(state: EngineState, t: Task, local: str, members: list[Member]) -> tuple[str, list[PendingChange]]:
    """`T1 [status | kind] "title" — assignee: Hà (u_ha) — due: …` (+ thay đổi đang chờ duyệt)."""
    pend = pending_for(state, t.task_id)
    who = _who(t, members)
    p_as = next((p for p in pend if p.field == "assignee_refs"), None)
    if p_as:
        who += f", PENDING APPROVAL: {p_as.new_display}"
    due = _due_en(t.due_type, t.due_date, t.due_text)
    p_due = next((p for p in pend if p.field == "due"), None)
    if p_due:
        nv = p_due.new_value or {}
        due += f", PENDING APPROVAL: {_due_en(nv.get('due_type', 'none'), nv.get('due_date'), nv.get('due_text'))}"
    extra = " — PENDING APPROVAL: cancel" if any(p.field == "status" for p in pend) else ""
    return f'{local} [{t.status} | {t.kind}] "{t.title}" — assignee: {who} — due: {due}{extra}', pend


def _index_line(t: Task, local: str, members: list[Member]) -> str:
    """Dòng gọn ở <TASK_INDEX>: `T7 "title" — An (u_an) — due 2026-12-01 (thứ 3)`; task declined có nhãn."""
    due = _due_en(t.due_type, t.due_date, t.due_text)
    tag = f"[{t.status}] " if t.status == "declined" else ""
    return f'{local} {tag}"{t.title}" — {_who(t, members)} — ' + ("no due" if due == "none" else f"due {due}")


def members_block(members: list[Member], include: Optional[set[str]], max_n: int) -> str:
    """`<MEMBERS>` — một dòng một người: `u_minh Minh (anh Minh, Minh Hoàng)`. Hội thoại đông hơn `max_n`: chỉ người trong `include`."""
    rows = members if len(members) <= max_n or include is None else [m for m in members if m.user_id in include]
    lines = [f"{m.user_id} {m.display_name}" + (f" ({', '.join(m.aliases)})" if m.aliases else "") for m in rows]
    return "<MEMBERS>\n" + ("\n".join(lines) or "(none)") + "\n</MEMBERS>"


def _ev_refs(t: Task, rev_map: dict[str, str]) -> str:
    refs = [f"{rev_map[e.message_id]} {e.role}" for e in t.evidence if e.message_id in rev_map]
    hidden = sum(1 for e in t.evidence if e.message_id not in rev_map)
    return ", ".join(refs + ([f"+{hidden} outside context"] if hidden else [])) or "—"


def _pick_evidence(t: Task, n: int, at_of: dict[str, str]) -> list[str]:
    """Tin giao (anchor) + các tin bằng chứng mới nhất, tối đa n."""
    if n <= 0:
        return []
    ids = [i for i in dict.fromkeys(e.message_id for e in t.evidence) if i in at_of]
    out = [t.anchor_message_id] if t.anchor_message_id in at_of else []
    for i in sorted(ids, key=lambda i: -ts(at_of[i])):
        if len(out) >= n:
            break
        if i not in out:
            out.append(i)
    return sorted(out[:n], key=lambda i: ts(at_of[i]))


def linked_message_ids(batch: list[StreamEvent], msgs: dict[str, Message], chain_max: int) -> list[tuple[str, str, int]]:
    """(message_id, cách link, số bước) — reply_to_id của TIN MỚI, đi ngược chuỗi reply ≤ chain_max (reaction không link)."""
    out: list[tuple[str, str, int]] = []
    seen: set[str] = set()
    for e in batch:
        cur = e.message.reply_to_id if e.message else None
        step = 0
        while cur and step < max(1, chain_max) and cur not in seen:
            seen.add(cur)
            out.append((cur, "reply", step))
            cur = msgs[cur].reply_to_id if cur in msgs else None
            step += 1
    return out


def to_iso_days_before(iso: str, days: float) -> str:
    return to_iso(parse(iso) - timedelta(days=days))


def query_text(store: Any, key: str, batch: list[StreamEvent], msgs: dict[str, Message],
               window_ids: set[str]) -> tuple[str, Optional[str]]:
    """Văn bản truy vấn (embedding) = nội dung TIN MỚI bỏ @mention; tin reply mà tin được reply nằm ngoài cửa sổ K → nối
    thêm nội dung tin đó ("ok em, lùi sang thứ 6 nhé" một mình không nói việc gì)."""
    batch_ids = {e.id for e in batch}
    texts = [e.message.text for e in batch if e.message]
    joined: Optional[str] = None
    for e in batch:
        rid = e.message.reply_to_id if e.message else None
        if rid and rid in msgs and rid not in window_ids and rid not in batch_ids:
            joined = rid
            break
    if joined:
        texts.append(msgs[joined].text)
    return store.strip_mentions(key, " ".join(texts)), joined


def build_context(state: EngineState, key: str, batch: list[StreamEvent], cfg: Config,
                  instructions: str, embedder: Any = None) -> BuiltContext:
    from .store import TaskStore  # tránh vòng import

    store = TaskStore(state, cfg, embedder)
    msgs = {e.id: e.message for e in state.events if e.message}
    by_id = {e.id: e for e in state.events}
    processed = set(state.processed)
    batch_ids = {e.id for e in batch}
    turn_at, first_at = batch[-1].at, batch[0].at
    members = state.members.get(key.split(";")[0], [])
    names = {m.user_id: m.display_name for m in members}
    conv_type = next((e.message.conversation_type for e in batch if e.message), None) or next(
        (e.message.conversation_type for e in state.events if e.message and e.key == key), "group")
    since = to_iso_days_before(turn_at, cfg.search_lookup_days)

    hist_msgs = [e for e in state.events if e.key == key and e.id in processed and e.id not in batch_ids and ts(e.at) <= ts(first_at)]

    # ---- tầng 1: task được link (TIN MỚI reply vào tin bằng chứng, đi ngược chuỗi reply) — không bao giờ bị cắt ----
    chain = linked_message_ids(batch, msgs, cfg.reply_chain_max)
    linked_tasks = store.linked(key, [c[0] for c in chain], since)[: max(0, cfg.linked_tasks_max)]
    linked_via = {t.task_id: next(c for c in chain if c[0] in {e.message_id for e in t.evidence}) for t in linked_tasks}

    # ---- tầng 2, 3: TASK_DETAILS (gần nhất + gần nghĩa) và TASK_INDEX (phần còn lại) ----
    base_window = {e.id for e in hist_msgs[-cfg.window_k:]} if cfg.window_k > 0 else set()
    q, joined = query_text(store, key, batch, msgs, base_window)
    tiers = store.tiers(key, linked_tasks, q, turn_at)

    at_of = {e.id: e.at for e in state.events if e.message and e.key == key and ts(e.at) <= ts(turn_at)}
    K, n_det, n_idx, det_anchor_on = cfg.window_k, len(tiers.details), len(tiers.index), True
    trims: list[str] = []

    def build() -> dict[str, Any]:
        details = tiers.details[:n_det]
        index = tiers.index[:n_idx]
        window = hist_msgs[-K:] if K > 0 else []
        window_ids = [e.id for e in window]
        wset = set(window_ids)

        reply_ids: list[str] = []
        for e in batch:
            cur = e.message.reply_to_id if e.message else None
            steps = 0
            while cur and steps < cfg.reply_chain_max:
                if cur not in wset and cur not in batch_ids and cur in msgs and cur not in reply_ids:
                    reply_ids.append(cur)
                cur = msgs[cur].reply_to_id if cur in msgs else None
                steps += 1

        shown = wset | set(reply_ids) | batch_ids
        ev_pick = {t.task_id: _pick_evidence(t, cfg.linked_evidence_per_task, at_of) for t in linked_tasks}
        for t, _ in details:
            ev_pick[t.task_id] = [t.anchor_message_id] if det_anchor_on and t.anchor_message_id in at_of else []
        # chỉ in kèm task những tin chưa xuất hiện ở LỊCH SỬ GẦN / TIN MỚI
        ev_shown = {tid: [i for i in v if i not in shown] for tid, v in ev_pick.items()}
        evidence_ids = list(dict.fromkeys(i for v in ev_shown.values() for i in v))

        pm = lambda e, sec: _to_prompt_msg(e, sec, turn_at, names)  # noqa: E731
        all_msgs = [pm(by_id[i], "evidence") for i in evidence_ids]
        all_msgs += [pm(by_id[i], "reply") for i in reply_ids if i not in evidence_ids]
        all_msgs += [pm(e, "window") for e in window]
        all_msgs += [pm(e, "new") for e in batch]
        rank = {"evidence": 0, "reply": 0, "window": 1, "new": 2}
        all_msgs.sort(key=lambda m: (ts(m.at), rank[m.section]))
        id_map, rev_map = {}, {}
        for i, m in enumerate(all_msgs):
            m.local = f"#{i + 1}"
            id_map[m.local] = m.id
            rev_map[m.id] = m.local
        pm_of = {m.id: m for m in all_msgs}
        mt = cfg.message_max_tokens

        task_map: dict[str, str] = {}
        prompt_tasks: list[PromptTask] = []
        lines_a: list[str] = []
        lines_d: list[str] = []
        lines_i: list[str] = []

        def local_for(t: Task) -> str:
            local = f"T{len(task_map) + 1}"
            task_map[local] = t.task_id
            return local

        for t in linked_tasks:
            local = local_for(t)
            mid, _, step = linked_via[t.task_id]
            ref = rev_map.get(mid, mid)
            why = f"NEW_MESSAGES reply to {ref}" if step == 0 else f"NEW_MESSAGES reply (via thread) to {ref}"
            head, pend = _task_head(state, t, local, members)
            lines_a.append(f"{head} — {why} — evidence: {_ev_refs(t, rev_map)}")
            lines_a += ["    " + _fmt_msg(pm_of[i], None, rev_map, mt) for i in ev_shown[t.task_id]]
            prompt_tasks.append(PromptTask(local, t, "linked", pend, ev_shown[t.task_id], why))
        for t, why in details:
            local = local_for(t)
            head, pend = _task_head(state, t, local, members)
            lines_d.append(head)
            if t.notes:
                lines_d.append(f"    notes: {truncate_tokens(t.notes, mt)}")
            lines_d += ["    " + _fmt_msg(pm_of[i], None, rev_map, mt) for i in ev_shown[t.task_id]]
            prompt_tasks.append(PromptTask(local, t, "details", pend, ev_shown[t.task_id], why))
        for t, why in index:
            local = local_for(t)
            lines_i.append(_index_line(t, local, members))
            prompt_tasks.append(PromptTask(local, t, "index", pending_for(state, t.task_id), [], why))

        history = [m for m in all_msgs if m.section in ("window", "reply")]
        new_msgs = [m for m in all_msgs if m.section == "new"]
        # người có mặt trong prompt: tác giả, người được @mention, người nhận / người giao của task trong prompt
        present = ({m.author_id for m in all_msgs} | {x["user_id"] for m in all_msgs for x in m.mentions}
                   | {u for p in prompt_tasks for u in [*p.task.assignee_ids, p.task.assigner_id] if u})
        head = f"Conversation type: {conv_type}.\n" + members_block(members, present, cfg.members_max)
        b = {
            "b1": instructions,
            "b2": f"<CALENDAR>\n{calendar14(turn_at).replace('← hôm nay', '← today')}\n</CALENDAR>",
            "b3a": ("<LINKED_TASKS>\n" + LINKED_HEADER + "\n" + "\n".join(lines_a) + "\n</LINKED_TASKS>") if lines_a else "",
            "b3b": "<TASK_DETAILS>\n" + ("\n".join(lines_d) or "(none)") + "\n</TASK_DETAILS>",
            "b3c": ("<TASK_INDEX>\n" + INDEX_HEADER + "\n" + "\n".join(lines_i) + "\n</TASK_INDEX>") if lines_i else "",
            "b4": "<RECENT_HISTORY>\n" + (_fmt_list(history, rev_map, mt) or "(empty)") + "\n</RECENT_HISTORY>",
            "b5": f"<NEW_MESSAGES>\n{_fmt_list(new_msgs, rev_map, mt)}\n</NEW_MESSAGES>",
        }
        tok = {"b1": count_tokens(b["b1"]), "b0": count_tokens(head), "b2": count_tokens(b["b2"]),
               "b3a": count_tokens(b["b3a"]), "b3b": count_tokens(b["b3b"]), "b3c": count_tokens(b["b3c"]),
               "b4": count_tokens(b["b4"]), "b5": count_tokens(b["b5"]), "b6": 0}
        tok["total"] = sum(tok.values())
        return dict(all=all_msgs, new=new_msgs, history=history, id_map=id_map, rev_map=rev_map, task_map=task_map,
                    tasks=prompt_tasks, blocks=b, tokens=tok, head=head, window_ids=window_ids, reply_ids=reply_ids,
                    evidence_ids=evidence_ids, index_count=len(index))

    r = build()
    # ---- ngân sách (v6 mục 6.5): tin anchor kèm TASK_DETAILS → giảm K (≥ 5) → dòng TASK_INDEX từ cuối (giữ ≥ 10)
    # → task TASK_DETAILS từ cuối (giữ ≥ 1). KHÔNG BAO GIỜ cắt LINKED_TASKS, MEMBERS.
    while r["tokens"]["total"] > cfg.context_token_budget:
        if det_anchor_on and any(p.source == "details" and p.evidence_shown for p in r["tasks"]):
            det_anchor_on = False
            trims.append("drop_details_anchor")
        elif K > K_MIN:
            K -= 1
            trims.append(f"reduce_k {K}")
        elif n_idx > INDEX_MIN:
            n_idx -= 1
            trims.append(f"drop_index {tiers.index[n_idx][0].task_id}")
        elif n_det > DETAILS_MIN:
            n_det -= 1
            trims.append(f"drop_details {tiers.details[n_det][0].task_id}")
        else:
            break
        r = build()

    b = r["blocks"]
    return BuiltContext(
        key=key, conv_type=conv_type, members=members, messages=r["all"], new_msgs=r["new"], history=r["history"],
        tasks=r["tasks"], id_map=r["id_map"], rev_map=r["rev_map"], task_map=r["task_map"], blocks=b, system=b["b1"],
        user="\n".join(x for x in [r["head"], b["b2"], b["b3a"], b["b3b"], b["b3c"], b["b4"], b["b5"]] if x),
        tokens=r["tokens"], trims=trims, window_k=K, window_ids=r["window_ids"], reply_ids=r["reply_ids"],
        evidence_ids=r["evidence_ids"], turn_at=turn_at, index_count=r["index_count"],
        retrieval={"query": q, "joined_reply": joined, "index_total": tiers.index_total,
                   "similarity": {k: round(v, 3) for k, v in tiers.sims.items()}},
        chain_ids=[c[0] for c in chain], cache_key=cfg.prompt_cache_key,
    )


def detail_check_targets(ctx: BuiltContext, out: Any) -> list[str]:
    """Lần gọi 2 kích hoạt bởi CẤU TRÚC op (v6 mục 6.4): UPDATE đặt `notes` hoặc `title` trên task chỉ có ở <TASK_INDEX>.
    `notes_add`, đổi hạn, đổi người, CANCEL không kích hoạt (không cần thấy chi tiết cũ để làm đúng)."""
    idx = {p.local: p.task.task_id for p in ctx.tasks if p.source == "index"}
    out_ids: list[str] = []
    for o in out.ops:
        tid = idx.get((o.target or "").strip())
        if o.op == "UPDATE" and tid and (o.notes is not None or o.title is not None) and tid not in out_ids:
            out_ids.append(tid)
    return out_ids


def extend_context(state: EngineState, ctx: BuiltContext, task_ids: list[str], cfg: Config) -> BuiltContext:
    """Lần gọi 2 = CÙNG prompt lần 1 (để cache trúng) + <TASK_DETAILS source="detail_check"> cho các task đã có ở index.

    `T<n>` giữ nguyên (task đã có trong prompt); tin anchor chưa có trong prompt được đánh `#n` nối tiếp."""
    c = copy.copy(ctx)
    c.id_map, c.rev_map, c.task_map = dict(ctx.id_map), dict(ctx.rev_map), dict(ctx.task_map)
    c.messages, c.tasks, c.tokens = list(ctx.messages), list(ctx.tasks), dict(ctx.tokens)
    by_id = {e.id: e for e in state.events}
    names = {m.user_id: m.display_name for m in ctx.members}
    local_of = {tid: loc for loc, tid in ctx.task_map.items()}
    n_msg = len(c.id_map)
    lines: list[str] = []
    for tid in task_ids:
        t = next(x for x in state.tasks if x.task_id == tid)
        head, pend = _task_head(state, t, local_of[tid], ctx.members)
        lines.append(head)
        if t.notes:
            lines.append(f"    notes: {truncate_tokens(t.notes, cfg.message_max_tokens)}")
        shown: list[str] = []
        i = t.anchor_message_id
        if i in by_id and i not in c.rev_map:
            n_msg += 1
            pm = _to_prompt_msg(by_id[i], "detail_check", ctx.turn_at, names)
            pm.local = f"#{n_msg}"
            c.id_map[pm.local] = i
            c.rev_map[i] = pm.local
            c.messages.append(pm)
            shown.append(i)
            lines.append("    " + _fmt_msg(pm, None, c.rev_map, cfg.message_max_tokens))
        c.tasks.append(PromptTask(local_of[tid], t, "detail_check", pend, shown, "notes/title trên task chỉ có ở index"))
    block = '<TASK_DETAILS source="detail_check">\n' + "\n".join(lines) + f"\n</TASK_DETAILS>\n{FINAL_CALL_NOTE}"
    c.extra_block = block
    c.user = ctx.user + "\n" + block
    c.tokens["b6"] = count_tokens(block)
    c.tokens["total"] = ctx.tokens["total"] + c.tokens["b6"]
    c.call = ctx.call + 1
    return c
