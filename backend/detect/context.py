"""Dựng ngữ cảnh cho bộ trích: code tìm task trước, LLM phán sau.

Prompt lần 1 = khối 1 hướng dẫn · <CALENDAR> · <LINKED_TASKS> (task có tin bằng chứng được TIN MỚI reply / thả reaction)
· <CANDIDATE_TASKS> (task khớp nội dung TIN MỚI `[matched: …]`, rồi task nhắc gần nhất `[recent]`) · <RECENT_HISTORY> (K tin)
· <NEW_MESSAGES>. Lần gọi 2 (chỉ khi kiểm tra target kích hoạt) = nguyên prompt lần 1 + khối <TASK_SEARCH_RESULTS>.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from .config import Config
from .ledger import pending_for
from .models import EngineState, Member, Message, PendingChange, StreamEvent, Task
from .timeexpr import calendar14, fmt_day_time, fmt_time, local_date, parse, to_iso, ts, vi_weekday
from .tokens import count_tokens, truncate_tokens

REACTION_EMOJI = {"like": "👍", "love": "❤️", "heart": "❤️", "haha": "😆", "wow": "😮", "sad": "😢", "angry": "😠", "ok": "👌", "check": "✅"}
LINKED_HEADER = ("The sender of NEW_MESSAGES replied directly to (or reacted on) an evidence message of the task below. "
                 "Assume NEW_MESSAGES are about this task unless their content clearly refers to something else.")
FINAL_CALL_NOTE = "This is the final call: return ops only."
OUTSIDE = "(message outside context)"


def conversation_key(m: Message) -> str:
    return f"{m.conversation_id};{m.thread_root_id}" if m.thread_root_id else m.conversation_id


@dataclass
class PromptMsg:
    local: str
    id: str
    kind: str  # message | reaction
    at: str
    author_id: str
    author_name: str
    text: str
    mentions: list[dict]
    reply_to_id: Optional[str]
    target_id: Optional[str]
    reaction_type: Optional[str]
    section: str  # evidence | reply | window | new | target_check


@dataclass
class PromptTask:
    local: str
    task: Task
    source: str  # linked | matched | recent | target_check
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
    gate_system: str
    gate_user: str
    turn_at: str
    retrieval: dict[str, Any] = field(default_factory=dict)  # truy vấn tìm task matched: query, people, joined_reply
    chain_ids: list[str] = field(default_factory=list)       # tin mà TIN MỚI reply / thả reaction (đi ngược chuỗi)
    call: int = 1                      # lần gọi bộ trích trong lượt (1 hoặc 2)
    cache_key: Optional[str] = None    # prompt_cache_key gửi OpenAI
    extra_block: str = ""              # khối <TASK_SEARCH_RESULTS> (chỉ ở lần gọi 2)

    @property
    def candidates(self) -> list[dict[str, Any]]:
        return [p.public() for p in self.tasks]


def _to_prompt_msg(e: StreamEvent, section: str) -> PromptMsg:
    if e.kind == "message":
        m = e.message
        assert m is not None
        text = m.text + ("".join(f" [file: {a}]" for a in m.attachments) if m.attachments else "")
        return PromptMsg("", m.id, "message", m.created_at, m.author_id, m.author_name, text,
                         [x.model_dump() for x in m.mentions], m.reply_to_id, None, None, section)
    return PromptMsg("", e.id, "reaction", e.at, e.user_id or "", e.user_name or "", "", [], None, e.target_id, e.type, section)


def _fmt_msg(m: PromptMsg, prev_at: Optional[str], rev_map: dict[str, str], max_tokens: int) -> str:
    full = not prev_at or local_date(prev_at) != local_date(m.at) or ts(m.at) - ts(prev_at) > 3600
    stamp = fmt_day_time(m.at) if full else fmt_time(m.at)
    if m.kind == "reaction":
        tgt = rev_map.get(m.target_id or "", OUTSIDE)
        return f"{m.local} [{stamp}] {m.author_name} {REACTION_EMOJI.get(m.reaction_type or '', m.reaction_type)} (reaction to {tgt})"
    line = f"{m.local} [{stamp}] {m.author_name}: {truncate_tokens(m.text, max_tokens)}"
    if m.mentions:
        line += " (" + ", ".join(f"@{x['name']}={x['user_id']}" for x in m.mentions) + ")"
    if m.reply_to_id:
        line += f" ↩{rev_map.get(m.reply_to_id, OUTSIDE)}"
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


def _task_head(state: EngineState, t: Task, local: str, members: list[Member]) -> tuple[str, list[PendingChange]]:
    """`T1 [status | kind] "title" — assignee: … — due: …` (+ thay đổi đang chờ duyệt)."""
    name_of = lambda u: next((m.display_name for m in members if m.user_id == u), u)  # noqa: E731
    pend = pending_for(state, t.task_id)
    who = ", ".join(name_of(u) for u in t.assignee_ids) or "(none)"
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


def _ev_refs(t: Task, rev_map: dict[str, str]) -> str:
    refs = [f"{rev_map[e.message_id]} {e.role}" for e in t.evidence if e.message_id in rev_map]
    hidden = sum(1 for e in t.evidence if e.message_id not in rev_map)
    return ", ".join(refs + ([f"+{hidden} outside context"] if hidden else [])) or "—"


def _pick_evidence(t: Task, n: int, at_of: dict[str, str]) -> list[str]:
    """Tin giao (anchor) + các tin bằng chứng mới nhất, tối đa n, chỉ tin nhắn (không lấy reaction)."""
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
    """(message_id, cách link, số bước) — reply_to_id / target_id của TIN MỚI, đi ngược chuỗi reply ≤ chain_max."""
    out: list[tuple[str, str, int]] = []
    seen: set[str] = set()
    for e in batch:
        how = "reply" if e.kind == "message" else "reaction"
        cur = e.message.reply_to_id if e.kind == "message" and e.message else e.target_id
        step = 0
        while cur and step < max(1, chain_max) and cur not in seen:
            seen.add(cur)
            out.append((cur, how, step))
            cur = msgs[cur].reply_to_id if cur in msgs else None
            step += 1
    return out


def to_iso_days_before(iso: str, days: float) -> str:
    return to_iso(parse(iso) - timedelta(days=days))


# Từ mô tả THAY ĐỔI chứ không mô tả VIỆC: "lùi sang thứ 2 tuần sau" khớp mọi task có chữ "tuần"/"lùi" (đo trên
# data/sample: chiếm 1–2 trong 3 chỗ matched). Bỏ khỏi truy vấn tìm ứng viên — "báo cáo tuần" vẫn giữ "tuần".
# shortcut: danh sách tay, chỉ tiếng Việt; chỉ ảnh hưởng xếp hạng ứng viên, không quyết định op nào.
CHANGE_WORDS_RE = re.compile(
    r"\b(tuần|tuan|tháng|thang)\s+(sau|này|nay|tới|toi|trước|truoc)\b"
    r"|\b(lùi|lui|dời|dời lại|đổi|hoãn|hoan|gia hạn|xong|huỷ|hủy|huy|khỏi|khoi|nhé|nha)\b", re.IGNORECASE)


def retrieval_query(store: Any, key: str, batch: list[StreamEvent], msgs: dict[str, Message],
                    window_ids: set[str]) -> tuple[str, list[str], Optional[str]]:
    """Truy vấn tìm task khớp TIN MỚI → (query_text, people, id tin được reply đã nối).

    query_text = nội dung mọi tin nhắn trong TIN MỚI (không gồm reaction), bỏ @mention; tin reply mà tin được reply nằm
    ngoài cửa sổ K → nối thêm nội dung tin được reply (tối đa 1 tin): "ok em, lùi sang thứ 6 nhé" một mình không có từ khoá.
    people = người gửi TIN MỚI + người được @mention + tác giả tin được reply + tên thành viên viết hoa trong tin."""
    batch_ids = {e.id for e in batch}
    texts = [e.message.text for e in batch if e.kind == "message" and e.message]
    people: list[str] = []
    joined: Optional[str] = None
    for e in batch:
        if e.kind == "message" and e.message:
            people.append(e.message.author_id)
            people += [x.user_id for x in e.message.mentions]
            rid = e.message.reply_to_id
            if rid and rid in msgs:
                people.append(msgs[rid].author_id)
                if joined is None and rid not in window_ids and rid not in batch_ids:
                    joined = rid
        elif e.user_id:
            people.append(e.user_id)
    if joined:
        texts.append(msgs[joined].text)
    raw = " ".join(texts)
    people += store.names_in_query(key, raw)
    q = re.sub(r"\s{2,}", " ", CHANGE_WORDS_RE.sub(" ", store.query_text(key, raw))).strip()
    return q, list(dict.fromkeys(p for p in people if p)), joined


def _matched_label(h: Any) -> str:
    """`[matched: bao, cao, tuan · 0.52]` — tối đa 3 từ khớp (bỏ dấu) và cosine nếu có."""
    words = [w for w in h.matched if not w.startswith(("người:", "ngữ nghĩa"))][:3]
    parts = ", ".join(words)
    if h.similarity is not None:
        parts = f"{parts} · {h.similarity:.2f}" if parts else f"{h.similarity:.2f}"
    return f"[matched: {parts}]" if parts else "[matched]"


def build_context(state: EngineState, key: str, batch: list[StreamEvent], cfg: Config,
                  instructions: str, gate_instructions: str, embedder: Any = None) -> BuiltContext:
    from .store import TaskStore  # tránh vòng import

    store = TaskStore(state, cfg, embedder)
    msgs = {e.id: e.message for e in state.events if e.kind == "message"}
    by_id = {e.id: e for e in state.events}
    processed = set(state.processed)
    batch_ids = {e.id for e in batch}
    turn_at, first_at = batch[-1].at, batch[0].at
    members = state.members.get(key.split(";")[0], [])
    conv_type = next((e.message.conversation_type for e in batch if e.kind == "message"), None) or next(
        (e.message.conversation_type for e in state.events if e.kind == "message" and e.key == key), "group")
    since = to_iso_days_before(turn_at, cfg.search_lookup_days)

    hist_events = [e for e in state.events if e.key == key and e.id in processed and e.id not in batch_ids and ts(e.at) <= ts(first_at)]
    hist_msgs = [e for e in hist_events if e.kind == "message"]

    # ---- task được link: TIN MỚI reply / thả reaction vào tin bằng chứng (đi ngược chuỗi reply) ----
    chain = linked_message_ids(batch, msgs, cfg.reply_chain_max)
    linked_tasks = store.linked(key, [c[0] for c in chain], since)[: max(0, cfg.linked_tasks_max)]
    linked_via = {t.task_id: next(c for c in chain if c[0] in {e.message_id for e in t.evidence}) for t in linked_tasks}

    # ---- ứng viên: task khớp nội dung TIN MỚI (matched), rồi task nhắc gần nhất (recent) ----
    base_window = {e.id for e in hist_msgs[-cfg.window_k:]} if cfg.window_k > 0 else set()
    q, q_people, joined = retrieval_query(store, key, batch, msgs, base_window)
    matched_hits, recent = store.candidates(key, q, q_people, since, {t.task_id for t in linked_tasks}, now=turn_at)
    cands: list[tuple[Task, str]] = [(h.task, _matched_label(h)) for h in matched_hits] + [(t, "[recent]") for t in recent]

    at_of = {e.id: e.at for e in state.events if e.kind == "message" and e.key == key and ts(e.at) <= ts(turn_at)}
    K, n_cand, cand_ev_on = cfg.window_k, len(cands), True
    trims: list[str] = []

    def build() -> dict[str, Any]:
        cur_cands = cands[:n_cand]
        window = hist_msgs[-K:] if K > 0 else []
        window_ids = [e.id for e in window]
        wset = set(window_ids)
        w_start = ts(window[0].at) if window else ts(first_at)
        window_events = [e for e in hist_events if e.id in wset or (e.kind == "reaction" and ts(e.at) >= w_start)]

        reply_ids: list[str] = []
        for e in batch:
            cur = e.message.reply_to_id if e.kind == "message" else e.target_id
            steps = 0
            while cur and steps < cfg.reply_chain_max:
                if cur not in wset and cur not in batch_ids and cur in msgs and cur not in reply_ids:
                    reply_ids.append(cur)
                cur = msgs[cur].reply_to_id if cur in msgs else None
                steps += 1

        shown = wset | set(reply_ids) | batch_ids
        ev_pick = {t.task_id: _pick_evidence(t, cfg.linked_evidence_per_task, at_of) for t in linked_tasks}
        for t, _ in cur_cands:
            ids = list(dict.fromkeys(e.message_id for e in sorted(t.evidence, key=lambda x: -ts(x.added_at)) if e.message_id in at_of))
            ev_pick[t.task_id] = ids[: cfg.evidence_per_task] if cand_ev_on else []
        # chỉ in kèm task những tin chưa xuất hiện ở LỊCH SỬ GẦN / TIN MỚI
        ev_shown = {tid: [i for i in v if i not in shown] for tid, v in ev_pick.items()}
        evidence_ids = list(dict.fromkeys(i for v in ev_shown.values() for i in v))

        all_msgs = [_to_prompt_msg(by_id[i], "evidence") for i in evidence_ids]
        all_msgs += [_to_prompt_msg(by_id[i], "reply") for i in reply_ids if i not in evidence_ids]
        all_msgs += [_to_prompt_msg(e, "window") for e in window_events]
        all_msgs += [_to_prompt_msg(e, "new") for e in batch]
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
        lines_b: list[str] = []

        def add(t: Task, source: str, why: str, lines: list[str]) -> None:
            local = f"T{len(task_map) + 1}"
            task_map[local] = t.task_id
            head, pend = _task_head(state, t, local, members)
            lines.append(f"{head} — {why} — evidence: {_ev_refs(t, rev_map)}")
            lines += ["    " + _fmt_msg(pm_of[i], None, rev_map, mt) for i in ev_shown[t.task_id]]
            prompt_tasks.append(PromptTask(local, t, source, pend, ev_shown[t.task_id], why))

        for t in linked_tasks:
            mid, how, step = linked_via[t.task_id]
            ref = rev_map.get(mid, mid)
            add(t, "linked", f"NEW_MESSAGES react to {ref}" if how == "reaction"
                else f"NEW_MESSAGES reply to {ref}" if step == 0 else f"NEW_MESSAGES reply (via thread) to {ref}", lines_a)
        for t, label in cur_cands:
            add(t, "recent" if label == "[recent]" else "matched", label, lines_b)

        history = [m for m in all_msgs if m.section in ("window", "reply")]
        new_msgs = [m for m in all_msgs if m.section == "new"]
        head = f"Conversation type: {conv_type}."
        b = {
            "b1": instructions,
            "b2": f"<CALENDAR>\n{calendar14(turn_at).replace('← hôm nay', '← today')}\n</CALENDAR>",
            "b3a": ("<LINKED_TASKS>\n" + LINKED_HEADER + "\n" + "\n".join(lines_a) + "\n</LINKED_TASKS>") if lines_a else "",
            "b3b": "<CANDIDATE_TASKS>\n" + ("\n".join(lines_b) or "(none)") + "\n</CANDIDATE_TASKS>",
            "b4": "<RECENT_HISTORY>\n" + (_fmt_list(history, rev_map, mt) or "(empty)") + "\n</RECENT_HISTORY>",
            "b5": f"<NEW_MESSAGES>\n{_fmt_list(new_msgs, rev_map, mt)}\n</NEW_MESSAGES>",
        }
        tok = {"b1": count_tokens(b["b1"]), "b2": count_tokens(b["b2"]), "b3a": count_tokens(b["b3a"]),
               "b3b": count_tokens(b["b3b"]), "b4": count_tokens(b["b4"]), "b5": count_tokens(head + "\n" + b["b5"]), "b6": 0}
        tok["total"] = sum(tok.values())
        return dict(all=all_msgs, new=new_msgs, history=history, id_map=id_map, rev_map=rev_map, task_map=task_map,
                    tasks=prompt_tasks, blocks=b, tokens=tok, head=head, window_ids=window_ids, reply_ids=reply_ids,
                    evidence_ids=evidence_ids)

    r = build()
    # ---- ngân sách: bằng chứng kèm ứng viên → giảm K (≥ 5) → ứng viên từ cuối (giữ ≥ 1). KHÔNG BAO GIỜ cắt LINKED_TASKS.
    while r["tokens"]["total"] > cfg.context_token_budget:
        if cand_ev_on and any(p.source != "linked" and p.evidence_shown for p in r["tasks"]):
            cand_ev_on = False
            trims.append("drop_candidate_evidence")
        elif K > 5:
            K -= 1
            trims.append(f"reduce_k {K}")
        elif n_cand > 1:
            n_cand -= 1
            trims.append(f"drop_candidate {cands[n_cand][0].task_id}")
        else:
            break
        r = build()

    # ---- ngữ cảnh rút gọn cho cổng: tiêu đề task + gate_window_k tin + TIN MỚI ----
    rev = r["rev_map"]

    def g_line(m: PromptMsg) -> str:
        if m.kind == "reaction":
            return f"{m.local} {m.author_name} {REACTION_EMOJI.get(m.reaction_type or '', '')} (reaction vào {rev.get(m.target_id or '', 'tin cũ')})"
        return f"{m.local} {m.author_name}: {m.text}"

    g_win = [m for m in r["history"] if m.section == "window"][-cfg.gate_window_k:] if cfg.gate_window_k > 0 else []
    titles = "\n".join(f"{p.local} [{p.task.status}] {p.task.title}" for p in r["tasks"]) or "(không có)"
    gate_user = (f"<VIEC_DANG_THEO_DOI>\n{titles}\n</VIEC_DANG_THEO_DOI>\n<LICH_SU_GAN>\n"
                 + ("\n".join(g_line(m) for m in g_win) or "(trống)")
                 + "\n</LICH_SU_GAN>\n<TIN_MOI>\n" + "\n".join(g_line(m) for m in r["new"]) + "\n</TIN_MOI>")
    b = r["blocks"]
    return BuiltContext(
        key=key, conv_type=conv_type, members=members, messages=r["all"], new_msgs=r["new"], history=r["history"],
        tasks=r["tasks"], id_map=r["id_map"], rev_map=rev, task_map=r["task_map"], blocks=b, system=b["b1"],
        user="\n".join(x for x in [r["head"], b["b2"], b["b3a"], b["b3b"], b["b4"], b["b5"]] if x),
        tokens=r["tokens"], trims=trims, window_k=K, window_ids=r["window_ids"], reply_ids=r["reply_ids"],
        evidence_ids=r["evidence_ids"], gate_system=gate_instructions, gate_user=gate_user, turn_at=turn_at,
        retrieval={"query": q, "people": q_people, "joined_reply": joined}, chain_ids=[c[0] for c in chain],
        cache_key=cfg.prompt_cache_key,
    )


def extend_context(state: EngineState, ctx: BuiltContext, hits: list[Any], cfg: Config, intro: str) -> BuiltContext:
    """Lần gọi 2 = CÙNG prompt lần 1 (để cache trúng) + khối <TASK_SEARCH_RESULTS source="target_check">.

    Task mới được đánh `T<n>` nối tiếp; tin bằng chứng chưa có trong prompt được đánh `#n` nối tiếp
    (không đánh số lại tin cũ để giữ nguyên tiền tố prompt). Task đã có trong prompt thì bỏ qua."""
    c = copy.copy(ctx)
    c.id_map, c.rev_map, c.task_map = dict(ctx.id_map), dict(ctx.rev_map), dict(ctx.task_map)
    c.messages, c.tasks, c.tokens = list(ctx.messages), list(ctx.tasks), dict(ctx.tokens)
    by_id = {e.id: e for e in state.events}
    at_of = {e.id: e.at for e in state.events if e.kind == "message"}
    in_prompt = set(ctx.task_map.values())
    lines = [intro]
    n_task, n_msg = len(c.task_map), len(c.id_map)
    for h in hits:
        t: Task = h.task
        if t.task_id in in_prompt:
            continue
        n_task += 1
        local = f"T{n_task}"
        c.task_map[local] = t.task_id
        pick = _pick_evidence(t, min(2, max(1, cfg.linked_evidence_per_task)), at_of)
        ev_lines: list[str] = []
        for i in pick:
            if i not in c.rev_map:
                n_msg += 1
                pm = _to_prompt_msg(by_id[i], "target_check")
                pm.local = f"#{n_msg}"
                c.id_map[pm.local] = i
                c.rev_map[i] = pm.local
                c.messages.append(pm)
                ev_lines.append("    " + _fmt_msg(pm, None, c.rev_map, cfg.message_max_tokens))
        head, pend = _task_head(state, t, local, ctx.members)
        lines.append(f"{head} — last mentioned: {fmt_day_time(t.last_mentioned_at or t.created_at)} — evidence: {_ev_refs(t, c.rev_map)}")
        lines += ev_lines
        why = f"điểm {h.score} · khớp: {', '.join(h.matched) or '—'}"
        c.tasks.append(PromptTask(local, t, "target_check", pend, list(pick), why))
    lines.append("(no other results)")
    block = '<TASK_SEARCH_RESULTS source="target_check">\n' + "\n".join(lines) + f"\n</TASK_SEARCH_RESULTS>\n{FINAL_CALL_NOTE}"
    c.extra_block = block
    c.user = ctx.user + "\n" + block
    c.tokens["b6"] = count_tokens(block)
    c.tokens["total"] = ctx.tokens["total"] + c.tokens["b6"]
    c.call = ctx.call + 1
    return c
