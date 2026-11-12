"""resolve_due + bảng lịch. Múi giờ Asia/Ho_Chi_Minh (+07:00, không có DST). Code tự tính ngày, LLM không tính."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

VN = timezone(timedelta(hours=7))

TIME_CONVENTIONS = (
    '"cuối tuần" = thứ 6 tuần này; "cuối tuần sau" = thứ 6 tuần sau; "đầu tuần sau" = thứ 2 tuần sau. '
    '"thứ N" không nói tuần: sau hôm nay → tuần này; đã qua hoặc là hôm nay → tuần sau. '
    '"ngày D" không nói tháng: tháng này nếu D chưa qua, ngược lại tháng sau. '
    '"sớm nhất có thể", "ASAP" = không có hạn. Giờ trong ngày chỉ giữ trong due_text.'
)


def parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(VN)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(VN).replace(microsecond=0).isoformat()


def ts(iso: str) -> float:
    return parse(iso).timestamp()


def local_date(iso: str) -> date:
    return parse(iso).date()


def weekday(d: date) -> int:
    """thứ 2 = 1 … chủ nhật = 7"""
    return d.isoweekday()


def monday_of(d: date) -> date:
    return d - timedelta(days=d.isoweekday() - 1)


def vi_weekday(n: int) -> str:
    return "chủ nhật" if n == 7 else f"thứ {n + 1}"


def ddmm(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}"


def format_due_date(d: Optional[str]) -> str:
    if not d:
        return "—"
    dd = date.fromisoformat(d)
    return f"{vi_weekday(weekday(dd))} ({ddmm(dd)})"


def fmt_time(iso: str) -> str:
    return parse(iso).strftime("%H:%M")


def fmt_day_time(iso: str) -> str:
    dt = parse(iso)
    return f"{vi_weekday(dt.isoweekday())} {dt.day:02d}/{dt.month:02d} {dt.strftime('%H:%M')}"


def calendar14(ref_iso: str) -> str:
    today = local_date(ref_iso)
    mon = monday_of(today)
    lines = []
    for i in range(14):
        d = mon + timedelta(days=i)
        lines.append(f"{vi_weekday(weekday(d))} {ddmm(d)}/{d.year}" + (" ← hôm nay" if d == today else ""))
    return "\n".join(lines)


@dataclass
class DueResolution:
    due_type: str  # calendar | event | none
    due_date: Optional[str]
    notes: list[str] = field(default_factory=list)  # fabricated_date_blocked | unresolvable_due
    explain: str = ""


DATE_FIELDS = ("due_day_offset", "due_weekday", "due_week_offset", "due_week_part", "due_abs_day", "due_abs_month")


def resolve_due(f: dict[str, Any], ref_iso: str) -> DueResolution:
    """Quy hạn ra ngày theo quy ước mục 6. ref_iso = thời điểm tin chứa hạn."""
    today = local_date(ref_iso)
    twd = weekday(today)
    kind = f.get("due_kind") or "none"

    def bad(why: str) -> DueResolution:
        return DueResolution("none", None, ["unresolvable_due"], why)

    def ok(d: date, why: str) -> DueResolution:
        return DueResolution("calendar", d.isoformat(), [], why)

    if kind == "none":
        return DueResolution("none", None, [], "không có hạn")
    if kind == "event":
        filled = any(f.get(k) is not None for k in DATE_FIELDS)
        return DueResolution("event", None, ["fabricated_date_blocked"] if filled else [], "hạn theo sự kiện → không quy ra ngày")
    if kind == "relative_day":
        off = f.get("due_day_offset")
        if off is None:
            return bad("relative_day thiếu due_day_offset")
        return ok(today + timedelta(days=off), f"hôm nay {ddmm(today)} + {off} ngày")
    if kind == "weekday":
        n = f.get("due_weekday")
        if n is None or not 1 <= n <= 7:
            return bad("weekday thiếu/sai due_weekday")
        mon = monday_of(today)
        wk = f.get("due_week_offset")
        if wk is None:
            this_week = mon + timedelta(days=n - 1)
            if n > twd:
                return ok(this_week, f"{vi_weekday(n)} sau hôm nay → tuần này")
            return ok(this_week + timedelta(days=7), f"{vi_weekday(n)} đã qua/là hôm nay → tuần sau")
        return ok(mon + timedelta(days=7 * wk + n - 1), f"{vi_weekday(n)}, tuần +{wk}")
    if kind == "week_part":
        part = f.get("due_week_part")
        if not part:
            return bad("week_part thiếu due_week_part")
        mon = monday_of(today)
        wk = f.get("due_week_offset")
        if part == "end":
            wk = 0 if wk is None else wk
            return ok(mon + timedelta(days=7 * wk + 4), f"cuối tuần +{wk} = thứ 6")
        wk = 1 if wk is None else wk
        return ok(mon + timedelta(days=7 * wk), f"đầu tuần +{wk} = thứ 2")
    if kind == "absolute":
        d = f.get("due_abs_day")
        if d is None or not 1 <= d <= 31:
            return bad("absolute thiếu/sai due_abs_day")
        y, m = today.year, f.get("due_abs_month")
        if m is None:
            m = today.month if d >= today.day else today.month + 1
        elif m < today.month - 6:
            y += 1
        if m > 12:
            m -= 12
            y += 1
        try:
            dd = date(y, m, d)
        except ValueError:
            return bad(f"ngày {d}/{m} không tồn tại")
        return ok(dd, f"ngày {d} → tháng {m}" if f.get("due_abs_month") is None else f"ngày {d}/{m}")
    return bad("due_kind không hợp lệ")
