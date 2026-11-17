"""Đọc config.yaml, ghi đè bằng --set key=value hoặc dict từ UI."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
PROMPT_VERSION = "v6"  # prompts/extract.txt


class Config(BaseModel):
    draft_min: Literal["assignee", "assignee_due"] = "assignee"
    window_k: int = 10
    reply_chain_max: int = 3
    context_token_budget: int = 3500
    message_max_tokens: int = 80
    # hàng đợi: đợi hội thoại yên (v6 mục 3)
    quiet_seconds: float = 60
    max_wait_seconds: float = 300
    max_batch_messages: int = 10
    candidate_ttl_hours: float = 48
    draft_ttl_days: float = 7
    approved_grace_days: int = 3
    undated_open_days: float = 14
    declined_suppress_days: float = 7
    llm_max_retries: int = 2
    # ---- ngữ cảnh: task được link + task khớp nội dung TIN MỚI + task gần nhất ----
    linked_tasks_max: int = 3
    linked_evidence_per_task: int = 3
    search_lookup_days: float = 30
    # ---- ba tầng task trong prompt (v6 mục 6) ----
    detail_recent_max: int = 3     # TASK_DETAILS: task mở nhắc gần nhất
    detail_similar_max: int = 2    # TASK_DETAILS: task mở gần nghĩa nhất với TIN MỚI (xếp hạng, không ngưỡng)
    index_tasks_max: int = 30      # TASK_INDEX: tối đa số dòng
    index_overflow_max: int = 5    # TASK_INDEX tràn: số chỗ cuối dành cho task gần nghĩa nhất trong phần bị cắt
    prompt_cache_key: Optional[str] = "ai_reminder_extract"
    members_max: int = 30  # hội thoại đông hơn: <MEMBERS> chỉ liệt kê người xuất hiện trong prompt (v6 mục 5.1)
    pricing: dict[str, dict[str, float]] = {}


CONFIG_HELP: dict[str, str] = {
    "draft_min": "assignee: nháp hiện khi có người nhận | assignee_due: cần thêm hạn",
    "window_k": "Số tin trước trong ngữ cảnh bộ trích (K)",
    "reply_chain_max": "Số tin tối đa lần ngược theo chuỗi reply",
    "context_token_budget": "Trần token input của bộ trích",
    "message_max_tokens": "Cắt tin dài hơn số token này",
    "quiet_seconds": "Lượt kết thúc khi hội thoại yên quá số giây này (tin kế tiếp cách tin trước > quiet_seconds)",
    "max_wait_seconds": "Trần thời lượng một lượt (giây, từ tin đầu)",
    "max_batch_messages": "Trần số tin một lượt",
    "candidate_ttl_hours": "Task ẩn chưa ai nhận thì hết hạn (giờ)",
    "draft_ttl_days": "Nháp không được chạm tới thì đóng (closed, draft_ttl) sau (ngày)",
    "approved_grace_days": "Task đã duyệt còn mở sau hạn (ngày)",
    "undated_open_days": "Task đã duyệt không hạn còn mở kể từ lần chạm cuối (ngày)",
    "declined_suppress_days": "Đề xuất bị từ chối không được đề xuất lại; task bị từ chối chuyển closed sau (ngày)",
    "llm_max_retries": "Retry khi lỗi parse hoặc mạng",
    "linked_tasks_max": "Task được link (TIN MỚI reply vào tin bằng chứng) tối đa",
    "linked_evidence_per_task": "Tin bằng chứng kèm mỗi task được link",
    "search_lookup_days": "Phạm vi thời gian task được link (ngày)",
    "detail_recent_max": "TASK_DETAILS: số task mở nhắc gần nhất",
    "detail_similar_max": "TASK_DETAILS: số task mở gần nghĩa nhất với TIN MỚI (embedding, xếp hạng)",
    "index_tasks_max": "TASK_INDEX: số dòng tối đa",
    "index_overflow_max": "TASK_INDEX tràn: số chỗ cuối cho task gần nghĩa nhất trong phần bị cắt",
    "prompt_cache_key": "prompt_cache_key gửi OpenAI (null = không gửi)",
    "members_max": "Hội thoại có nhiều thành viên hơn số này: <MEMBERS> chỉ gồm tác giả tin, người được @mention, người nhận / người giao của task trong prompt",
}


def read_cache(f: Path) -> Any:
    """Đọc một file cache JSON; thiếu hoặc hỏng (đang được tiến trình / luồng khác ghi dở) → None = trượt cache."""
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_cache(f: Path, v: Any) -> None:
    """Ghi nguyên tử (file tạm + os.replace) để các luồng `--jobs` / tiến trình song song không đọc phải file ghi dở."""
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


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
