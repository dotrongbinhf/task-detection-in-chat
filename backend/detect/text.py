"""Chuẩn hoá tiếng Việt: bỏ dấu, từ khoá, stopword, xưng hô."""
from __future__ import annotations

import re
import unicodedata


def strip_diacritics(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace("đ", "d").replace("Đ", "D")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", strip_diacritics(s).lower()).strip()


def norm_aligned(s: str) -> str:
    """Bỏ dấu từng ký tự, giữ nguyên độ dài để ánh xạ vị trí về chuỗi gốc."""
    out = []
    for ch in s:
        c = strip_diacritics(ch).lower()
        out.append(c[0] if len(c) >= 1 else " ")
    return "".join(out)


HONORIFICS = {
    "anh", "chi", "em", "ban", "a", "c", "e", "co", "chu", "bac", "sep", "thay", "ong", "ba", "mr", "ms", "bro", "sis",
}

STOPWORDS = HONORIFICS | {
    "nhe", "nha", "oi", "di", "voi", "va", "cho", "cua", "cai", "nay", "do", "kia", "la", "thi", "ma", "de", "duoc", "dc",
    "khong", "ko", "k", "nhung", "cac", "mot", "nhieu", "it", "rat", "qua", "lam", "roi", "chua", "se", "dang", "da", "vua",
    "moi", "luon", "nua", "con", "ok", "oke", "okie", "uh", "um", "vang", "giup", "gium", "ho", "minh", "toi", "tui",
    "chung", "ta", "no", "ay", "the", "nao", "gi", "sao", "vay", "nhi", "truoc", "sau", "trong", "ngoai", "tren", "duoi",
    "khi", "luc", "hom", "ngay", "mai", "thu", "tuan", "thang", "nam", "gio", "phut", "sang", "chieu", "cuoi", "dau", "to",
    "for", "and", "pls", "plz", "please", "xiu", "chut", "can", "phai", "hay", "neu", "vao", "ra", "len", "xuong", "tu",
    "den", "bay", "hon", "nhat", "cung", "deu", "o", "tai", "ah",
}


def keywords(s: str) -> set[str]:
    out = set()
    for w in re.split(r"[^a-z0-9]+", norm(s)):
        if len(w) < 2 or w in STOPWORDS or w.isdigit():
            continue
        out.add(w)
    return out


# "tuần", "tháng" là stopword khi chấm điểm v3 nhưng lại phân biệt việc khi tìm ("báo cáo tuần" ≠ "báo cáo tháng")
SEARCH_STOPWORDS = STOPWORDS - {"tuan", "thang"}


def terms(s: str) -> list[str]:
    """Từ khoá cho tìm kiếm (BM25): giữ thứ tự và số lần lặp; giữ "tuần"/"tháng"; giữ số ≥ 3 chữ số (vd. "PR #482")."""
    return [w for w in re.split(r"[^a-z0-9]+", norm(s))
            if len(w) >= 2 and w not in SEARCH_STOPWORDS and (not w.isdigit() or len(w) >= 3)]
