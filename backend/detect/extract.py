"""Gọi bộ trích, parse, Validate, gán người nhận, quy hạn, áp op vào sổ cái (mục 4.5, 4.6)."""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from pydantic import ValidationError

from .config import Config, prompt_version
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


def validate_and_apply(state: EngineState, ctx: BuiltContext, out: ExtractOutput, cfg: Config, turn: int, model: str) -> tuple[list[dict], list[dict]]:
    at = ctx.turn_at
    results: list[dict] = []
    events: list[dict] = []
    new_ids = {m.id for m in ctx.new_msgs}
    msg_at = lambda i: next((m.at for m in ctx.messages if m.id == i), at)  # noqa: E731
    name_of = lambda u: next((m.display_name for m in ctx.members if m.user_id == u), u)  # noqa: E731
    created_now: set[str] = set()

    def resolve(local: Optional[str]) -> Optional[str]:
        """'#12' → message_id. Chấp nhận '#12 [thứ 2 …] Linh: …' (model chép cả dòng tin) — chỉ lấy id ở đầu."""
        if not local:
            return None
        hit = ctx.id_map.get(local.strip())
        if hit is None and (m := re.match(r"\s*(#\d+)(?!\d)", local)):
            hit = ctx.id_map.get(m.group(1))
        return hit

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
                created_at=at, created_turn=turn, last_touched_at=at, status_changed_at=at, prompt_version=prompt_version(ctx.mode), model=model,
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

        # 4. UPDATE / CANCEL phải nhắm vào task có trong prompt (TASK LIÊN QUAN | 3a/3b/khối 6)
        task_id = ctx.task_map.get((op.target or "").strip())
        if not task_id:
            reject(f"unknown_task {op.target} (không có trong prompt)")
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


# ---------- kiểm trùng khi CREATE (mục 7 tài liệu "Ngữ cảnh mới") ----------

def dedup_check(state: EngineState, ctx: BuiltContext, out: ExtractOutput, cfg: Config, embedder: Any = None) -> list[dict[str, Any]]:
    """Mỗi CREATE đủ điều kiện cơ bản → tìm task gần giống trong kho (mọi trạng thái, kể cả declined).

    Ứng viên = giống nội dung (≥ dedup_min_keyword_overlap từ khoá chung, HOẶC cosine ≥ ngưỡng ngữ nghĩa khi
    search_mode ≠ keyword) VÀ (trùng người nhận / người được nhắc hoặc trùng người giao) VÀ không nằm trong prompt.
    Trả về một mục cho mỗi CREATE (kể cả khi không có ứng viên) để ghi trace."""
    from .context import to_iso_days_before
    from .store import TaskStore

    store = TaskStore(state, cfg, embedder)
    thr = store.threshold("dedup")
    new_ids = {m.id for m in ctx.new_msgs}
    in_prompt = set(ctx.task_map.values())
    since = to_iso_days_before(ctx.turn_at, cfg.search_lookup_days)
    checks: list[dict[str, Any]] = []
    for op in out.ops:
        if op.op != "CREATE":
            continue
        anchor_id = ctx.id_map.get((op.anchor or "").strip())
        if not anchor_id:
            continue
        ev = [ctx.id_map.get(e.msg.strip()) for e in op.evidence]
        if anchor_id not in new_ids and not any(i in new_ids for i in ev if i):
            continue  # Validate sẽ loại (not_from_new_message)
        if any(t.anchor_message_id == anchor_id for t in state.tasks):
            continue  # Validate sẽ loại (duplicate_anchor)
        ids, _ = resolve_assignees(op.assignee_refs or [], ctx, anchor_id)
        anchor_msg = next((m for m in ctx.messages if m.id == anchor_id), None)
        assigner = (anchor_msg.author_id if anchor_msg and op.kind != "self_commit" and anchor_msg.author_id not in ids else None)
        query = f"{op.title or ''} {anchor_msg.text if anchor_msg else ''}".strip()
        # người được nhắc trong tin anchor (@mention hoặc gọi tên viết hoa "Mai ơi…") cũng tính là "cùng người":
        # LLM có thể gán sai người nhận (vd. SPEAKER) nhưng việc vẫn là của người được gọi tên → vẫn phải bắt trùng
        named = [x["user_id"] for x in (anchor_msg.mentions if anchor_msg else [])]
        named += store.names_in_query(ctx.key, anchor_msg.text) if anchor_msg and anchor_msg.kind == "message" else []
        named = [u for u in dict.fromkeys(named) if u not in ids and u != (anchor_msg.author_id if anchor_msg else None)]
        people = [*ids, *named]
        hits = store.search(ctx.key, query, people, since, cfg.search_max_results, now=ctx.turn_at, lookup_days=cfg.search_lookup_days)
        rows, cands = [], []
        for h in hits:
            t = h.task
            same_people = bool(set(people) & set(t.assignee_ids)) or bool(assigner and t.assigner_id == assigner)
            why = None
            semantic = h.similarity is not None and h.similarity >= thr
            if h.keyword_overlap < cfg.dedup_min_keyword_overlap and not semantic:
                why = (f"chỉ {h.keyword_overlap} từ khoá chung" + (f", ngữ nghĩa {h.similarity:.2f} < {thr:.2f}" if h.similarity is not None else ""))
            elif not same_people:
                why = "khác người nhận và người giao"
            elif t.task_id in in_prompt:
                why = "đã có trong prompt"
            rows.append({"task_id": t.task_id, "title": t.title, "status": t.status, "score": h.score,
                         "matched": h.matched, "overlap": h.keyword_overlap, "similarity": h.similarity,
                         "same_people": same_people, "candidate": why is None, "why_not": why})
            if why is None:
                cands.append(h)
        checks.append({"title": op.title, "anchor": anchor_id, "query": query, "people": ids, "named": named, "assigner": assigner,
                       "results": rows, "candidates": [h.task.task_id for h in cands], "_hits": cands})
    return checks


