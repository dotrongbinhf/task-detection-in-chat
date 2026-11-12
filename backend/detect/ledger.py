"""Sổ cái: vòng đời task, hết hạn, user_actions, đề xuất thay đổi (mục 4.2)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from .config import Config
from .models import OPEN_STATUSES, EngineState, FieldHistory, PendingChange, Task, UserAction
from .timeexpr import format_due_date, local_date, ts

H = 3600.0
D = 24 * H


def log(state: EngineState, out: Optional[list[dict]], entry: dict[str, Any]) -> dict[str, Any]:
    state.trace.append(entry)
    if out is not None:
        out.append(entry)
    return entry


def pending_for(state: EngineState, task_id: str) -> list[PendingChange]:
    return [c for c in state.changes if c.task_id == task_id and c.status == "pending"]


def is_open(t: Task, state: EngineState) -> bool:
    """Tập đang mở = candidate + draft + approved + declined; task có đề xuất pending cũng giữ mở."""
    return t.status in OPEN_STATUSES or bool(pending_for(state, t.task_id))


def meets_draft_min(t: Task, cfg: Config) -> bool:
    if not t.assignee_ids:
        return False
    if cfg.draft_min == "assignee_due":
        return t.due_type != "none"
    return True


def set_status(state: EngineState, t: Task, status: str, at: str, reason: str, out: Optional[list[dict]] = None) -> None:
    if t.status == status:
        return
    log(state, out, {"type": "status_change", "at": at, "task_id": t.task_id, "from": t.status, "to": status, "reason": reason})
    t.status = status  # type: ignore[assignment]
    t.status_changed_at = at


def sync_approvals(t: Task) -> None:
    t.approvals = {u: t.approvals.get(u, "pending") for u in t.assignee_ids}


def display_due(due_type: str, due_date: Optional[str], due_text: Optional[str]) -> str:
    if due_type == "calendar":
        return format_due_date(due_date)
    if due_type == "event":
        return f"sự kiện: {due_text or '?'}"
    return "không hạn"


def task_due_display(t: Task) -> str:
    return display_due(t.due_type, t.due_date, t.due_text)


def apply_expiry(state: EngineState, now_iso: str, cfg: Config) -> list[dict]:
    """Áp luật hết hạn theo thời gian mô phỏng (bảng vòng đời mục 4.2)."""
    out: list[dict] = []
    now = ts(now_iso)
    for t in state.tasks:
        touched = ts(t.last_touched_at)
        if t.status == "candidate" and now - touched > cfg.candidate_ttl_hours * H:
            set_status(state, t, "expired", now_iso, f"candidate_ttl_hours={cfg.candidate_ttl_hours}", out)
        elif t.status == "draft" and now - touched > cfg.draft_ttl_days * D:
            set_status(state, t, "stale", now_iso, f"draft_ttl_days={cfg.draft_ttl_days}", out)
        elif t.status == "approved" and not pending_for(state, t.task_id):
            if t.due_type == "calendar" and t.due_date:
                if local_date(now_iso) > date.fromisoformat(t.due_date) + timedelta(days=cfg.approved_grace_days):
                    set_status(state, t, "closed", now_iso, f"quá hạn + approved_grace_days={cfg.approved_grace_days}", out)
            elif now - touched > cfg.undated_open_days * D:
                set_status(state, t, "closed", now_iso, f"undated_open_days={cfg.undated_open_days}", out)
        elif t.status == "declined" and now - ts(t.status_changed_at) > cfg.declined_suppress_days * D:
            set_status(state, t, "closed", now_iso, f"declined_suppress_days={cfg.declined_suppress_days}", out)
    for e in out:
        e["type"] = "expiry"
    return out


def value_of(t: Task, field: str) -> Any:
    if field == "assignee_refs":
        return list(t.assignee_ids)
    if field == "due":
        return {"due_type": t.due_type, "due_date": t.due_date, "due_text": t.due_text, "due_kind": t.due_kind}
    if field == "status":
        return t.status
    return getattr(t, field)


def apply_field_value(t: Task, field: str, value: Any) -> None:
    if field == "assignee_refs":
        t.assignee_ids = list(value["ids"])
        t.assignee_refs_raw = list(value["raw"])
    elif field == "due":
        for k in ("due_text", "due_kind", "due_day_offset", "due_weekday", "due_week_offset", "due_week_part",
                  "due_abs_day", "due_abs_month", "due_date"):
            setattr(t, k, value.get(k))
        t.due_type = value.get("due_type") or "none"
    elif field == "title":
        t.title = str(value)
    elif field == "notes":
        t.notes = None if value is None else str(value)
    elif field == "kind":
        t.kind = value


def propose_change(state: EngineState, t: Task, field: str, new_value: Any, new_display: str, old_display: str,
                   evidence_id: str, at: str, out: list[dict]) -> PendingChange:
    """Tạo đề xuất thay đổi cho task đã duyệt; mỗi trường tối đa một đề xuất pending (cũ → superseded)."""
    for c in state.changes:
        if c.task_id == t.task_id and c.field == field and c.status == "pending":
            c.status = "superseded"
            c.resolved_at = at
            log(state, out, {"type": "change_superseded", "at": at, "task_id": t.task_id, "change_id": c.change_id, "field": field})
    approvers = [u for u, v in t.approvals.items() if v == "approved"]
    notified: list[str] = []
    if field == "assignee_refs":
        ids = new_value["ids"]
        fresh = [u for u in ids if u not in t.assignee_ids]
        notified = [u for u in t.assignee_ids if u not in ids]
        approvers = fresh or list(ids)  # đổi người nhận do người nhận MỚI duyệt
    state.counters.change += 1
    c = PendingChange(
        change_id=f"C{state.counters.change}", task_id=t.task_id, field=field, old_value=value_of(t, field),
        new_value=new_value, new_display=new_display, old_display=old_display, evidence_message_id=evidence_id,
        created_at=at, approver_ids=approvers, notified_ids=notified,
    )
    state.changes.append(c)
    log(state, out, {"type": "change_proposed", "at": at, "task_id": t.task_id, "change_id": c.change_id, "field": field,
                     "old": old_display, "new": new_display, "approvers": approvers, "notified": notified, "evidence": evidence_id})
    if notified:
        log(state, out, {"type": "notified", "at": at, "task_id": t.task_id, "change_id": c.change_id, "user_ids": notified})
    return c


def apply_user_action(state: EngineState, a: UserAction, cfg: Config) -> list[dict]:
    """Áp một user_action. Người duyệt là người nhận; hành động của người khác → user_action_unauthorized."""
    out: list[dict] = []
    at = a.at
    base = {"at": at, "user_id": a.user_id, "action": a.action, "message_id": a.message_id, "source": a.source}

    if a.action in ("approve", "decline"):
        cands = [
            t for t in state.tasks
            if (t.status == "draft" or (t.status == "approved" and t.approvals.get(a.user_id) == "pending"))
            and (t.task_id == a.task_id if a.task_id else any(e.message_id == a.message_id for e in t.evidence))
        ]
        if not cands:
            log(state, out, {"type": "user_action_unmatched", **base})
            return out
        t = next((x for x in cands if a.user_id in x.assignee_ids), None)
        if t is None:
            log(state, out, {"type": "user_action_unauthorized", **base, "task_id": cands[0].task_id, "reason": "không phải người nhận"})
            return out
        t.approvals[a.user_id] = "approved" if a.action == "approve" else "declined"
        log(state, out, {"type": "user_action", **base, "task_id": t.task_id})
        vals = list(t.approvals.values())
        if "approved" in vals:
            set_status(state, t, "approved", at, "người nhận duyệt", out)
        elif vals and all(v == "declined" for v in vals):
            set_status(state, t, "declined", at, "mọi người nhận từ chối", out)
        t.last_touched_at = at
        return out

    c = next((x for x in state.changes if x.status == "pending"
              and (x.change_id == a.change_id if a.change_id else x.evidence_message_id == a.message_id)), None)
    if c is None:
        log(state, out, {"type": "user_action_unmatched", **base})
        return out
    if a.user_id not in c.approver_ids:
        log(state, out, {"type": "user_action_unauthorized", **base, "change_id": c.change_id, "reason": "không phải người duyệt đề xuất"})
        return out
    t = next(x for x in state.tasks if x.task_id == c.task_id)
    c.resolved_at = at
    if a.action == "approve_change":
        c.status = "approved"
        if c.field == "status":
            set_status(state, t, "cancelled", at, "đề xuất huỷ được duyệt", out)
            log(state, out, {"type": "draft_cancelled", "at": at, "task_id": t.task_id, "via": "change_approved"})
        else:
            t.field_history.append(FieldHistory(field=c.field, old=c.old_value, new=c.new_value,
                                                evidence_message_id=c.evidence_message_id, at=at, via="change_approved"))
            apply_field_value(t, c.field, c.new_value)
            if c.field == "assignee_refs":
                sync_approvals(t)
                t.approvals[a.user_id] = "approved"
        log(state, out, {"type": "change_approved", **base, "task_id": t.task_id, "change_id": c.change_id, "field": c.field})
    else:
        c.status = "declined"
        log(state, out, {"type": "change_declined", **base, "task_id": t.task_id, "change_id": c.change_id, "field": c.field})
    t.last_touched_at = at
    return out
