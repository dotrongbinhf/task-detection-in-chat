"""Embedding cho tìm kiếm ngữ nghĩa trong kho task (tìm task matched, kiểm tra target).

- `OpenAIEmbedder` — `text-embedding-3-small` (đa ngôn ngữ), cache đĩa `.cache/emb/` theo hash(model + văn bản).
- `FakeEmbedder` — vector định sẵn theo cụm từ khoá, chỉ cho unit test.

Khi chuyển sang model nội bộ: thay `OpenAIEmbedder` bằng `bge-m3` / `multilingual-e5` (cùng giao diện `embed`),
rồi hiệu chỉnh lại `semantic_min_similarity` (thang cosine mỗi model một khác).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from typing import Optional, Protocol

from dotenv import load_dotenv

from .config import ROOT, read_cache, write_cache
from .text import norm

load_dotenv(ROOT / ".env")
for _k in ("OPENAI_BASE_URL", "EMBED_MODEL"):  # biến rỗng trong .env → SDK hiểu là URL rỗng (DECISIONS #17)
    if not os.environ.get(_k, "").strip():
        os.environ.pop(_k, None)
CACHE_DIR = ROOT / ".cache" / "emb"


class Embedder(Protocol):
    name: str
    tokens_used: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def cosine(a: list[float], b: list[float]) -> float:
    da = math.sqrt(sum(x * x for x in a)) or 1.0
    db = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (da * db)


def _key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


@dataclass
class OpenAIEmbedder:
    name: str = "text-embedding-3-small"
    # Đo trên data/sample (bỏ @mention cả hai phía): task đúng 0.41–0.77 (trung vị 0.54), task sai cao nhất 0.58 —
    # ngưỡng tuyệt đối không tách được; xếp hạng thì tốt (task đúng hạng 1 ở 11/14 tin). Nên chỉ dùng embedding để
    # XẾP HẠNG ứng viên cho LLM chọn, không dùng ngưỡng cosine để quyết định thay LLM.
    tokens_used: int = 0           # token gửi đi thật (không tính cache trúng)
    no_cache: bool = False
    _mem: dict[str, list[float]] = field(default_factory=dict)

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[Optional[list[float]]] = []
        miss: list[tuple[int, str, str]] = []
        for i, t in enumerate(texts):
            k = _key(self.name, t)
            v = self._mem.get(k)
            f = CACHE_DIR / f"{k}.json"
            if v is None and not self.no_cache:
                v = read_cache(f)
                if v is not None:
                    self._mem[k] = v
            out.append(v)
            if v is None:
                miss.append((i, t, k))
        if miss:
            from openai import OpenAI

            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url=os.environ.get("OPENAI_BASE_URL") or None)
            r = client.embeddings.create(model=self.name, input=[t or " " for _, t, _ in miss])
            self.tokens_used += getattr(r.usage, "total_tokens", 0) or 0
            for (i, _, k), d in zip(miss, r.data):
                out[i] = list(d.embedding)
                self._mem[k] = out[i]
                write_cache(CACHE_DIR / f"{k}.json", out[i])
        return [v or [] for v in out]


@dataclass
class FakeEmbedder:
    """Cho unit test: vector định sẵn theo cụm từ khoá — văn bản chứa cụm nào thì có chiều đó."""

    concepts: dict[str, list[str]] = field(default_factory=dict)  # tên khái niệm -> các cụm (bỏ dấu, chữ thường)
    name: str = "fake"
    tokens_used: int = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        names = sorted(self.concepts)
        out = []
        for t in texts:
            n = norm(t)
            v = [1.0 if any(p in n for p in self.concepts[c]) else 0.0 for c in names]
            out.append(v + [0.01])  # tránh vector 0
        return out
