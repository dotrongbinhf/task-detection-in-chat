"""Gọi bộ trích, parse, Validate, gán người nhận, quy hạn, áp op vào sổ cái (mục 4.5, 4.6)."""
from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import ValidationError

from .config import PROMPT_VERSION, Config
from .context import BuiltContext
from .ledger import (apply_field_value, display_due, log, meets_draft_min, pending_for, propose_change, set_status,
                     sync_approvals, task_due_display)
from .models import DUE_FIELDS, EngineState, Evidence, ExtractOutput, FieldHistory, Task
from .text import HONORIFICS, norm
from .timeexpr import DATE_FIELDS, resolve_due, ts


def parse_extract(raw: str) -> tuple[Optional[ExtractOutput], Optional[str]]:
    try:
        return ExtractOutput.model_validate(json.loads(raw)), None
    except (json.JSONDecodeError, ValidationError) as e:
        return None, str(e)[:500]


def resolve_assignees(refs: list[str], ctx: BuiltContext, anchor_id: Optional[str]) -> tuple[list[str], list[dict]]:
    """mention user_id | SPEAKER:#n | ADDRESSEE (chỉ DM) | tên (bỏ dấu, bỏ xưng hô, khớp đúng một người)."""
    details: list[dict] = []
    member_ids = {m.user_id for m in ctx.members}
    mentioned = {x["user_id"] for m in ctx.messages for x in m.mentions}
    by_local = {m.local: m for m in ctx.messages}
    for ref in refs:
        r = ref.strip()
        if r.startswith("SPEAKER:"):
            m = by_local.get(r[8:].strip())
            details.append({"ref": r, "id": m.author_id, "how": f"người gửi {m.local} ({m.author_name})"} if m
                           else {"ref": r, "id": None, "how": "id tin không có trong prompt"})
            continue
        if r == "ADDRESSEE":
            if ctx.conv_type != "dm":
                details.append({"ref": r, "id": None, "how": "ADDRESSEE chỉ hợp lệ trong DM"})
                continue
            speaker = next((m.author_id for m in ctx.messages if m.id == anchor_id), None) or (ctx.new_msgs[0].author_id if ctx.new_msgs else None)
            other = [m for m in ctx.members if m.user_id != speaker]
            details.append({"ref": r, "id": other[0].user_id, "how": f"người còn lại trong DM ({other[0].display_name})"} if len(other) == 1
                           else {"ref": r, "id": None, "how": "DM không xác định người còn lại"})
            continue
        if r in mentioned or r in member_ids:
            details.append({"ref": r, "id": r, "how": "user_id trong mentions" if r in mentioned else "user_id thành viên (không có trong mentions)"})
            continue
        key = " ".join(t for t in norm(r).lstrip("@").split(" ") if t and t not in HONORIFICS)
        hits = [m for m in ctx.members if any(norm(n) == key for n in [m.display_name, *m.aliases])]
        if len(hits) == 1:
            details.append({"ref": r, "id": hits[0].user_id, "how": f'khớp tên "{hits[0].display_name}"'})
        else:
            details.append({"ref": r, "id": None, "how": f"tên trùng {len(hits)} người" if hits else "không khớp thành viên nào"})
    ids = list(dict.fromkeys(d["id"] for d in details if d["id"]))
    return ids, details


def _due_fields(o: dict[str, Any]) -> dict[str, Any]:
    d = {k: o.get(k) for k in DUE_FIELDS}
    d["due_kind"] = d["due_kind"] or "none"
    return d


def _due_cmp(due_type: Any, due_date: Any, due_text: Any) -> str:
    return f"{due_type}|{due_date}|{due_text if due_type == 'event' else ''}"


def _role_for(field: str) -> str:
    return {"due": "due", "assignee_refs": "assignee"}.get(field, "content")


def _add_evidence(t: Task, items: list[tuple[str, str]], at: str) -> None:
    for mid, role in items:
        if not any(e.message_id == mid and e.role == role for e in t.evidence):
            t.evidence.append(Evidence(message_id=mid, role=role, added_at=at))


def _show_draft(state: EngineState, t: Task, at: str, turn: int, events: list[dict]) -> None:
    set_status(state, t, "draft", at, "đủ draft_min")
    t.shown_at = t.shown_at or at
    sync_approvals(t)
    log(state, events, {"type": "draft_shown", "at": at, "turn": turn, "task_id": t.task_id, "to": t.assignee_ids, "title": t.title})


