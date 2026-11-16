"""Bộ trích GIẢ LẬP bằng luật — chỉ để demo luồng khi chưa có OPENAI_API_KEY.

KHÔNG phải thành phần của thiết kế v3 (docs: regex không dùng làm trigger). Nó chỉ đóng vai "LLM"
để toàn bộ pipeline (ngữ cảnh → validate → sổ cái → HITL) chạy được offline. Đọc ngữ cảnh đã dựng
(BuiltContext) và trả đúng schema đầu ra của bộ trích (tham chiếu #n / T<n>).
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from .context import BuiltContext, PromptMsg, PromptTask
from .models import Member, empty_op
from .text import HONORIFICS, keywords, norm, norm_aligned

VERBS = (
    "restart|reset|reboot|khoi dong|chay lai|gui|lam|check|review|kiem tra|chuan bi|viet|soan|cap nhat|update|fix|sua|"
    "test|deploy|tong hop|lien he|goi|dat|book|chot|bao cao|trien khai|design|ve|thiet ke|tao|setup|cai|chay|xu ly|doc|"
    "nop|upload|share|gom|ra soat|phan tich|merge|build|research|tim hieu|demo|trinh bay|mock|draft|note|ghi|dich|in|"
    "xem lai|follow|theo doi|lo|support|ho tro|chuyen|dat lich|len lich|sap xep|refactor|migrate|optimize|toi uu|export|"
    "import|confirm|xac nhan|tra loi|reply|email|mail|nhac|order|mua"
)
VERB_RE = re.compile(rf"\b({VERBS})\b")
VERB_WORDS = {v for v in VERBS.split("|") if " " not in v}  # động từ đơn (gửi, làm…) — không tính là khớp nội dung
NEG_RE = re.compile(
    r"\b(de (anh|chi|em|minh|toi) (xem|nghi|tinh)( (da|lai|them))?|nen lam (theo )?huong nao|em gui roi|da gui( roi)?|"
    r"gui roi a|xong roi|da xong|lam xong|done roi|da fix|da merge|da deploy|em xong|xong a|da update)\b")
CANCEL_RE = re.compile(
    r"\b(thoi khoi|khoi lam|khong can (lam )?(nua|nhe|dau)|huy (viec|task|cai)|bo (viec|cai|task) (do|nay)|"
    r"thoi khong (can|lam)|cancel|khong lam nua|tam dung)\b")
ACCEPT_RE = re.compile(
    r"^(ok+|oke+|okie|okay|dc|duoc|da|vang|uh|u|nhan|roger|noted|got it|yes)\b|"
    r"\b(de em( lam| nhan| lo| xu ly)?|em lam (cho|nhe|a|luon)|em nhan|em lo|em xu ly|da em lam|em lam duoc|em handle|em take)\b")
SELF_RE = re.compile(
    rf"\b(em|minh|toi|tui|anh|chi)( se| de| xin)?( (mai|chieu nay|toi nay|sang mai|hom nay|tuan sau|thu \d))? ({VERBS})\b")
OPEN_RE = re.compile(
    r"\b(ai (di|hop|tham gia|lam|nhan|xu ly|check|co the|ranh|lam duoc|giup|support|ho tro|review)|co ai|can (ai|nguoi)|"
    r"minh can|team can|ben minh can|chung ta can|can (cap nhat|update|lam|viet|fix|sua|chuan bi|bo sung|review)|"
    r"phai (cap nhat|update|fix|sua|lam lai))\b")
ASK_RE = re.compile(r"\b(nhe|nha|giup|gium|nho|di|pls|please)\b")
DUECHANGE_RE = re.compile(r"\b(lui|doi (han|sang|lai)|chuyen sang|gia han|hoan|day (len|lui)|doi thanh|thoi .*nhe|sang .*nhe|han (moi|la))\b")

WD = {"hai": 1, "ba": 2, "tu": 3, "nam": 4, "sau": 5, "bay": 6}


def _due(**kw: Any) -> dict[str, Any]:
    base = dict(due_text=None, due_kind=None, due_day_offset=None, due_weekday=None, due_week_offset=None,
                due_week_part=None, due_abs_day=None, due_abs_month=None)
    base.update(kw)
    return base


def detect_due(orig: str, members: list[Member]) -> Optional[dict[str, Any]]:
    n = norm_aligned(orig)

    def take(m: re.Match) -> str:
        s = orig[m.start(): m.end()].strip()
        return re.sub(r"\s+(nhé|nha|nhe|nhá|ạ|đi|nhé ạ)$", "", s, flags=re.I).strip()

    if m := re.search(r"\btruoc (buoi |khi |dot )?(uat|demo|release|hop|meeting|sprint review|review|go[- ]?live|deploy|"
                      r"ban giao|nghiem thu|kick ?off|launch)\b[^,.!?]*", n):
        return _due(due_text=take(m), due_kind="event")
    if m := re.search(r"\b(asap|som nhat co the|cang som cang tot)\b", n):
        return _due(due_text=take(m), due_kind="none")
    if m := re.search(r"\b(cuoi|dau) tuan( sau| toi| nay)?\b", n):
        nxt = bool(m.group(2)) and m.group(2).strip() in ("sau", "toi")
        return _due(due_text=take(m), due_kind="week_part", due_week_part="end" if m.group(1) == "cuoi" else "start",
                    due_week_offset=1 if nxt else (0 if m.group(1) == "cuoi" else None))
    if m := re.search(r"\b(?:(?:truoc |han |vao |toi da )?)(thu|t) ?([2-7]|hai|ba|tu|nam|sau|bay)\b( tuan (sau|toi|nay))?|"
                      r"\b(chu nhat|cn)\b( tuan (sau|toi|nay))?", n):
        if m.group(5):
            wd, wk = 7, m.group(7)
        else:
            raw = m.group(2)
            wd, wk = (int(raw) if raw.isdigit() else WD[raw] + 1), m.group(4)  # số như trong chữ: thứ 5 → 5
        off = None if not wk else (0 if wk == "nay" else 1)
        return _due(due_text=take(m), due_kind="weekday", due_weekday=wd, due_week_offset=off)
    if m := re.search(r"\b(\d{1,2})/(\d{1,2})\b", n):
        return _due(due_text=take(m), due_kind="absolute", due_abs_day=int(m.group(1)), due_abs_month=int(m.group(2)))
    if m := re.search(r"\bngay (\d{1,2})\b", n):
        return _due(due_text=take(m), due_kind="absolute", due_abs_day=int(m.group(1)))
    if m := re.search(r"\b(eod|cuoi ngay|trong hom nay|hom nay|chieu nay|toi nay|sang nay|trua nay)\b", n):
        return _due(due_text=take(m), due_kind="relative_day", due_day_offset=0)
    if m := re.search(r"\bngay kia\b", n):
        return _due(due_text=take(m), due_kind="relative_day", due_day_offset=2)
    has_mai = any(norm(x.display_name) == "mai" for x in members)
    for m in re.finditer(r"\b(sang |chieu |toi |trua |truoc |den |trong ngay )?mai\b", n):
        word = orig[m.start() + len(m.group(1) or ""): m.end()]
        after = (n[m.end():].strip().split(" ") or [""])[0]
        is_name = has_mai and not m.group(1) and word.startswith("M") and after not in HONORIFICS and after != "minh"
        if not is_name:  # "Mai em gửi…" = ngày mai; "Mai, thứ 6 xong nhé" = chị Mai
            return _due(due_text=take(m), due_kind="relative_day", due_day_offset=1)
    return None


def names_in_text(orig: str, members: list[Member], exclude: str) -> list[dict[str, Any]]:
    """Tên thành viên xuất hiện trong tin (không @). Tên trùng từ thời gian (Mai, Năm, Tuấn/tuần) chỉ tính khi viết hoa."""
    n = norm_aligned(orig)
    out = []
    for mb in members:
        if mb.user_id == exclude:
            continue
        found = False
        for nm in [mb.display_name, *mb.aliases]:
            key = norm(nm)
            for m in re.finditer(rf"(^|[^a-z0-9]){re.escape(key)}(?![a-z0-9])", n):
                idx = m.start() + len(m.group(1))
                word = orig[idx: idx + len(key)]
                if key in ("mai", "nam", "tuan") and not re.match(r"[A-ZĐ]", word):
                    continue
                if key == "nam" and re.search(r"thu\s*$", n[:idx]):
                    continue
                out.append({"uid": mb.user_id, "name": word, "idx": idx})
                found = True
                break
            if found:
                break
    return sorted(out, key=lambda x: x["idx"])


def make_title(orig: str, due: Optional[dict], members: list[Member], mentions: list[dict] | None = None) -> str:
    s = f" {orig} "
    for m in mentions or []:
        s = s.replace(f"@{m['name']}", " ")
    if due and due.get("due_text"):
        s = s.replace(due["due_text"], " ", 1)
    names = "|".join(sorted({re.escape(x) for mb in members for x in [mb.display_name, *mb.aliases]}, key=len, reverse=True))
    I = re.I
    s = re.sub(rf"^\s*((e|a|c|em|anh|chị|chi)\s+)?({names})\s*(ơi|oi)?\s*(,|:|\s)\s*((với|và|voi|va)\s+({names})\s+)?", " ", s, flags=I)
    s = re.sub(r"^\s*(anh|chị|chi|em|a|c|e|mọi người|team|cả nhà)\s*(ơi|oi)\s*[,:]?\s*", " ", s, flags=I)
    s = re.sub(r"^\s*(ok|oke|okie|dạ|da|vâng|ừ)\s*[,.]?\s*", " ", s, flags=I)
    s = re.sub(r"^\s*(có ai|ai)(\s+(rảnh|ranh|có thể|co the))?\s+", " ", s, flags=I)
    s = re.sub(r"^\s*(mình cần|team cần|bên mình cần|chúng ta cần|cần|để em|em sẽ|mai em|em nhớ|em|anh|chị|mình|tôi|e|a)\s+", " ", s, flags=I)
    s = re.sub(r"\s(giúp|giùm|giup|gium)\s(anh|em|chị|chi|mình|minh|a|e|c)\b", " ", s, flags=I)
    s = re.sub(r"\s(cái|cai)\s", " ", s, flags=I)
    s = re.sub(r"\s(thay anh|thay em|thay chị)\s", " thay người giao ", s, flags=I)
    for _ in range(2):
        s = re.sub(r"\s+(được không|làm được không|không|ko|nhé|nha|nhe|nhá|ạ|đi|với|giúp|giùm|luôn|truoc|trước|vào|hạn|gấp)\s*[!?.]*\s*$", " ", s, flags=I)
    s = re.sub(r"[,;:.!?\s]+$", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    if not s:
        s = orig[:60]
    s = s[0].upper() + s[1:]
    return s if len(s) <= 70 else s[:67] + "…"


def pick_task(ctx: BuiltContext, m: PromptMsg, people: list[str] | None = None, prefer_unassigned: bool = False,
              min_score: int = 13) -> Optional[PromptTask]:
    """Chọn task mà TIN MỚI nói tới: điểm ngữ cảnh + reply + người được nhắc + từ khoá (mô phỏng LLM đọc nội dung)."""
    open_ = [t for t in ctx.tasks if t.source in LIVE_SOURCES and t.task.status in ("candidate", "draft", "approved")]
    ref = m.reply_to_id or m.target_id
    for t in open_:
        if any(e.message_id == ref for e in t.task.evidence):
            return t
    kw = keywords(m.text)
    prev = next((h for h in reversed(ctx.history) if h.kind == "message" and h.author_id != m.author_id), None)
    best, best_s = None, -1
    for t in open_:
        s = (100 if t.source == "linked" else 0) + 15 * len(kw & keywords(f"{t.task.title} {t.task.notes or ''}"))
        if people and any(p in t.task.assignee_ids for p in people):
            s += 15
        hit_prev = bool(prev and any(e.message_id == prev.id for e in t.task.evidence))
        if hit_prev:
            s += 40  # trả lời ngay tin trước
        # task không được link phải khớp nội dung (từ khoá) hoặc vừa được nhắc ở tin trước, không đoán bừa theo người
        if (t.source != "linked" and not prefer_unassigned and not hit_prev
                and not (kw - VERB_WORDS) & keywords(f"{t.task.title} {t.task.notes or ''}")):
            continue
        anchor_author = next((x.author_id for x in ctx.messages if x.id == t.task.anchor_message_id), None)
        own_ask = t.task.assigner_id == m.author_id or anchor_author == m.author_id
        if prefer_unassigned and not t.task.assignee_ids and not own_ask:
            s += 25
        if s > best_s:
            best, best_s = t, s
    return best if best and best_s >= min_score else None


LIVE_SOURCES = ("linked", "recent", "matched", "target_check")


def _declined_similar(ctx: BuiltContext, text: str) -> bool:
    """Task declined có trong prompt (chỉ qua LINKED_TASKS) trùng ≥ 2 từ khoá → không tạo lại (quy tắc 5 của prompt)."""
    kw = keywords(text)
    return any(t.task.status == "declined" and len(kw & keywords(t.task.title)) >= 2 for t in ctx.tasks)


def _ev(msg: str, role: str) -> dict[str, str]:
    return {"msg": msg, "role": role}


def _change(field: str, value: Any, msg: str) -> dict[str, str]:
    return {"field": field, "value": json.dumps(value, ensure_ascii=False), "evidence_msg": msg}


def heuristic_extract(ctx: BuiltContext) -> dict[str, Any]:
    ops: list[dict[str, Any]] = []

    for m in ctx.new_msgs:
        if m.kind == "reaction":
            if (m.reaction_type or "") not in ("like", "ok", "check", "heart", "love"):
                continue
            t = next((p for p in ctx.tasks if p.source in LIVE_SOURCES and p.task.status in ("candidate", "draft", "approved")
                      and any(e.message_id == m.target_id for e in p.task.evidence)), None)
            if t and not t.task.assignee_ids:
                ops.append(empty_op("UPDATE", target=t.local, evidence=[_ev(m.local, "accept")],
                                    changes=[_change("assignee_refs", [f"SPEAKER:{m.local}"], m.local), _change("kind", "assigned", m.local)]))
            elif not t and m.target_id:
                tgt = next((x for x in ctx.messages if x.id == m.target_id), None)
                if tgt and tgt.kind == "message":
                    tn = norm(tgt.text)
                    if OPEN_RE.search(tn) or (VERB_RE.search(tn) and ASK_RE.search(tn)):
                        due = detect_due(tgt.text, ctx.members) or {}
                        ops.append(empty_op("CREATE", due=due, kind="assigned", anchor=tgt.local,
                                            title=make_title(tgt.text, due, ctx.members), assignee_refs=[f"SPEAKER:{m.local}"],
                                            due_kind=due.get("due_kind") or "none",
                                            evidence=[_ev(tgt.local, "assign"), _ev(m.local, "accept")]))
            continue

        n = norm(m.text)
        words = [w for w in n.split(" ") if w]
        due = detect_due(m.text, ctx.members)
        mentions = [x for x in m.mentions if x["user_id"] != m.author_id]
        names = names_in_text(m.text, ctx.members, m.author_id)
        people = [x["user_id"] for x in mentions] + [x["uid"] for x in names]
        due_ev = [_ev(m.local, "due")] if due else []

        if NEG_RE.search(n) and not CANCEL_RE.search(n):
            continue

        # ---- huỷ ----
        if CANCEL_RE.search(n):
            t = pick_task(ctx, m, people)
            if t:
                ops.append(empty_op("CANCEL", target=t.local, evidence=[_ev(m.local, "cancel")]))
            continue

        # ---- đổi người nhận ----
        if re.search(r"\b(de|cho|chuyen (cho|sang)|giao cho)\s+(\S+)\s+(lam|lo|xu ly|nhan)\b", n) and (names or mentions):
            t = pick_task(ctx, m, people)
            if t:
                ref = [x["user_id"] for x in mentions] if mentions else [names[0]["name"]]
                ops.append(empty_op("UPDATE", target=t.local, evidence=[_ev(m.local, "assignee")],
                                    changes=[_change("assignee_refs", ref, m.local)]))
                continue

        is_accept = bool(ACCEPT_RE.search(n)) and (len(words) <= 8 or bool(re.search(r"\b(de em|em nhan|em lam)\b", n))) and not mentions
        has_verb = bool(VERB_RE.search(n))

        # ---- đổi / bổ sung hạn cho việc có sẵn ----
        if due and (DUECHANGE_RE.search(n) or (not mentions and len(words) <= 7)):
            t = pick_task(ctx, m, people)
            if t:
                ch = [_change("due", due, m.local)]
                if is_accept and not t.task.assignee_ids:
                    ch.append(_change("assignee_refs", [f"SPEAKER:{m.local}"], m.local))
                ops.append(empty_op("UPDATE", target=t.local, evidence=[_ev(m.local, "due")], changes=ch))
                continue
            if DUECHANGE_RE.search(n):
                continue

        # ---- nhận việc ----
        if is_accept:
            t = pick_task(ctx, m, [m.author_id], prefer_unassigned=True)
            if t:
                already = m.author_id in t.task.assignee_ids
                ch = []
                if not already and (not t.task.assignee_ids or t.task.kind == "open_ask"):
                    ch.append(_change("assignee_refs", [f"SPEAKER:{m.local}"], m.local))
                    if t.task.kind != "assigned":
                        ch.append(_change("kind", "assigned", m.local))
                if due:
                    ch.append(_change("due", due, m.local))
                if ch or already:
                    ops.append(empty_op("UPDATE", target=t.local, evidence=[_ev(m.local, "accept")], changes=ch))
                continue
            # nhận một việc vừa được nêu trong LỊCH SỬ GẦN → anchor là câu nêu việc
            prev = next((h for h in list(reversed(ctx.history))[:6]
                         if h.kind == "message" and h.author_id != m.author_id
                         and (OPEN_RE.search(norm(h.text)) or (VERB_RE.search(norm(h.text)) and re.search(r"\b(giup|nho|ai|can)\b", norm(h.text))))), None)
            if prev:
                d = detect_due(prev.text, ctx.members) or due or {}
                ops.append(empty_op("CREATE", due=d, kind="assigned", anchor=prev.local, title=make_title(prev.text, d, ctx.members),
                                    assignee_refs=[f"SPEAKER:{m.local}"], due_kind=d.get("due_kind") or "none",
                                    evidence=[_ev(prev.local, "assign"), _ev(m.local, "accept"), *due_ev]))
            continue

        # ---- nêu việc chưa ai nhận ----
        if OPEN_RE.search(n) and not mentions and not names:
            ops.append(empty_op("CREATE", due=due, kind="open_ask", anchor=m.local, title=make_title(m.text, due, ctx.members),
                                assignee_refs=[], due_kind=(due or {}).get("due_kind") or "none",
                                evidence=[_ev(m.local, "assign"), *due_ev]))
            continue

        # ---- giao việc ----
        name_lead = bool(names) and names[0]["idx"] <= 3
        dm_ask = ctx.conv_type == "dm" and has_verb and re.search(r"\b(giup|nho|gium|nhe|nha)\b", n) and re.search(r"\b(em|anh|chi|ban|e|a|c)\b", n)
        group_ask = has_verb and re.search(r"\b(lam giup|giup (anh|chi|em)|nho (em|anh|chi))\b", n)
        if (mentions or name_lead) and has_verb and (ASK_RE.search(n) or due or mentions):
            segs = [s.strip() for s in re.split(r"[,;]|\bcòn\b", m.text, flags=re.I) if s.strip()]
            owners = [{"s": s, "ms": [x for x in mentions if f"@{x['name']}" in s or x["name"] in s],
                       "ns": names_in_text(s, ctx.members, m.author_id)} for s in segs]
            multi = [o for o in owners if (o["ms"] or o["ns"]) and VERB_RE.search(norm(o["s"]))]
            parts = multi if len(multi) >= 2 else [{"s": m.text, "ms": mentions, "ns": [] if mentions else names}]
            for p in parts:
                d = detect_due(p["s"], ctx.members) or due or {}
                refs = [x["user_id"] for x in p["ms"]] if p["ms"] else [x["name"] for x in p["ns"]]
                ops.append(empty_op("CREATE", due=d, kind="assigned", anchor=m.local, title=make_title(p["s"], d, ctx.members, mentions),
                                    assignee_refs=refs, due_kind=d.get("due_kind") or "none",
                                    evidence=[_ev(m.local, "assign"), *([_ev(m.local, "due")] if d else [])]))
            continue
        if dm_ask or group_ask:
            ops.append(empty_op("CREATE", due=due, kind="assigned", anchor=m.local, title=make_title(m.text, due, ctx.members),
                                assignee_refs=["ADDRESSEE"] if ctx.conv_type == "dm" else [],
                                due_kind=(due or {}).get("due_kind") or "none", evidence=[_ev(m.local, "assign"), *due_ev]))
            continue

        # ---- tự hứa ----
        if SELF_RE.search(n) and not mentions and (due or re.search(r"\b(nhe|nha|se|de)\b", n)) and "?" not in m.text:
            ops.append(empty_op("CREATE", due=due, kind="self_commit", anchor=m.local, title=make_title(m.text, due, ctx.members),
                                assignee_refs=[f"SPEAKER:{m.local}"], due_kind=(due or {}).get("due_kind") or "none",
                                evidence=[_ev(m.local, "assign"), *due_ev]))

    # quy tắc 5: việc đã bị từ chối thì không tạo lại
    def anchor_text(o: dict) -> str:
        return next((x.text for x in ctx.messages if x.local == o.get("anchor")), "")

    ops = [o for o in ops if o["op"] != "CREATE" or not _declined_similar(ctx, anchor_text(o))]
    mt = "no_change" if not ops else "new_task" if all(o["op"] == "CREATE" for o in ops) else "task_in_prompt"
    return {"message_type": mt, "ops": ops}


def heuristic_gate(ctx: BuiltContext) -> float:
    if heuristic_extract(ctx)["ops"]:
        return 0.9
    signal = any(m.mentions or detect_due(m.text, ctx.members) or m.kind == "reaction" for m in ctx.new_msgs)
    return 0.3 if signal else 0.06
