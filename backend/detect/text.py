"""Chuẩn hoá tiếng Việt cho khớp tên thành viên: bỏ dấu, xưng hô."""
from __future__ import annotations

import re
import unicodedata


def strip_diacritics(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace("đ", "d").replace("Đ", "D")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", strip_diacritics(s).lower()).strip()


HONORIFICS = {
    "anh", "chi", "em", "ban", "a", "c", "e", "co", "chu", "bac", "sep", "thay", "ong", "ba", "mr", "ms", "bro", "sis",
}
