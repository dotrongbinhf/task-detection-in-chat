"""Lớp LLM: OpenAI Chat Completions + cache đĩa + retry + probe; FakeLLM cho unit test.

    python -m detect.llm probe

Không có OPENAI_API_KEY → CLI và server báo lỗi rõ ràng (v6 mục 8.3: đã bỏ bộ trích giả lập bằng luật).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from dotenv import load_dotenv

from .config import ROOT, read_cache, write_cache
from .context import BuiltContext
from .models import EXTRACT_JSON_SCHEMA

load_dotenv(ROOT / ".env")
# Biến để trống trong .env (vd. OPENAI_BASE_URL=) sẽ bị SDK hiểu là URL rỗng → bỏ đi.
for _k in ("OPENAI_BASE_URL", "EXTRACT_MODEL", "RESPONSE_FORMAT", "EMBED_MODEL"):
    if not os.environ.get(_k, "").strip():
        os.environ.pop(_k, None)

CACHE_DIR = ROOT / ".cache" / "llm"
NO_KEY = "Chưa có OPENAI_API_KEY (điền vào backend/.env). v6 không còn bộ trích giả lập: cần key để chạy engine."


@dataclass
class ExtractResult:
    raw: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0
    disk_cache: bool = False
    refusal: Optional[str] = None


class LLM(Protocol):
    mode: str
    extract_model: str

    def extract(self, ctx: BuiltContext, attempt: int) -> ExtractResult: ...


def has_openai_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def extract_model_name() -> str:
    return os.environ.get("EXTRACT_MODEL") or "gpt-4o-mini"  # model tham chiếu v6 (mục 14.3)


def is_openai() -> bool:
    """Endpoint là OpenAI thật (không đặt OPENAI_BASE_URL hoặc trỏ api.openai.com) — chỉ khi đó mới gửi `prompt_cache_key`."""
    url = os.environ.get("OPENAI_BASE_URL") or ""
    return not url or "api.openai.com" in url


def response_format() -> dict[str, Any]:
    """`RESPONSE_FORMAT=json_object` cho endpoint không nhận json_schema strict; Pydantic vẫn kiểm schema khi parse."""
    if os.environ.get("RESPONSE_FORMAT") == "json_object":
        return {"type": "json_object"}
    return {"type": "json_schema", "json_schema": EXTRACT_JSON_SCHEMA}


def make_embedder(no_cache: bool = False) -> Any:
    """`EMBED_MODEL=none` → không embedding (TASK_DETAILS / TASK_INDEX chỉ xếp theo độ mới).
    `EMBED_BASE_URL` / `EMBED_API_KEY` cho embedding ở endpoint khác bộ trích; trống = dùng chung OPENAI_*."""
    name = os.environ.get("EMBED_MODEL") or "text-embedding-3-small"
    if name.lower() == "none":
        return None
    from .embed import OpenAIEmbedder

    return OpenAIEmbedder(name=name, no_cache=no_cache)


# ---------- cache đĩa: khoá = hash(model + prompt + tham số) ----------

def _cached(key_obj: Any, no_cache: bool, fn: Callable[[], dict]) -> tuple[dict, bool]:
    key = hashlib.sha256(json.dumps(key_obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    f = CACHE_DIR / f"{key}.json"
    if not no_cache and (hit := read_cache(f)) is not None:
        return hit, True
    v = fn()
    write_cache(f, v)
    return v, False


def _retry(fn: Callable[[], Any], retries: int = 8) -> Any:
    """Retry có backoff với 429 / 5xx (tới ~2 phút): chạy song song nhiều hội thoại dễ chạm giới hạn token / phút,
    lượt bị bỏ vì 429 sẽ làm sai số đo."""
    for i in range(retries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            status = getattr(e, "status_code", 0) or 0
            if i == retries or (status != 429 and status < 500):
                raise
            time.sleep(min(30.0, 1.0 * 2**i))


class OpenAILLM:
    mode = "openai"

    def __init__(self, no_cache: bool = False, seed: int = 7):
        from openai import OpenAI

        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url=os.environ.get("OPENAI_BASE_URL") or None)
        self.extract_model = extract_model_name()
        self.no_cache = no_cache
        self.seed = seed  # `seed` của lời gọi, nằm trong khoá cache đĩa → mỗi seed là một lần chạy riêng (v6 mục 11.5)
        self.embedder = make_embedder(no_cache)

    def extract(self, ctx: BuiltContext, attempt: int) -> ExtractResult:
        """response_format json_schema strict; temperature=0, seed cố định; khối 1 đứng đầu để tận dụng prompt caching."""
        t0 = time.time()
        req = dict(model=self.extract_model, messages=[{"role": "system", "content": ctx.system},
                                                       {"role": "user", "content": ctx.user}],
                   temperature=0, seed=self.seed + attempt, response_format=response_format())
        if ctx.cache_key and is_openai():
            req["prompt_cache_key"] = ctx.cache_key  # định tuyến các lời gọi có cùng tiền tố tới cùng máy cache

        def call() -> dict:
            r = _retry(lambda: self.client.chat.completions.create(**req))
            msg = r.choices[0].message
            u = r.usage
            details = getattr(u, "prompt_tokens_details", None)
            return {"raw": msg.content or "", "refusal": getattr(msg, "refusal", None),
                    "input_tokens": u.prompt_tokens if u else 0, "output_tokens": u.completion_tokens if u else 0,
                    "cached_tokens": (getattr(details, "cached_tokens", 0) or 0) if details else 0}

        v, hit = _cached(req, self.no_cache and attempt == 0, call)
        return ExtractResult(**v, latency_ms=int((time.time() - t0) * 1000), disk_cache=hit)


@dataclass
class FakeLLM:
    """Cho unit test: trả kết quả định sẵn theo id TIN MỚI (message_id thật → được ánh xạ sang #n).

    `by_message[id]` = {"ops": [...], "message_type": ...} cho mọi lần gọi, hoặc [lần1, lần2] theo lần gọi trong lượt."""

    by_message: dict[str, Any] = field(default_factory=dict)  # message_id -> {"ops": [...]} (tham chiếu bằng message_id / task_id)
    mode: str = "fake"
    extract_model: str = "fake"
    calls: list[dict[str, Any]] = field(default_factory=list)  # nhật ký các lần gọi (để test)
    embedder: Any = None  # None → không xếp hạng theo embedding; test ngữ nghĩa truyền FakeEmbedder

    def extract(self, ctx: BuiltContext, attempt: int) -> ExtractResult:
        out: dict[str, Any] = {"ops": []}
        self.calls.append({"call": ctx.call, "new": [m.id for m in ctx.new_msgs],
                           "task_map": dict(ctx.task_map), "user": ctx.user})
        for m in ctx.new_msgs:
            v = self.by_message.get(m.id)
            if isinstance(v, list):
                v = v[min(ctx.call, len(v)) - 1]
            if isinstance(v, str):  # chuỗi thô (để test JSON hỏng)
                return ExtractResult(v)
            if v:
                loc = json.loads(_localize(json.dumps(v, ensure_ascii=False), ctx))
                out["ops"] += loc.get("ops", [])
                if loc.get("message_type") is not None:
                    out["message_type"] = loc["message_type"]
        return ExtractResult(json.dumps(out, ensure_ascii=False))


def _localize(s: str, ctx: BuiltContext) -> str:
    """Thay message_id / task_id thật bằng #n / T<n> của prompt."""
    for mid, local in sorted(ctx.rev_map.items(), key=lambda x: -len(x[0])):
        s = s.replace(f'"@{mid}"', f'"{local}"').replace(f"SPEAKER:@{mid}", f"SPEAKER:{local}")
    for local, tid in sorted(ctx.task_map.items(), key=lambda x: -len(x[1])):
        s = s.replace(f'"@{tid}"', f'"{local}"')
    return s


def make_llm(no_cache: bool = False, seed: int = 7) -> LLM:
    if not has_openai_key():
        raise RuntimeError(NO_KEY)
    return OpenAILLM(no_cache, seed)


def probe() -> dict[str, Any]:
    """Endpoint + model bộ trích nhận gì: json_schema strict, json_object, seed, prompt_cache_key; embedding có chạy không.
    Kết quả gợi ý đặt RESPONSE_FORMAT / EMBED_MODEL trong .env."""
    if not has_openai_key():
        return {"ok": False, "error": NO_KEY, "models": []}
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url=os.environ.get("OPENAI_BASE_URL") or None, max_retries=1)
    model = extract_model_name()
    msgs = [{"role": "system", "content": 'Return JSON: {"message_type": "no_change", "ops": []}'},
            {"role": "user", "content": "NEW_MESSAGES: ok"}]
    r: dict[str, Any] = {"model": model, "base_url": os.environ.get("OPENAI_BASE_URL") or "api.openai.com"}

    def attempt(name: str, **kw: Any) -> None:
        try:
            b = client.chat.completions.create(model=model, messages=msgs, temperature=0, **kw)
            raw = b.choices[0].message.content or ""
            r[name] = parse_ok = __import__("detect.extract", fromlist=["parse_extract"]).parse_extract(raw)[0] is not None
            if not parse_ok:
                r[name + "_raw"] = raw[:200]
        except Exception as e:  # noqa: BLE001
            r[name] = False
            r[name + "_error"] = str(e)[:300]

    attempt("json_schema_strict", response_format={"type": "json_schema", "json_schema": EXTRACT_JSON_SCHEMA})
    attempt("json_object", response_format={"type": "json_object"})
    attempt("seed", response_format={"type": "json_object"}, seed=7)
    attempt("prompt_cache_key", response_format={"type": "json_object"}, extra_body={"prompt_cache_key": "probe"})
    emb = make_embedder(True)
    if emb is None:
        r["embedding"] = "tắt (EMBED_MODEL=none)"
    else:
        try:
            r["embedding"] = f"{emb.name}: {len(emb.embed(['thử'])[0])} chiều"
        except Exception as e:  # noqa: BLE001
            r["embedding"] = False
            r["embedding_error"] = str(e)[:300]
    r["suggest"] = {"RESPONSE_FORMAT": "json_schema" if r["json_schema_strict"] else "json_object" if r["json_object"] else "?",
                    "EMBED_MODEL": None if r["embedding"] else "none (hoặc EMBED_BASE_URL / EMBED_API_KEY của endpoint khác)"}
    ok = (r["json_schema_strict"] or r["json_object"]) and r["seed"] is not False
    return {"ok": bool(ok), "models": [r]}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        res = probe()
        print(json.dumps(res, ensure_ascii=False, indent=2))
        run_dir = ROOT / "runs" / f"probe_{time.strftime('%Y%m%d_%H%M%S')}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "capabilities.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"→ {run_dir / 'capabilities.json'}")
        sys.exit(0 if res["ok"] else 1)
    print("Dùng: python -m detect.llm probe")