def validate_and_apply(state: EngineState, ctx: BuiltContext, out: ExtractOutput, cfg: Config, turn: int, model: str) -> tuple[list[dict], list[dict]]:
    at = ctx.turn_at
    results: list[dict] = []
    events: list[dict] = []
    new_ids = {m.id for m in ctx.new_msgs}
    msg_at = lambda i: next((m.at for m in ctx.messages if m.id == i), at)  # noqa: E731
    name_of = lambda u: next((m.display_name for m in ctx.members if m.user_id == u), u)  # noqa: E731
    created_now: set[str] = set()

    def resolve(local: Optional[str]) -> Optional[str]:
        return ctx.id_map.get(local.strip()) if local else None

    for op in out.ops:
        res: dict[str, Any] = {"op": op.op, "target": op.target, "task_id": None, "accepted": False, "notes": [],
                               "raw": op.model_dump(), "reason": None, "effect": None}

        def reject(reason: str) -> None:
            res["reason"] = reason
            log(state, events, {"type": "op_rejected", "at": at, "turn": turn, "op": op.op, "target": op.target, "reason": reason})
            results.append(res)

        # 1. id hợp lệ
        ev_ids: list[tuple[str, str]] = []
        bad = None
        for e in op.evidence:
            i = resolve(e.msg)
            if not i:
                bad = f"unknown_message_id {e.msg}"
            else:
                ev_ids.append((i, e.role))
        if bad:
            reject(bad)
            continue

        if op.op == "CREATE":
            anchor_id = resolve(op.anchor)
            if not anchor_id:
                reject(f"unknown_message_id anchor {op.anchor}")
                continue
            # 2. op phải trích ít nhất một tin trong TIN MỚI
            if anchor_id not in new_ids and not any(i in new_ids for i, _ in ev_ids):
                reject("not_from_new_message")
                continue
            # 3. chống tạo lại: anchor đã là anchor của task tạo ở lượt trước (kể cả declined)
            dup = next((t for t in state.tasks if t.anchor_message_id == anchor_id and t.task_id not in created_now), None)
            if dup:
                reject(f"duplicate_anchor (đã là anchor của {dup.task_id} [{dup.status}])")
                continue
            ids, details = resolve_assignees(op.assignee_refs or [], ctx, anchor_id)
            for d in details:
                res["notes"].append(f"assignee: {d['ref']} → {name_of(d['id'])} ({d['how']})" if d["id"]
                                    else f"assignee_unresolved: {d['ref']} — {d['how']}")
            df = _due_fields(op.model_dump())
            due_ref = next((i for i, r in ev_ids if r == "due"), anchor_id)
            due = resolve_due(df, msg_at(due_ref))
            res["notes"] += due.notes + [f"due: {df['due_kind']}{' “' + df['due_text'] + '”' if df['due_text'] else ''} → {due.due_date or due.due_type} ({due.explain})"]
            if "fabricated_date_blocked" in due.notes:
                for k in DATE_FIELDS:
                    df[k] = None
                log(state, events, {"type": "fabricated_date_blocked", "at": at, "turn": turn})
            anchor_msg = next((m for m in ctx.messages if m.id == anchor_id), None)
            kind = op.kind or ("assigned" if ids else "open_ask")
            assigner = anchor_msg.author_id if kind != "self_commit" and anchor_msg and anchor_msg.author_id not in ids else None
            ev_list = ev_ids if any(i == anchor_id for i, _ in ev_ids) else [(anchor_id, "assign"), *ev_ids]
            state.counters.task += 1
            t = Task(
                **df, task_id=f"K{state.counters.task}", conversation_key=ctx.key, status="candidate", kind=kind,
                title=(op.title or "").strip() or "(chưa có tiêu đề)", notes=op.notes, assignee_refs_raw=op.assignee_refs or [],
                assignee_ids=ids, assigner_id=assigner, due_type=due.due_type, due_date=due.due_date, anchor_message_id=anchor_id,
                created_at=at, created_turn=turn, last_touched_at=at, status_changed_at=at, prompt_version=PROMPT_VERSION, model=model,
            )
            _add_evidence(t, ev_list, at)
            state.tasks.append(t)
            created_now.add(t.task_id)
            res.update(task_id=t.task_id, accepted=True)
            log(state, events, {"type": "task_created", "at": at, "turn": turn, "task_id": t.task_id, "title": t.title, "kind": t.kind, "anchor": anchor_id})
            if meets_draft_min(t, cfg):
                _show_draft(state, t, at, turn, events)
                res["effect"] = f"Tạo {t.task_id} → nháp hiện cho {', '.join(map(name_of, t.assignee_ids))}"
            else:
                res["effect"] = f"Tạo {t.task_id} → giữ ẩn (candidate): {'chưa có hạn' if t.assignee_ids else 'chưa xác định người nhận'}"
            log(state, events, {"type": "op_applied", "at": at, "turn": turn, "op": "CREATE", "task_id": t.task_id})
            results.append(res)
            continue

        # 4. UPDATE / CANCEL phải nhắm vào task trong khối TASK LIÊN QUAN
        task_id = ctx.task_map.get((op.target or "").strip())
        if not task_id:
            reject(f"unknown_task {op.target} (không có trong TASK LIÊN QUAN)")
            continue
        t = next(x for x in state.tasks if x.task_id == task_id)
        res["task_id"] = t.task_id
        if t.status in ("declined", "expired", "stale", "closed", "cancelled"):
            reject(f"target_not_open ({t.task_id} đang {t.status})")
            continue

        if op.op == "CANCEL":
            ev_new = next((i for i, _ in ev_ids if i in new_ids), None)
            if not ev_new:
                reject("not_from_new_message")
                continue
            _add_evidence(t, ev_ids, at)
            t.last_touched_at = at
            if t.status == "approved":
                if any(p.field == "status" for p in pending_for(state, t.task_id)):
                    reject("no_op_change (đã có đề xuất huỷ đang chờ)")
                    continue
                propose_change(state, t, "status", "cancelled", "huỷ task", t.status, ev_new, at, events)
                res["effect"] = f"Đề xuất HUỶ {t.task_id} → chờ người nhận duyệt"
            else:
                was_draft = t.status == "draft"
                set_status(state, t, "cancelled", at, "CANCEL", events)
                if was_draft:
                    log(state, events, {"type": "draft_cancelled", "at": at, "turn": turn, "task_id": t.task_id})
                res["effect"] = f"Huỷ {t.task_id}" + (" (báo đã huỷ cho người nhận)" if was_draft else " (task ẩn)")
            res["accepted"] = True
            log(state, events, {"type": "op_applied", "at": at, "turn": turn, "op": "CANCEL", "task_id": t.task_id})
            results.append(res)
            continue

        # ---- UPDATE ----
        parsed: list[dict] = []
        err = None
        for c in op.changes or []:
            ev = resolve(c.evidence_msg)
            if not ev:
                err = "missing_evidence (change thiếu evidence_msg hợp lệ)"
                break
            try:
                v = json.loads(c.value)
            except json.JSONDecodeError:
                err = f"bad_change_value {c.field}: {c.value}"
                break
            if c.field == "assignee_refs":
                refs = [str(x) for x in v] if isinstance(v, list) else [str(v)]
                ids, details = resolve_assignees(refs, ctx, None)
                for d in details:
                    res["notes"].append(f"assignee: {d['ref']} → {name_of(d['id'])} ({d['how']})" if d["id"]
                                        else f"assignee_unresolved: {d['ref']} — {d['how']}")
                if ids:
                    parsed.append({"field": "assignee_refs", "value": {"ids": ids, "raw": refs},
                                   "display": ", ".join(map(name_of, ids)), "ev": ev, "cmp": ",".join(sorted(ids))})
            elif c.field == "due":
                df = _due_fields(v if isinstance(v, dict) else {})
                due = resolve_due(df, msg_at(ev))
                res["notes"] += due.notes + [f"due: {df['due_kind']}{' “' + df['due_text'] + '”' if df['due_text'] else ''} → {due.due_date or due.due_type} ({due.explain})"]
                if "fabricated_date_blocked" in due.notes:
                    for k in DATE_FIELDS:
                        df[k] = None
                nv = {**df, "due_type": due.due_type, "due_date": due.due_date}
                parsed.append({"field": "due", "value": nv, "display": display_due(due.due_type, due.due_date, df["due_text"]),
                               "ev": ev, "cmp": _due_cmp(due.due_type, due.due_date, df["due_text"])})
            else:
                s = None if v is None else str(v)
                parsed.append({"field": c.field, "value": s, "display": str(s), "ev": ev, "cmp": str(s)})
        if err:
            reject(err)
            continue
        if not any(p["ev"] in new_ids for p in parsed) and not any(i in new_ids for i, _ in ev_ids):
            reject("not_from_new_message")
            continue

        def cur(f: str) -> str:
            if f == "assignee_refs":
                return ",".join(sorted(t.assignee_ids))
            if f == "due":
                return _due_cmp(t.due_type, t.due_date, t.due_text)
            return str(getattr(t, f))

        def cmp_of(c: Any) -> str:
            if c.field == "assignee_refs":
                return ",".join(sorted(c.new_value["ids"]))
            if c.field == "due":
                return _due_cmp(c.new_value.get("due_type"), c.new_value.get("due_date"), c.new_value.get("due_text"))
            return str(c.new_value)

        applied: list[str] = []
        dropped: list[str] = []
        ev_msg_ids = {i for i, _ in ev_ids}
        for p in parsed:
            pend = next((c for c in pending_for(state, t.task_id) if c.field == p["field"]), None)
            if p["cmp"] == cur(p["field"]) or (pend and cmp_of(pend) == p["cmp"]):
                dropped.append(f"{p['field']}: no_op_change")
                log(state, events, {"type": "change_rejected", "at": at, "turn": turn, "task_id": t.task_id, "field": p["field"], "reason": "no_op_change"})
                continue
            if t.status == "approved":
                declined = next((c for c in state.changes if c.task_id == t.task_id and c.field == p["field"] and c.status == "declined"
                                 and ts(at) - ts(c.resolved_at or c.created_at) <= cfg.declined_suppress_days * 86400
                                 and cmp_of(c) == p["cmp"]), None)
                if declined:
                    dropped.append(f"{p['field']}: declined_change_suppressed")
                    log(state, events, {"type": "change_rejected", "at": at, "turn": turn, "task_id": t.task_id, "field": p["field"], "reason": "declined_change_suppressed"})
                    continue
                old = (task_due_display(t) if p["field"] == "due"
                       else (", ".join(map(name_of, t.assignee_ids)) or "(chưa có)") if p["field"] == "assignee_refs"
                       else str(getattr(t, p["field"]) or ""))
                propose_change(state, t, p["field"], p["value"], p["display"], old, p["ev"], at, events)
                applied.append(f"đề xuất {p['field']} → {p['display']}")
            else:
                t.field_history.append(FieldHistory(field=p["field"], old=cur(p["field"]), new=p["display"], evidence_message_id=p["ev"], at=at, via="draft_update"))
                apply_field_value(t, p["field"], p["value"])
                applied.append(f"{p['field']} → {p['display']}")
            if p["ev"] not in ev_msg_ids:
                _add_evidence(t, [(p["ev"], _role_for(p["field"]))], at)
        _add_evidence(t, ev_ids, at)
        t.last_touched_at = at

        if not applied and not ev_ids:
            reason = "; ".join(dropped) or "không có thay đổi"
            res["reason"] = reason
            log(state, events, {"type": "op_rejected", "at": at, "turn": turn, "op": "UPDATE", "target": op.target, "reason": reason})
            results.append(res)
            continue
        res["notes"] += dropped
        if t.status in ("candidate", "draft"):
            if any(a.startswith("assignee_refs") for a in applied):
                sync_approvals(t)
                if t.kind == "open_ask":
                    t.kind = "assigned"
                    res["notes"].append("kind: open_ask → assigned (đã có người nhận)")
            if t.status == "candidate" and meets_draft_min(t, cfg):
                _show_draft(state, t, at, turn, events)
                res["effect"] = f"{t.task_id}: candidate → nháp hiện cho {', '.join(map(name_of, t.assignee_ids))}"
            elif t.status == "draft" and applied:
                t.draft_update_count += 1
                log(state, events, {"type": "draft_updated", "at": at, "turn": turn, "task_id": t.task_id, "changes": applied})
                res["effect"] = f"Cập nhật nháp {t.task_id}: {'; '.join(applied)}"
            else:
                res["effect"] = f"Cập nhật task ẩn {t.task_id}: {'; '.join(applied)}" if applied else f"Bổ sung bằng chứng cho {t.task_id}"
        else:
            res["effect"] = f"{t.task_id} đã duyệt → {'; '.join(applied)} (chờ người nhận duyệt)" if applied else f"Bổ sung bằng chứng cho {t.task_id}"
        res["accepted"] = True
        log(state, events, {"type": "op_applied", "at": at, "turn": turn, "op": "UPDATE", "task_id": t.task_id, "changes": applied})
        results.append(res)
    return results, events
