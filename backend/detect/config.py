"""Đọc config.yaml, ghi đè bằng --set key=value hoặc dict từ UI."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
PROMPT_VERSION = "v4"  # prompt của context_mode = linked_recent_search; chế độ scored vẫn dùng extract_v3
ContextMode = Literal["scored", "linked_recent_search"]


def prompt_version(mode: str) -> str:
    return "v3" if mode == "scored" else PROMPT_VERSION


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
    # ---- ngữ cảnh mới (tài liệu "Ngữ cảnh mới - task được link, 3 task gần nhất, tìm task") ----
    context_mode: ContextMode = "linked_recent_search"
    linked_tasks_max: int = 3
    linked_evidence_per_task: int = 3
    recent_tasks_max: int = 3
    search_enabled: bool = True
    search_max_results: int = 5
    search_lookup_days: float = 30
    extract_max_calls_per_turn: int = 2
    dedup_check_on_create: bool = True
    dedup_min_keyword_overlap: int = 2
    target_check_on_update: bool = True
    search_mode: Literal["keyword", "semantic", "hybrid"] = "hybrid"
    semantic_min_similarity: Optional[float] = None  # kiểm tra target; None = mặc định embedder (OpenAI 0.45)
    dedup_semantic_min_similarity: Optional[float] = None  # kiểm trùng; None = mặc định embedder (OpenAI 0.55)
    semantic_margin: float = 0.0
    target_check_max_candidates: int = 3  # kiểm tra target: số ứng viên NGỮ NGHĨA tối đa đưa vào lần 2 (DECISIONS #44)
    semantic_floor: float = 0.35                     # cosine tối thiểu để task vào danh sách xếp hạng ngữ nghĩa
    rrf_k: int = 10
    prompt_cache_key: Optional[str] = "ai_reminder_extract"
    pricing: dict[str, dict[str, float]] = {}


CONFIG_HELP: dict[str, str] = {
    "draft_min": "assignee: nháp hiện khi có người nhận | assignee_due: cần thêm hạn",
    "gate_mode": "none = trần chất lượng | shadow = chấm p không chặn | llm = chặn khi p < ngưỡng",
    "gate_threshold": "Ngưỡng p khi gate_mode = llm",
    "gate_window_k": "Số tin trước trong ngữ cảnh của cổng",
    "window_k": "Số tin trước trong ngữ cảnh bộ trích (K)",
    "reply_chain_max": "Số tin tối đa lần ngược theo chuỗi reply",
    "open_tasks_max": "(scored) Task đang mở tối đa đưa vào prompt",
    "evidence_per_task": "Tin bằng chứng gần nhất kèm mỗi task",
    "closed_lookup_days": "(scored) Phạm vi truy vấn task cũ trong DB (ngày)",
    "closed_tasks_max": "(scored) Task cũ tối đa đưa vào prompt",
    "context_token_budget": "Trần token input của bộ trích",
    "message_max_tokens": "Cắt tin dài hơn số token này",
    "coalesce_seconds": "Gộp tin đến dồn (giây)",
    "candidate_ttl_hours": "Task ẩn chưa ai nhận thì hết hạn (giờ)",
    "draft_ttl_days": "Nháp không được chạm tới thì stale (ngày)",
    "approved_grace_days": "Task đã duyệt còn mở sau hạn (ngày)",
    "undated_open_days": "Task đã duyệt không hạn còn mở kể từ lần chạm cuối (ngày)",
    "declined_suppress_days": "Task/đề xuất bị từ chối còn dùng để chặn tạo lại (ngày)",
    "llm_max_retries": "Retry khi lỗi parse hoặc mạng",
    "context_mode": "scored = v3 (bảng điểm, top 5) | linked_recent_search = task được link + 3 task gần nhất + tìm task",
    "linked_tasks_max": "Task được link (reply/reaction) tối đa",
    "linked_evidence_per_task": "Tin bằng chứng kèm mỗi task được link",
    "recent_tasks_max": "Task được nhắc gần nhất đưa vào prompt",
    "search_enabled": "Cho phép LLM yêu cầu tìm task trong kho",
    "search_max_results": "Số kết quả tìm kiếm trả về LLM",
    "search_lookup_days": "Phạm vi thời gian tìm task / task được link (ngày)",
    "extract_max_calls_per_turn": "Số lần gọi bộ trích tối đa mỗi lượt",
    "dedup_check_on_create": "Kiểm trùng trong kho trước khi CREATE",
    "dedup_min_keyword_overlap": "Số từ khoá chung tối thiểu để coi là có thể trùng",
    "target_check_on_update": "UPDATE/CANCEL nhắm task không được link mà khớp TIN MỚI kém hơn một task ngoài prompt (hoặc T<n> không tồn tại) → code tìm và hỏi lại LLM",
    "search_mode": "keyword = BM25 | semantic = embedding | hybrid = trộn BM25 + embedding (RRF)",
    "semantic_min_similarity": "Kiểm tra target: cosine tối thiểu của task gần nghĩa nhất; trống = mặc định embedder",
    "dedup_semantic_min_similarity": "Kiểm trùng: cosine tối thiểu để coi là có thể trùng; trống = mặc định embedder",
    "semantic_margin": "Kiểm tra target: ứng viên phải gần nghĩa hơn task đang nhắm ít nhất chừng này",
    "target_check_max_candidates": "Kiểm tra target: số ứng viên gần nghĩa nhất tối đa đưa cho LLM chọn ở lần 2",
    "semantic_floor": "Cosine tối thiểu để task vào danh sách xếp hạng ngữ nghĩa",
    "rrf_k": "Hằng số k của Reciprocal Rank Fusion",
    "prompt_cache_key": "prompt_cache_key gửi OpenAI (null = không gửi)",
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
