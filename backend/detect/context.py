"""Dựng ngữ cảnh (mục 4.3): chọn task liên quan, cửa sổ K tin, chuỗi reply, định dạng nén, ngân sách token."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .config import Config
from .ledger import pending_for, task_due_display
from .models import DB_STATUSES, OPEN_STATUSES, EngineState, Member, Message, PendingChange, StreamEvent, Task
from .text import keywords
from .timeexpr import calendar14, fmt_day_time, fmt_time, local_date, ts
from .tokens import count_tokens, truncate_tokens

REACTION_EMOJI = {"like": "👍", "love": "❤️", "heart": "❤️", "haha": "😆", "wow": "😮", "sad": "😢", "angry": "😠", "ok": "👌", "check": "✅"}


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
    section: str  # evidence | reply | window | new


@dataclass
class Scored:
    task: Task
    score: int
    reasons: list[str]
    source: str  # open | db
    local: str = ""

    def public(self, selected: bool) -> dict[str, Any]:
        return {"local": self.local if selected else "", "task_id": self.task.task_id, "title": self.task.title,
                "status": self.task.status, "score": self.score, "reasons": self.reasons, "source": self.source}


@dataclass
class PromptTask:
    local: str
    task: Task
    score: int
    reasons: list[str]
    source: str
    pending: list[PendingChange]
    evidence_shown: list[str]


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
    selected: list[dict]
    not_selected: list[dict]
    window_k: int
    window_ids: list[str]
    reply_ids: list[str]
    evidence_ids: list[str]
    gate_system: str
    gate_user: str
    turn_at: str
    extra: dict[str, Any] = field(default_factory=dict)


def _to_prompt_msg(e: StreamEvent, section: str) -> PromptMsg:
    if e.kind == "message":
        m = e.message
        assert m is not None
        text = m.text + ("".join(f" [file: {a}]" for a in m.attachments) if m.attachments else "")
        return PromptMsg("", m.id, "message", m.created_at, m.author_id, m.author_name, text,
                         [x.model_dump() for x in m.mentions], m.reply_to_id, None, None, section)
    return PromptMsg("", e.id, "reaction", e.at, e.user_id or "", e.user_name or "", "", [], None, e.target_id, e.type, section)


def score_task(t: Task, new_events: list[StreamEvent], window_ids: set[str], turn_at: str) -> tuple[int, list[str]]:
    """Bảng tín hiệu mục 4.3."""
    score, reasons = 0, []
    ev_ids = {e.message_id for e in t.evidence}
    if any((e.message.reply_to_id in ev_ids) if e.kind == "message" else (e.target_id in ev_ids) for e in new_events):
        score += 100
        reasons.append("+100 reply/reaction vào bằng chứng")
    if ev_ids & window_ids:
        score += 20
        reasons.append("+20 bằng chứng trong LỊCH SỬ GẦN")
    people: set[str] = set()
    for e in new_events:
        if e.kind == "message":
            people.add(e.message.author_id)
            people |= {m.user_id for m in e.message.mentions}
        else:
            people.add(e.user_id or "")
    involved = set(t.assignee_ids) | ({t.assigner_id} if t.assigner_id else set())
    if people & involved:
        score += 10
        reasons.append("+10 cùng người giao/nhận")
    new_text = " ".join(e.message.text for e in new_events if e.kind == "message")
    kw = sorted(keywords(new_text) & keywords(f"{t.title} {t.notes or ''}"))
    if kw:
        score += 3 * len(kw)
        reasons.append(f"+{3 * len(kw)} từ khoá: {', '.join(kw)}")
    if ts(turn_at) - ts(t.last_touched_at) <= 24 * 3600:
        score += 5
        reasons.append("+5 chạm trong 24h")
    return score, reasons


def build_context(state: EngineState, key: str, batch: list[StreamEvent], cfg: Config,
                  instructions: str, gate_instructions: str) -> BuiltContext:
    msgs = {e.id: e.message for e in state.events if e.kind == "message"}
    by_id = {e.id: e for e in state.events}
    processed = set(state.processed)
    batch_ids = {e.id for e in batch}
    turn_at, first_at = batch[-1].at, batch[0].at
    conv_id = key.split(";")[0]
    members = state.members.get(conv_id, [])
    conv_type = next((e.message.conversation_type for e in batch if e.kind == "message"), None) or next(
        (e.message.conversation_type for e in state.events if e.kind == "message" and e.key == key), "group")

    hist_events = [e for e in state.events if e.key == key and e.id in processed and e.id not in batch_ids and ts(e.at) <= ts(first_at)]
    hist_msgs = [e for e in hist_events if e.kind == "message"]

    # ---- chấm điểm & chọn task (theo cửa sổ gốc K) ----
    base_window = {e.id for e in hist_msgs[-cfg.window_k:]} if cfg.window_k > 0 else set()
    scored_open: list[Scored] = []
    scored_db: list[Scored] = []
    for t in state.tasks:
        if t.conversation_key != key:
            continue
        is_open = t.status in OPEN_STATUSES or bool(pending_for(state, t.task_id))
        s, reasons = score_task(t, batch, base_window, turn_at)
        if is_open:
            if t.status == "declined" and s < 20:
                scored_open.append(Scored(t, -1, reasons + ["declined cần ≥ 20 → loại"], "open"))
            else:
                scored_open.append(Scored(t, s, reasons, "open"))
        elif t.status in DB_STATUSES and (ts(turn_at) - ts(t.status_changed_at)) / 86400 <= cfg.closed_lookup_days:
            scored_db.append(Scored(t, s, reasons, "db"))
    order = lambda x: (-x.score, -ts(x.task.last_touched_at))  # noqa: E731
    scored_open.sort(key=order)
    scored_db.sort(key=order)
    sel_open = [s for s in scored_open if s.score >= 0][: cfg.open_tasks_max]
    sel_db = [s for s in scored_db if s.score >= 13][: cfg.closed_tasks_max]

    K = cfg.window_k
    evidence_on = {s.task.task_id for s in sel_open + sel_db}
    trims: list[str] = []

    def build() -> dict[str, Any]:
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

        tasks_sel = sel_open + sel_db
        shown = wset | set(reply_ids) | batch_ids
        ev_shown: dict[str, list[str]] = {}
        for s in tasks_sel:
            if s.task.task_id not in evidence_on or cfg.evidence_per_task <= 0:
                ev_shown[s.task.task_id] = []
                continue
            ids: list[str] = []
            for e in sorted(s.task.evidence, key=lambda x: -ts(x.added_at)):
                if e.message_id not in ids and e.message_id not in shown and e.message_id in msgs:
                    ids.append(e.message_id)
            ev_shown[s.task.task_id] = ids[: cfg.evidence_per_task]
        evidence_ids = list(dict.fromkeys(i for v in ev_shown.values() for i in v))

        all_msgs: list[PromptMsg] = [_to_prompt_msg(by_id[i], "evidence") for i in evidence_ids]
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

        def fmt_msg(m: PromptMsg, prev_at: Optional[str]) -> str:
            full = not prev_at or local_date(prev_at) != local_date(m.at) or ts(m.at) - ts(prev_at) > 3600
            stamp = fmt_day_time(m.at) if full else fmt_time(m.at)
            if m.kind == "reaction":
                tgt = rev_map.get(m.target_id or "", "tin ngoài ngữ cảnh")
                return f"{m.local} [{stamp}] {m.author_name} {REACTION_EMOJI.get(m.reaction_type or '', m.reaction_type)} (reaction vào {tgt})"
            line = f"{m.local} [{stamp}] {m.author_name}: {truncate_tokens(m.text, cfg.message_max_tokens)}"
            if m.mentions:
                line += " (" + ", ".join(f"@{x['name']}={x['user_id']}" for x in m.mentions) + ")"
            if m.reply_to_id:
                line += f" ↩{rev_map.get(m.reply_to_id, '(tin ngoài ngữ cảnh)')}"
            return line

        def fmt_list(lst: list[PromptMsg]) -> str:
            lines, prev = [], None
            for m in lst:
                lines.append(fmt_msg(m, prev))
                prev = m.at
            return "\n".join(lines)

        name_of = lambda u: next((m.display_name for m in members if m.user_id == u), u)  # noqa: E731
        task_map: dict[str, str] = {}
        prompt_tasks: list[PromptTask] = []
        lines: list[str] = []
        for i, s in enumerate(tasks_sel):
            local = f"T{i + 1}"
            s.local = local
            t = s.task
            task_map[local] = t.task_id
            pend = pending_for(state, t.task_id)
            who = ", ".join(name_of(u) for u in t.assignee_ids) or "(chưa có)"
            p_as = next((p for p in pend if p.field == "assignee_refs"), None)
            if p_as:
                who += f", ĐANG CHỜ DUYỆT: {p_as.new_display}"
            due = task_due_display(t)
            p_due = next((p for p in pend if p.field == "due"), None)
            if p_due:
                due += f", ĐANG CHỜ DUYỆT: {p_due.new_display}"
            ev_refs = [f"{rev_map[e.message_id]} {e.role}" for e in t.evidence if e.message_id in rev_map]
            hidden = sum(1 for e in t.evidence if e.message_id not in rev_map)
            ev_s = ", ".join(ev_refs + ([f"+{hidden} tin ngoài ngữ cảnh"] if hidden else [])) or "—"
            extra = " — ĐANG CHỜ DUYỆT: huỷ" if any(p.field == "status" for p in pend) else ""
            tag = " (task cũ)" if s.source == "db" else ""
            lines.append(f'{local} [{t.status} | {t.kind}]{tag} "{t.title}" — người nhận: {who} — hạn: {due}{extra} — bằng chứng: {ev_s}')
            for mid in ev_shown.get(t.task_id, []):
                pm = next(m for m in all_msgs if m.id == mid)
                lines.append("    " + fmt_msg(pm, None))
            prompt_tasks.append(PromptTask(local, t, s.score, s.reasons, s.source, pend, ev_shown.get(t.task_id, [])))

        history = [m for m in all_msgs if m.section in ("window", "reply")]
        new_msgs = [m for m in all_msgs if m.section == "new"]
        head = f"Loại hội thoại: {conv_type}."
        b = {
            "b1": instructions,
            "b2": f"<LICH>\n{calendar14(turn_at)}\n</LICH>",
            "b3": "<TASK_LIEN_QUAN>\n" + ("\n".join(lines) or "(không có)") + "\n</TASK_LIEN_QUAN>",
            "b4": "<LICH_SU_GAN>\n" + (fmt_list(history) or "(trống)") + "\n</LICH_SU_GAN>",
            "b5": f"<TIN_MOI>\n{fmt_list(new_msgs)}\n</TIN_MOI>",
        }
        tok = {"b1": count_tokens(b["b1"]), "b2": count_tokens(b["b2"]), "b3": count_tokens(b["b3"]),
               "b4": count_tokens(b["b4"]), "b5": count_tokens(head + "\n" + b["b5"])}
        tok["total"] = sum(tok.values())
        return dict(all=all_msgs, new=new_msgs, history=history, id_map=id_map, rev_map=rev_map, task_map=task_map,
                    tasks=prompt_tasks, blocks=b, tokens=tok, head=head, window_ids=window_ids, reply_ids=reply_ids,
                    evidence_ids=evidence_ids)

    r = build()
    # ---- ngân sách token: task cũ DB → bằng chứng kèm task → task điểm thấp (giữ ≥ 3) → giảm K (≥ 5) ----
    for _ in range(200):
        if r["tokens"]["total"] <= cfg.context_token_budget:
            break
        with_ev = [p for p in reversed(r["tasks"]) if p.task.task_id in evidence_on and p.evidence_shown]
        if sel_db:
            trims.append(f"drop_db_task {sel_db.pop().task.task_id}")
        elif with_ev:
            evidence_on.discard(with_ev[0].task.task_id)
            trims.append(f"drop_evidence {with_ev[0].task.task_id}")
        elif len(sel_open) > 3:
            trims.append(f"drop_open_task {sel_open.pop().task.task_id}")
        elif K > 5:
            K -= 1
            trims.append(f"reduce_k {K}")
        else:
            break
        r = build()

    sel_ids = {s.task.task_id for s in sel_open + sel_db}
    selected = [s.public(True) for s in sel_open + sel_db]
    not_selected = [s.public(False) for s in scored_open + scored_db if s.task.task_id not in sel_ids]

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
                 + f"\n</LICH_SU_GAN>\n<TIN_MOI>\n" + "\n".join(g_line(m) for m in r["new"]) + "\n</TIN_MOI>")
    b = r["blocks"]
    user = "\n".join([r["head"], b["b2"], b["b3"], b["b4"], b["b5"]])
    return BuiltContext(
        key=key, conv_type=conv_type, members=members, messages=r["all"], new_msgs=r["new"], history=r["history"],
        tasks=r["tasks"], id_map=r["id_map"], rev_map=rev, task_map=r["task_map"], blocks=b, system=b["b1"], user=user,
        tokens=r["tokens"], trims=trims, selected=selected, not_selected=not_selected, window_k=K,
        window_ids=r["window_ids"], reply_ids=r["reply_ids"], evidence_ids=r["evidence_ids"],
        gate_system=gate_instructions, gate_user=gate_user, turn_at=turn_at,
    )
