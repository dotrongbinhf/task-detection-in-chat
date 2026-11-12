"""Đọc config.yaml, ghi đè bằng --set key=value hoặc dict từ UI."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
PROMPT_VERSION = "v3"


class Config(BaseModel):
    draft_min: Literal["assignee", "assignee_due"] = "assignee"
    gate_mode: Literal["none", "shadow", "llm"] = "none"
    gate_threshold: float = 0.2
    gate_window_k: int = 5
    window_k: int = 10
    reply_chain_max: int = 3
    open_tasks_max: int = 5
    evidence_per_task: int = 1
    closed_lookup_days: int = 30
    closed_tasks_max: int = 2
    context_token_budget: int = 3000
    message_max_tokens: int = 80
    coalesce_seconds: float = 5
    candidate_ttl_hours: float = 48
    draft_ttl_days: float = 7
    approved_grace_days: int = 3
    undated_open_days: float = 14
    declined_suppress_days: float = 7
    llm_max_retries: int = 2
    pricing: dict[str, dict[str, float]] = {}


CONFIG_HELP: dict[str, str] = {
    "draft_min": "assignee: nháp hiện khi có người nhận | assignee_due: cần thêm hạn",
    "gate_mode": "none = trần chất lượng | shadow = chấm p không chặn | llm = chặn khi p < ngưỡng",
    "gate_threshold": "Ngưỡng p khi gate_mode = llm",
    "gate_window_k": "Số tin trước trong ngữ cảnh của cổng",
    "window_k": "Số tin trước trong ngữ cảnh bộ trích (K)",
    "reply_chain_max": "Số tin tối đa lần ngược theo chuỗi reply",
    "open_tasks_max": "Task đang mở tối đa đưa vào prompt",
    "evidence_per_task": "Tin bằng chứng gần nhất kèm mỗi task",
    "closed_lookup_days": "Phạm vi truy vấn task cũ trong DB (ngày)",
    "closed_tasks_max": "Task cũ tối đa đưa vào prompt",
    "context_token_budget": "Trần token input của bộ trích",
    "message_max_tokens": "Cắt tin dài hơn số token này",
    "coalesce_seconds": "Gộp tin đến dồn (giây)",
    "candidate_ttl_hours": "Task ẩn chưa ai nhận thì hết hạn (giờ)",
    "draft_ttl_days": "Nháp không được chạm tới thì stale (ngày)",
    "approved_grace_days": "Task đã duyệt còn mở sau hạn (ngày)",
    "undated_open_days": "Task đã duyệt không hạn còn mở kể từ lần chạm cuối (ngày)",
    "declined_suppress_days": "Task/đề xuất bị từ chối còn dùng để chặn tạo lại (ngày)",
    "llm_max_retries": "Retry khi lỗi parse hoặc mạng",
}


def load_config(overrides: dict[str, Any] | None = None, sets: list[str] | None = None) -> Config:
    data: dict[str, Any] = {}
    p = ROOT / "config.yaml"
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    for s in sets or []:
        k, _, v = s.partition("=")
        data[k.strip()] = yaml.safe_load(v)
    if overrides:
        data.update({k: v for k, v in overrides.items() if k in Config.model_fields and v is not None})
    return Config(**data)
