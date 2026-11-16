"""Đọc config.yaml, ghi đè bằng --set key=value hoặc dict từ UI."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
PROMPT_VERSION = "v5.1"  # prompts/extract.txt


class Config(BaseModel):
    draft_min: Literal["assignee", "assignee_due"] = "assignee"
    gate_mode: Literal["none", "shadow", "llm"] = "none"
    gate_threshold: float = 0.2
    gate_window_k: int = 5
    window_k: int = 10
    reply_chain_max: int = 3
    evidence_per_task: int = 1
    context_token_budget: int = 3000
    message_max_tokens: int = 80
    coalesce_seconds: float = 5
    candidate_ttl_hours: float = 48
    draft_ttl_days: float = 7
    approved_grace_days: int = 3
    undated_open_days: float = 14
    declined_suppress_days: float = 7
    llm_max_retries: int = 2
    # ---- ngữ cảnh: task được link + task khớp nội dung TIN MỚI + task gần nhất ----
    linked_tasks_max: int = 3
    linked_evidence_per_task: int = 3
    search_max_results: int = 5
    search_lookup_days: float = 30
    target_check_on_update: bool = True    # UPDATE/CANCEL nhắm nhầm → code tìm task khớp hơn → gọi lần 2
    semantic_min_similarity: Optional[float] = None  # kiểm tra target; None = mặc định embedder (OpenAI 0.45)
    target_check_max_candidates: int = 3  # kiểm tra target: số ứng viên gần nghĩa tối đa (DECISIONS #44)
    semantic_floor: float = 0.35  # cosine tối thiểu để task vào danh sách xếp hạng ngữ nghĩa
    rrf_k: int = 10
    prompt_cache_key: Optional[str] = "ai_reminder_extract"
    candidate_tasks_max: int = 5            # tổng task ở khối CANDIDATE_TASKS (matched + recent)
    matched_tasks_max: int = 3              # task khớp nội dung TIN MỚI tối đa
    matched_min_keyword_overlap: int = 1    # vào matched khi ≥ n từ khoá chung hoặc cosine ≥ semantic_floor
    pricing: dict[str, dict[str, float]] = {}


CONFIG_HELP: dict[str, str] = {
    "draft_min": "assignee: nháp hiện khi có người nhận | assignee_due: cần thêm hạn",
    "gate_mode": "none = trần chất lượng | shadow = chấm p không chặn | llm = chặn khi p < ngưỡng",
    "gate_threshold": "Ngưỡng p khi gate_mode = llm",
    "gate_window_k": "Số tin trước trong ngữ cảnh của cổng",
    "window_k": "Số tin trước trong ngữ cảnh bộ trích (K)",
    "reply_chain_max": "Số tin tối đa lần ngược theo chuỗi reply",
    "evidence_per_task": "Tin bằng chứng gần nhất kèm mỗi task",
    "context_token_budget": "Trần token input của bộ trích",
    "message_max_tokens": "Cắt tin dài hơn số token này",
    "coalesce_seconds": "Gộp tin đến dồn (giây)",
    "candidate_ttl_hours": "Task ẩn chưa ai nhận thì hết hạn (giờ)",
    "draft_ttl_days": "Nháp không được chạm tới thì stale (ngày)",
    "approved_grace_days": "Task đã duyệt còn mở sau hạn (ngày)",
    "undated_open_days": "Task đã duyệt không hạn còn mở kể từ lần chạm cuối (ngày)",
    "declined_suppress_days": "Đề xuất bị từ chối không được đề xuất lại; task bị từ chối chuyển closed sau (ngày)",
    "llm_max_retries": "Retry khi lỗi parse hoặc mạng",
    "linked_tasks_max": "Task được link (reply/reaction) tối đa",
    "linked_evidence_per_task": "Tin bằng chứng kèm mỗi task được link",
    "search_max_results": "Số kết quả tìm kiếm trả về LLM",
    "search_lookup_days": "Phạm vi thời gian tìm task / task được link (ngày)",
    "target_check_on_update": "UPDATE/CANCEL nhắm task không được link mà khớp TIN MỚI kém hơn một task ngoài prompt (hoặc T<n> không tồn tại) → code tìm và hỏi lại LLM",
    "semantic_min_similarity": "Kiểm tra target: cosine tối thiểu để task gần nghĩa được đưa vào lần 2; trống = mặc định embedder",
    "target_check_max_candidates": "Kiểm tra target: số ứng viên gần nghĩa nhất tối đa đưa cho LLM chọn ở lần 2",
    "semantic_floor": "Cosine tối thiểu để task vào danh sách xếp hạng ngữ nghĩa",
    "rrf_k": "Hằng số k của Reciprocal Rank Fusion",
    "prompt_cache_key": "prompt_cache_key gửi OpenAI (null = không gửi)",
    "candidate_tasks_max": "Tổng task ở khối CANDIDATE_TASKS (khớp nội dung + gần nhất)",
    "matched_tasks_max": "Task khớp nội dung TIN MỚI tối đa",
    "matched_min_keyword_overlap": "Số từ khoá chung tối thiểu để task vào nhóm khớp nội dung (hoặc cosine ≥ semantic_floor)",
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
