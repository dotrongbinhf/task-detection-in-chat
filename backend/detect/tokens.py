"""Đếm token bằng tiktoken theo model đang dùng (họ gpt-4.1 / gpt-4o dùng o200k_base)."""
from __future__ import annotations

from functools import lru_cache

import tiktoken


@lru_cache(maxsize=4)
def _enc(model: str):
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("o200k_base")


_model = "gpt-4o-mini"


def set_model(model: str) -> None:
    global _model
    _model = model if model != "fake" else "gpt-4o-mini"


def count_tokens(s: str) -> int:
    return len(_enc(_model).encode(s)) if s else 0


def truncate_tokens(s: str, n: int) -> str:
    e = _enc(_model)
    ids = e.encode(s)
    if len(ids) <= n:
        return s
    return e.decode(ids[:n]).rstrip() + "…[cắt]"
