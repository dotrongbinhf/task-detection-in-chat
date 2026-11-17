"""Parse, Validate, gán người nhận, quy hạn, áp op vào sổ cái.

v6: người nhận là `user_id` (mục 5); UPDATE dùng trường phẳng như CREATE, danh sách dùng `_add` / `_remove` (mục 7).
Code giữ phần so sánh, gộp, kiểm tra; không còn chốt chặn đoán vai trò người nói (#57, #58, #71b, #72a/b).
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any, Callable, Optional

from pydantic import ValidationError

from .config import PROMPT_VERSION, Config
from .context import BuiltContext
from .ledger import (apply_field_value, display_due, log, meets_draft_min, pending_for, propose_change, set_status,
                     sync_approvals, task_due_display)
from .models import DUE_FIELDS, EngineState, Evidence, ExtractOutput, FieldHistory, Op, Task
from .text import HONORIFICS, norm
from .timeexpr import DATE_FIELDS, local_date, resolve_due, ts


def parse_extract(raw: str) -> tuple[Optional[ExtractOutput], Optional[str]]:
    # model ngoài OpenAI ở chế độ json_object đôi khi bọc JSON trong ```json … ```
    raw = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw)
    try:
        return ExtractOutput.model_validate(json.loads(raw)), None
    except (json.JSONDecodeError, ValidationError) as e:
        return None, str(e)[:500]


def resolve_user_ids(refs: list[str], ctx: BuiltContext) -> tuple[list[str], list[dict]]:
    """user_id thành viên hội thoại → giữ. Không phải thành viên → khớp tên (bỏ dấu, bỏ xưng hô, đúng một người);
    không khớp → bỏ id đó, ghi `assignee_unknown` (v6 mục 5.3)."""
    details: list[dict] = []
    member_ids = {m.user_id for m in ctx.members}
    for ref in refs:
        r = str(ref).strip().lstrip("@")
        if r in member_ids:
            details.append({"ref": ref, "id": r, "how": "user_id thành viên"})
            continue
        key = " ".join(t for t in norm(r).split(" ") if t and t not in HONORIFICS)
        hits = [m for m in ctx.members if any(norm(n) == key for n in [m.display_name, *m.aliases])]
        if len(hits) == 1:
            details.append({"ref": ref, "id": hits[0].user_id, "how": f'khớp tên "{hits[0].display_name}"'})
        else:
            details.append({"ref": ref, "id": None,
                            "how": f"assignee_unknown: tên trùng {len(hits)} người" if hits else "assignee_unknown: không khớp thành viên nào"})
    return list(dict.fromkeys(d["id"] for d in details if d["id"])), details


_COPIED_SUFFIX = re.compile(r"(\s*\(@[^()]*=[^()]*\))?(\s*↩\S*)?(\s*\[[^\[\]]*\])?\s*$")


def resolve_msg(local: Optional[str], ctx: BuiltContext) -> Optional[str]:
    """'#12' → message_id. Chấp nhận hai kiểu model chép nhầm (DECISIONS #37, #71):
    - cả dòng tin '#12 [thứ 2 …] Linh (u_linh): …' → lấy id ở đầu;
    - chỉ nội dung tin '@Đạt viết script backup…' (không có #n) → tin có nội dung đúng như vậy trong prompt; nhiều tin
      trùng nội dung thì ưu tiên TIN MỚI, vẫn còn nhiều → không đoán."""
    if not local:
        return None
    hit = ctx.id_map.get(local.strip())
    if hit is None and (m := re.match(r"\s*(#\d+)(?!\d)", local)):
        hit = ctx.id_map.get(m.group(1))
    if hit is not None or local.lstrip().startswith("#"):
        return hit
    text = _COPIED_SUFFIX.sub("", local).strip()
    if not text:
        return None
    same = [m for m in ctx.messages if m.kind == "message" and m.text.strip() == text]
    if not same and len(text) >= 15:  # tin dài bị cắt trong prompt ("…[cắt]")
        same = [m for m in ctx.messages if m.kind == "message" and m.text.startswith(text.removesuffix("…[cắt]"))]
    new = {m.id for m in ctx.new_msgs}
    pick = [m for m in same if m.id in new] or same
    return pick[0].id if len({m.id for m in pick}) == 1 else None


def _due_fields(o: dict[str, Any]) -> dict[str, Any]:
    """Trường hạn của model → dạng nội bộ. Model ghi `due_weekday` đúng số trong chữ ("thứ 5" → 5, chủ nhật → 8);
    nội bộ dùng thứ ISO (thứ 2 = 1 … chủ nhật = 7). Quy ước "N − 1" cũ làm model nhỏ hay lệch một ngày (DECISIONS #72)."""
    d = {k: o.get(k) for k in DUE_FIELDS}
    d["due_kind"] = d["due_kind"] or "none"
    if isinstance(d["due_weekday"], int):
        d["due_weekday"] = d["due_weekday"] - 1 if 2 <= d["due_weekday"] <= 8 else None
    return d


def _due_cmp(due_type: Any, due_date: Any, due_text: Any) -> str:
    return f"{due_type}|{due_date}|{due_text if due_type == 'event' else ''}"


def _role_for(field: str) -> str:
    return {"due": "due", "assignee_refs": "assignee"}.get(field, "content")


# vai trò bằng chứng khớp với một trường (để chọn tin bằng chứng của từng thay đổi, v6 mục 7.2)
FIELD_ROLES = {"due": {"due"}, "assignee_refs": {"accept", "assignee"}, "title": {"content"}, "notes": {"content"},
               "kind": {"content", "accept", "assignee"}}


def _add_evidence(t: Task, items: list[tuple[str, str]], at: str, when: Callable[[str], str]) -> None:
    """Thêm bằng chứng; last_mentioned_at = created_at của tin bằng chứng mới nhất (không phải giờ xử lý)."""
    for mid, role in items:
        if not any(e.message_id == mid and e.role == role for e in t.evidence):
            t.evidence.append(Evidence(message_id=mid, role=role, added_at=at))
        m_at = when(mid)
        if not t.last_mentioned_at or ts(m_at) > ts(t.last_mentioned_at):
            t.last_mentioned_at = m_at


def _show_draft(state: EngineState, t: Task, at: str, turn: int, events: list[dict]) -> None:
    set_status(state, t, "draft", at, "đủ draft_min")
    t.shown_at = t.shown_at or at
    sync_approvals(t)
    log(state, events, {"type": "draft_shown", "at": at, "turn": turn, "task_id": t.task_id, "to": t.assignee_ids, "title": t.title})


def _resolve_due_change(op: Op, t: Task, ref_at: str, res: dict) -> Optional[dict[str, Any]]:
    """Trường hạn của UPDATE → giá trị hạn mới, hoặc None (không đổi). `due_kind` null = không đổi; "none" = bỏ hạn;
    `due_shift_days` = cộng vào hạn lịch hiện tại (v6 mục 7.1–7.2)."""
    o = op.model_dump()
    if op.due_shift_days is not None and op.due_kind is None:
        if t.due_type != "calendar" or not t.due_date:
            res["notes"].append("due_shift_no_base: task không có hạn lịch để lùi")
            return None
        nd = (date.fromisoformat(t.due_date) + timedelta(days=op.due_shift_days)).isoformat()
        res["notes"].append(f"due: {t.due_date} + {op.due_shift_days} ngày → {nd}")
        return {**{k: None for k in DUE_FIELDS}, "due_text": op.due_text or t.due_text, "due_kind": "absolute",
                "due_abs_day": date.fromisoformat(nd).day, "due_abs_month": date.fromisoformat(nd).month,
                "due_type": "calendar", "due_date": nd}
    if op.due_kind is None:
        return None
    df = _due_fields(o)
    due = resolve_due(df, ref_at)
    if (df["due_kind"] == "weekday" and due.due_date and date.fromisoformat(due.due_date) <= local_date(ref_at)):
        # đổi hạn về hôm nay / ngày đã qua ("lùi sang thứ 2" nói vào thứ 2) → lần tới của thứ đó (DECISIONS #72)
        nd = (date.fromisoformat(due.due_date) + timedelta(days=7)).isoformat()
        due.notes.append(f"due_rolled_forward: {due.due_date} → {nd}")
        due.due_date = nd
    res["notes"] += due.notes + [f"due: {df['due_kind']}{' “' + df['due_text'] + '”' if df['due_text'] else ''} → {due.due_date or due.due_type} ({due.explain})"]
    if "fabricated_date_blocked" in due.notes:
        for k in DATE_FIELDS:
            df[k] = None
    return {**df, "due_type": due.due_type, "due_date": due.due_date}


def validate_and_apply(state: EngineState, ctx: BuiltContext, out: ExtractOutput, cfg: Config, turn: int,
                       model: str) -> tuple[list[dict], list[dict]]:
    at = ctx.turn_at
    results: list[dict] = []
    events: list[dict] = []
    new_ids = {m.id for m in ctx.new_msgs}
    msg_at = lambda i: next((m.at for m in ctx.messages if m.id == i), at)  # noqa: E731
    name_of = lambda u: next((m.display_name for m in ctx.members if m.user_id == u), u)  # noqa: E731
    created_now: set[str] = set()

    def resolve(local: Optional[str]) -> Optional[str]:
        return resolve_msg(local, ctx)

    def people(refs: Optional[list[str]], res: dict, label: str) -> list[str]:
        ids, details = resolve_user_ids(refs or [], ctx)
        for d in details:
            res["notes"].append(f"{label}: {d['ref']} → {name_of(d['id'])} ({d['how']})" if d["id"] else f"{d['how']} ({label}: {d['ref']})")
        return ids

    for op in out.ops:
        res: dict[str, Any] = {"op": op.op, "target": op.target, "task_id": None, "accepted": False, "notes": [],
                               "raw": op.model_dump(), "reason": None, "effect": None, "fields": []}

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
            ids = people(op.assignees, res, "assignee")
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
            notes = op.notes or op.notes_add
            state.counters.task += 1
            conv = ctx.key.split(";")[0]
            n_conv = state.counters.task_by_conv[conv] = state.counters.task_by_conv.get(conv, 0) + 1
            t = Task(
                **df, task_id=f"K{state.convIndex.get(conv, 0)}.{n_conv}", conversation_key=ctx.key, status="candidate", kind=kind,
                title=(op.title or "").strip() or "(chưa có tiêu đề)", notes=notes, assignee_refs_raw=list(op.assignees or []),
                assignee_ids=ids, assigner_id=assigner, due_type=due.due_type, due_date=due.due_date, anchor_message_id=anchor_id,
                created_at=at, created_turn=turn, last_touched_at=at, status_changed_at=at, prompt_version=PROMPT_VERSION, model=model,
            )
            _add_evidence(t, ev_list, at, msg_at)
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

        # 4. UPDATE / CANCEL phải nhắm vào task có trong prompt
        task_id = ctx.task_map.get((op.target or "").strip())
        if not task_id:
            reject(f"unknown_task {op.target} (không có trong prompt)")
            continue
        t = next(x for x in state.tasks if x.task_id == task_id)
        res["task_id"] = t.task_id
        if t.status in ("declined", "closed", "cancelled", "expired", "stale"):
            reject(f"target_not_open ({t.task_id} đang {t.status})")
            continue

        if op.op == "CANCEL":
            ev_new = next((i for i, _ in ev_ids if i in new_ids), None)
            if not ev_new:
                reject("not_from_new_message")
                continue
            _add_evidence(t, ev_ids, at, msg_at)
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

        # ---- UPDATE: mỗi trường khác null thành một thay đổi ----
        if not any(i in new_ids for i, _ in ev_ids):
            reject("not_from_new_message")
            continue
        ev_new = [(i, r) for i, r in ev_ids if i in new_ids]

        def ev_for(field: str) -> str:
            """Bằng chứng của một thay đổi: tin đầu tiên thuộc TIN MỚI có vai trò khớp, không có thì tin đầu tiên thuộc TIN MỚI."""
            return next((i for i, r in ev_new if r in FIELD_ROLES[field]), ev_new[0][0])

        pend_of = lambda f: next((c for c in pending_for(state, t.task_id) if c.field == f), None)  # noqa: E731
        parsed: list[dict] = []
        if op.title is not None:
            parsed.append({"field": "title", "value": op.title.strip(), "display": op.title.strip(), "cmp": op.title.strip()})
        if op.notes is not None or op.notes_add is not None:
            p_notes = pend_of("notes")
            base = (p_notes.new_value if p_notes else t.notes) or ""
            new = op.notes.strip() if op.notes is not None else base
            if op.notes_add:
                new = f"{new}; {op.notes_add.strip()}" if new else op.notes_add.strip()
            parsed.append({"field": "notes", "value": new, "display": new, "cmp": new})
        if op.kind is not None:
            parsed.append({"field": "kind", "value": op.kind, "display": op.kind, "cmp": op.kind})
        if op.assignees_add or op.assignees_remove or op.assignees:
            if op.assignees:
                res["notes"].append("assignees_ignored: UPDATE dùng assignees_add / assignees_remove")
            add = people(op.assignees_add, res, "assignee_add")
            rem = people(op.assignees_remove, res, "assignee_remove")
            p_as = pend_of("assignee_refs")
            base_ids = list(p_as.new_value["ids"]) if p_as else list(t.assignee_ids)
            ids = [u for u in dict.fromkeys([*base_ids, *add]) if u not in rem]
            if add or rem:
                parsed.append({"field": "assignee_refs", "value": {"ids": ids, "raw": [*(op.assignees_add or []), *[f"-{x}" for x in op.assignees_remove or []]]},
                               "display": ", ".join(map(name_of, ids)) or "(chưa có)", "cmp": ",".join(sorted(ids))})
        nv = _resolve_due_change(op, t, msg_at(ev_for("due")), res)
        if nv is not None:
            parsed.append({"field": "due", "value": nv, "display": display_due(nv["due_type"], nv["due_date"], nv.get("due_text")),
                           "cmp": _due_cmp(nv["due_type"], nv["due_date"], nv.get("due_text"))})
        for p in parsed:
            p["ev"] = ev_for(p["field"])

        def cur(f: str) -> str:
            if f == "assignee_refs":
                return ",".join(sorted(t.assignee_ids))
            if f == "due":
                return _due_cmp(t.due_type, t.due_date, t.due_text)
            return str(getattr(t, f) or "")

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
            pend = pend_of(p["field"])
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
            res["fields"].append(p["field"])
            if p["ev"] not in ev_msg_ids:
                _add_evidence(t, [(p["ev"], _role_for(p["field"]))], at, msg_at)
        _add_evidence(t, ev_ids, at, msg_at)
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
                if t.kind == "open_ask" and t.assignee_ids:
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
