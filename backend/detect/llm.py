"""Lớp LLM (mục 7): OpenAI Chat Completions + cache đĩa + retry + probe; bộ trích giả lập; FakeLLM cho unit test.

    python -m detect.llm probe
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from dotenv import load_dotenv

from .config import ROOT
from .context import BuiltContext
from .heuristic import heuristic_extract, heuristic_gate
from .models import EXTRACT_JSON_SCHEMA, extract_schema

load_dotenv(ROOT / ".env")
# Biến để trống trong .env (vd. OPENAI_BASE_URL=) sẽ bị SDK hiểu là URL rỗng → bỏ đi.
for _k in ("OPENAI_BASE_URL", "GATE_MODEL", "EXTRACT_MODEL"):
    if not os.environ.get(_k, "").strip():
        os.environ.pop(_k, None)

CACHE_DIR = ROOT / ".cache" / "llm"


@dataclass
class GateResult:
    p: Optional[float]
    raw: str
    latency_ms: int
    cached: bool
    warning: Optional[str] = None


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
    gate_model: str
    extract_model: str

    def gate(self, ctx: BuiltContext) -> GateResult: ...
    def extract(self, ctx: BuiltContext, attempt: int) -> ExtractResult: ...


def has_openai_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def gate_model_name() -> str:
    return os.environ.get("GATE_MODEL") or "gpt-4o-mini"


def extract_model_name() -> str:
    return os.environ.get("EXTRACT_MODEL") or "gpt-4.1-mini"


# ---------- cache đĩa: khoá = hash(model + prompt + tham số) ----------

def _cached(key_obj: Any, no_cache: bool, fn: Callable[[], dict]) -> tuple[dict, bool]:
    key = hashlib.sha256(json.dumps(key_obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    f = CACHE_DIR / f"{key}.json"
    if not no_cache and f.exists():
        return json.loads(f.read_text(encoding="utf-8")), True
    v = fn()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    return v, False


def _retry(fn: Callable[[], Any], retries: int = 3) -> Any:
    """Retry có backoff với 429 / 5xx."""
    for i in range(retries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            status = getattr(e, "status_code", 0) or 0
            if i == retries or (status != 429 and status < 500):
                raise
            time.sleep(0.5 * 2**i)


class OpenAILLM:
    mode = "openai"

    def __init__(self, no_cache: bool = False):
        from openai import OpenAI

        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url=os.environ.get("OPENAI_BASE_URL") or None)
        self.gate_model = gate_model_name()
        self.extract_model = extract_model_name()
        self.no_cache = no_cache
        from .embed import OpenAIEmbedder

        self.embedder = OpenAIEmbedder(name=os.environ.get("EMBED_MODEL") or "text-embedding-3-small", no_cache=no_cache)

    def gate(self, ctx: BuiltContext) -> GateResult:
        """max_tokens=1, logprobs; p = P(yes) / (P(yes) + P(no)), cộng dồn biến thể token."""
        t0 = time.time()
        req = dict(model=self.gate_model, messages=[{"role": "system", "content": ctx.gate_system},
                                                    {"role": "user", "content": ctx.gate_user}],
                   max_tokens=1, temperature=0, seed=7, logprobs=True, top_logprobs=10)

        def call() -> dict:
            r = _retry(lambda: self.client.chat.completions.create(**req))
            ch = r.choices[0]
            top = ch.logprobs.content[0].top_logprobs if ch.logprobs and ch.logprobs.content else []
            return {"content": ch.message.content or "", "top": [{"token": t.token, "logprob": t.logprob} for t in top]}

        v, hit = _cached(req, self.no_cache, call)
        py = sum(math.exp(t["logprob"]) for t in v["top"] if t["token"].strip().lower() in ("yes", "y", "có"))
        pn = sum(math.exp(t["logprob"]) for t in v["top"] if t["token"].strip().lower() in ("no", "n", "không"))
        if py + pn == 0:
            return GateResult(0.5, v["content"], int((time.time() - t0) * 1000), hit, "không có yes/no trong top_logprobs → p = 0.5")
        return GateResult(py / (py + pn), v["content"], int((time.time() - t0) * 1000), hit)

    def extract(self, ctx: BuiltContext, attempt: int) -> ExtractResult:
        """response_format json_schema strict; temperature=0, seed cố định; khối 1 đứng đầu để tận dụng prompt caching.

        Chế độ scored gửi đúng request v3 (schema v3, không prompt_cache_key) để so sánh và để cache đĩa cũ còn trúng."""
        t0 = time.time()
        req = dict(model=self.extract_model, messages=[{"role": "system", "content": ctx.system},
                                                       {"role": "user", "content": ctx.user}],
                   temperature=0, seed=7 + attempt, response_format={"type": "json_schema", "json_schema": extract_schema(ctx.mode)})
        if ctx.cache_key:
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


class HeuristicLLM:
    """Đóng vai LLM bằng luật — chỉ để demo offline."""

    mode = "heuristic"
    gate_model = "heuristic"
    extract_model = "heuristic"

    def __init__(self) -> None:
        from .embed import HashEmbedder

        self.embedder = HashEmbedder()  # n-gram ký tự, KHÔNG hiểu nghĩa — chỉ để demo offline

    def gate(self, ctx: BuiltContext) -> GateResult:
        t0 = time.time()
        p = heuristic_gate(ctx)
        return GateResult(p, "yes" if p >= 0.5 else "no", int((time.time() - t0) * 1000), False)

    def extract(self, ctx: BuiltContext, attempt: int) -> ExtractResult:
        t0 = time.time()
        out = heuristic_extract(ctx)
        return ExtractResult(json.dumps(out, ensure_ascii=False, indent=2), input_tokens=ctx.tokens["total"],
                             latency_ms=int((time.time() - t0) * 1000))


@dataclass
class FakeLLM:
    """Cho unit test: trả kết quả định sẵn theo id TIN MỚI (message_id thật → được ánh xạ sang #n).

    `by_message[id]` = {"ops": [...], "search": ..., "message_type": ...} cho mọi lần gọi, hoặc [lần1, lần2] theo lần gọi
    trong lượt. Chế độ retrieve_first (schema v5) không trả `search`."""

    by_message: dict[str, Any] = field(default_factory=dict)  # message_id -> {"ops": [...]} (tham chiếu bằng message_id / task_id)
    p_by_message: dict[str, float] = field(default_factory=dict)
    mode: str = "fake"
    gate_model: str = "fake"
    extract_model: str = "fake"

    def gate(self, ctx: BuiltContext) -> GateResult:
        p = max((self.p_by_message.get(m.id, 0.0) for m in ctx.new_msgs), default=0.0)
        return GateResult(p, "yes" if p >= 0.5 else "no", 0, False)

    calls: list[dict[str, Any]] = field(default_factory=list)  # nhật ký các lần gọi (để test)
    embedder: Any = None  # None → tìm kiếm chỉ BM25; test ngữ nghĩa truyền FakeEmbedder

    def extract(self, ctx: BuiltContext, attempt: int) -> ExtractResult:
        out: dict[str, Any] = {"ops": []}
        self.calls.append({"call": ctx.call, "new": [m.id for m in ctx.new_msgs], "extra_kind": ctx.extra_kind,
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
                if loc.get("search") is not None and ctx.mode == "linked_recent_search":
                    out["search"] = loc["search"]
                if loc.get("message_type") is not None and ctx.mode != "scored":
                    out["message_type"] = loc["message_type"]
        if ctx.mode == "linked_recent_search":
            out.setdefault("search", None)
        return ExtractResult(json.dumps(out, ensure_ascii=False))


def _localize(s: str, ctx: BuiltContext) -> str:
    """Thay message_id / task_id thật bằng #n / T<n> của prompt."""
    for mid, local in sorted(ctx.rev_map.items(), key=lambda x: -len(x[0])):
        s = s.replace(f'"@{mid}"', f'"{local}"').replace(f"SPEAKER:@{mid}", f"SPEAKER:{local}")
    for local, tid in sorted(ctx.task_map.items(), key=lambda x: -len(x[1])):
        s = s.replace(f'"@{tid}"', f'"{local}"')
    return s


def make_llm(mode: str = "auto", no_cache: bool = False) -> LLM:
    if mode == "openai" or (mode == "auto" and has_openai_key()):
        if not has_openai_key():
            raise RuntimeError("Chưa có OPENAI_API_KEY (điền vào backend/.env)")
        return OpenAILLM(no_cache)
    return HeuristicLLM()


def probe() -> dict[str, Any]:
    """Model có trả logprobs/top_logprobs, nhận json_schema strict, nhận temperature không."""
    if not has_openai_key():
        return {"ok": False, "error": "Chưa có OPENAI_API_KEY trong backend/.env", "models": []}
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url=os.environ.get("OPENAI_BASE_URL") or None)
    results = []
    for model in dict.fromkeys([gate_model_name(), extract_model_name()]):
        r: dict[str, Any] = {"model": model}
        try:
            a = client.chat.completions.create(model=model, messages=[{"role": "user", "content": "Trả lời yes hoặc no: 1+1=2?"}],
                                               max_tokens=1, temperature=0, logprobs=True, top_logprobs=5)
            r["temperature"] = True
            lp = a.choices[0].logprobs
            r["logprobs"] = bool(lp and lp.content and lp.content[0].top_logprobs)
        except Exception as e:  # noqa: BLE001
            r["logprobs"] = False
            r["logprobs_error"] = str(e)
        try:
            b = client.chat.completions.create(model=model, messages=[{"role": "user", "content": 'Trả về {"ops": []}'}],
                                               temperature=0, response_format={"type": "json_schema", "json_schema": EXTRACT_JSON_SCHEMA})
            r["json_schema_strict"] = bool(b.choices[0].message.content)
        except Exception as e:  # noqa: BLE001
            r["json_schema_strict"] = False
            r["json_schema_error"] = str(e)
        results.append(r)
    ok = all(x.get("logprobs") and x.get("json_schema_strict") for x in results)
    return {"ok": ok, "models": results}


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
