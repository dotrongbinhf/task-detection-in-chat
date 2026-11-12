"""Cổng lọc tuỳ chọn (mục 4.4): none = không gọi; shadow = chấm p, không chặn; llm = bỏ lượt khi p < ngưỡng."""
from __future__ import annotations

from typing import Any, Optional

from .config import Config
from .context import BuiltContext
from .llm import LLM


def run_gate(llm: LLM, ctx: BuiltContext, cfg: Config) -> Optional[dict[str, Any]]:
    if cfg.gate_mode == "none":
        return None
    g = llm.gate(ctx)
    p = 0.5 if g.p is None else g.p
    skip = cfg.gate_mode == "llm" and p < cfg.gate_threshold
    return {"mode": cfg.gate_mode, "p": g.p, "threshold": cfg.gate_threshold, "skipped": skip, "warning": g.warning,
            "model": llm.gate_model, "latency_ms": g.latency_ms, "prompt": ctx.gate_user, "cached": g.cached, "raw": g.raw}