# ---------- kiểm tra target của UPDATE / CANCEL (bổ sung ngoài tài liệu — xem DECISIONS) ----------

def target_check(state: EngineState, ctx: BuiltContext, out: ExtractOutput, cfg: Config, embedder: Any = None) -> list[dict[str, Any]]:
    """Đo được: gpt-4.1-mini hay UPDATE/CANCEL nhầm sang một task gần nhất (hoặc bịa T<n>) thay vì search.

    Với mỗi UPDATE/CANCEL nhắm task KHÔNG được link: tìm trong kho bằng nội dung TIN MỚI; nếu có task ngoài prompt
    khớp ≥ dedup_min_keyword_overlap từ khoá VÀ khớp nhiều hơn task đang nhắm (hoặc T<n> không tồn tại) → ứng viên."""
    from .context import to_iso_days_before
    from .store import TaskStore
    from .text import terms

    store = TaskStore(state, cfg, embedder)
    thr = store.threshold()
    in_prompt = set(ctx.task_map.values())
    linked = {p.task.task_id for p in ctx.tasks if p.source == "linked"}
    text = " ".join(m.text for m in ctx.new_msgs if m.kind == "message")
    people = list(dict.fromkeys(x["user_id"] for m in ctx.new_msgs for x in m.mentions))
    since = to_iso_days_before(ctx.turn_at, cfg.search_lookup_days)
    q_terms = set(terms(text))
    checks: list[dict[str, Any]] = []
    if not q_terms:
        return checks
    hits = None
    for op in out.ops:
        if op.op not in ("UPDATE", "CANCEL"):
            continue
        tid = ctx.task_map.get((op.target or "").strip())
        if tid in linked:
            continue
        target = next((t for t in state.tasks if t.task_id == tid), None)
        t_overlap = len(q_terms & set(terms(store.document(target)))) if target else -1
        t_sim = (store.similarity(store.query_text(ctx.key, text), [target]).get(target.task_id, -1.0)
                 if target and store.mode != "keyword" else -1.0)
        if hits is None:
            hits = [h for h in store.search(ctx.key, text, people, since, cfg.search_max_results, now=ctx.turn_at,
                                            lookup_days=cfg.search_lookup_days) if h.task.task_id not in in_prompt]
        # ứng viên: khớp từ khoá tốt hơn target, HOẶC (xếp mọi task theo cosine với tin mới) là một trong tối đa
        # `target_check_max_candidates` task ngoài prompt đứng TRÊN task LLM đang nhắm, cosine ≥ ngưỡng loại nhiễu.
        # Không dùng ngưỡng cosine tuyệt đối để quyết định: đo được task đúng 0.41–0.77, task sai tới 0.58.
        sem = sorted([h for h in hits if h.similarity is not None and h.similarity >= thr and h.similarity > t_sim],
                     key=lambda h: -h.similarity)
        sem_ids = {h.task.task_id for h in sem[: max(0, cfg.target_check_max_candidates)]}
        cands = [h for h in hits
                 if (h.keyword_overlap >= cfg.dedup_min_keyword_overlap and h.keyword_overlap > t_overlap)
                 or h.task.task_id in sem_ids]
        cands.sort(key=lambda h: -(h.similarity if h.similarity is not None else -1))
        checks.append({"op": op.op, "target": op.target, "task_id": tid, "target_title": target.title if target else None,
                       "target_overlap": t_overlap, "target_similarity": None if t_sim < 0 else round(t_sim, 3),
                       "threshold": thr if store.mode != "keyword" else None, "query": text, "people": people,
                       "results": [{"task_id": h.task.task_id, "title": h.task.title, "status": h.task.status, "score": h.score,
                                    "matched": h.matched, "overlap": h.keyword_overlap, "similarity": h.similarity} for h in hits],
                       "candidates": [h.task.task_id for h in cands], "_hits": cands})
    return checks
