"""Kiểu dữ liệu dùng chung: tin nhắn, sự kiện, task, đề xuất thay đổi, op của bộ trích, trạng thái engine."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------- Dữ liệu đầu vào ----------


class Mention(BaseModel):
    user_id: str
    name: str


class Reaction(BaseModel):
    user_id: str
    type: str
    created_at: str


class Message(BaseModel):
    id: str
    conversation_id: str
    conversation_type: Literal["group", "dm"] = "group"
    thread_root_id: Optional[str] = None
    created_at: str
    author_id: str
    author_name: str
    text: str
    mentions: list[Mention] = Field(default_factory=list)
    reply_to_id: Optional[str] = None
    reactions: list[Reaction] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)
    source: Literal["dataset", "user"] = "dataset"


class Member(BaseModel):
    user_id: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)


class StreamEvent(BaseModel):
    """Một tin nhắn trong luồng sự kiện. v6: reaction không còn là sự kiện mà in kèm dòng tin (mục 4);
    `kind = "reaction"` và các trường target/user/type chỉ còn để đọc state.json của lần chạy cũ."""

    kind: Literal["message", "reaction"] = "message"
    id: str
    key: str
    at: str
    message: Optional[Message] = None
    target_id: Optional[str] = None
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    type: Optional[str] = None


UserActionType = Literal["approve", "decline", "approve_change", "decline_change"]


class UserAction(BaseModel):
    at: str
    user_id: str
    message_id: str = ""
    action: UserActionType
    source: Literal["dataset", "ui"] = "dataset"
    task_id: Optional[str] = None
    change_id: Optional[str] = None


TaskKind = Literal["assigned", "self_commit", "open_ask"]


class GoldTask(BaseModel):
    gold_id: str
    conversation_key: str
    anchor_message_id: str
    supporting_message_ids: list[str]
    title: str
    kind: TaskKind
    assignee_ids: list[str]
    due_type: Literal["calendar", "event", "none"]
    due_date: Optional[str] = None
    final_status: Literal["active", "cancelled"] = "active"
    scenario_tags: list[str] = Field(default_factory=list)
    # tin xác nhận KHÔNG đổi sổ cái ("dạ" khi task đã giao đúng người đó) — không thuộc supporting (v6 mục 10.4)
    confirm_message_ids: list[str] = Field(default_factory=list)


# ---------- Sổ cái ----------

# v6: một trạng thái đóng `closed` + `closed_reason` (mục 8.2). "expired" / "stale" chỉ còn để đọc lần chạy cũ.
TaskStatus = Literal["candidate", "draft", "approved", "declined", "cancelled", "closed", "expired", "stale"]
ClosedReason = Literal["candidate_ttl", "draft_ttl", "past_due", "undated", "declined_ttl"]
EvidenceRole = Literal["assign", "accept", "assignee", "due", "content", "cancel", "context"]
DueKind = Literal["none", "event", "relative_day", "weekday", "week_part", "absolute"]
ChangeField = Literal["title", "notes", "kind", "assignee_refs", "due", "status"]

OPEN_STATUSES = ("candidate", "draft", "approved", "declined")
DUE_FIELDS = ("due_text", "due_kind", "due_day_offset", "due_weekday", "due_week_offset",
              "due_week_part", "due_abs_day", "due_abs_month")


class Evidence(BaseModel):
    message_id: str
    role: EvidenceRole
    added_at: str


class FieldHistory(BaseModel):
    field: str
    old: Any = None
    new: Any = None
    evidence_message_id: Optional[str] = None
    at: str
    via: Literal["draft_update", "change_approved", "create"]


class Task(BaseModel):
    task_id: str
    conversation_key: str
    status: TaskStatus
    kind: TaskKind
    title: str
    notes: Optional[str] = None
    assignee_refs_raw: list[str] = Field(default_factory=list)
    assignee_ids: list[str] = Field(default_factory=list)
    assigner_id: Optional[str] = None
    due_text: Optional[str] = None
    due_kind: Optional[DueKind] = None
    due_day_offset: Optional[int] = None
    due_weekday: Optional[int] = None
    due_week_offset: Optional[int] = None
    due_week_part: Optional[Literal["end", "start"]] = None
    due_abs_day: Optional[int] = None
    due_abs_month: Optional[int] = None
    due_type: Literal["calendar", "event", "none"] = "none"
    due_date: Optional[str] = None
    anchor_message_id: str
    evidence: list[Evidence] = Field(default_factory=list)
    field_history: list[FieldHistory] = Field(default_factory=list)
    approvals: dict[str, Literal["pending", "approved", "declined"]] = Field(default_factory=dict)
    created_at: str
    created_turn: int
    last_touched_at: str
    # created_at của tin bằng chứng mới nhất trong chat. Thao tác duyệt KHÔNG cập nhật.
    last_mentioned_at: str = ""
    shown_at: Optional[str] = None
    status_changed_at: str
    closed_reason: Optional[ClosedReason] = None
    draft_update_count: int = 0
    prompt_version: str
    model: str


class PendingChange(BaseModel):
    change_id: str
    task_id: str
    field: ChangeField
    old_value: Any = None
    new_value: Any = None
    new_display: str
    old_display: str
    evidence_message_id: str
    created_at: str
    status: Literal["pending", "approved", "declined", "superseded"] = "pending"
    approver_ids: list[str] = Field(default_factory=list)
    notified_ids: list[str] = Field(default_factory=list)
    resolved_at: Optional[str] = None


class Counters(BaseModel):
    task: int = 0
    change: int = 0
    turn: int = 0
    msg: int = 0
    # số task / đề xuất theo hội thoại → id `K<thứ tự hội thoại>.<n>` không phụ thuộc thứ tự chạy (v6 mục 8.4)
    task_by_conv: dict[str, int] = Field(default_factory=dict)
    change_by_conv: dict[str, int] = Field(default_factory=dict)


class EngineState(BaseModel):
    """Toàn bộ trạng thái một lần chạy. Lượt (`turns`) và trace là dict tự do để dễ ghi JSONL."""

    dataset: str
    members: dict[str, list[Member]]
    events: list[StreamEvent]
    processed: list[str] = Field(default_factory=list)
    simNow: str
    tasks: list[Task] = Field(default_factory=list)
    changes: list[PendingChange] = Field(default_factory=list)
    turns: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    userActions: list[UserAction] = Field(default_factory=list)
    counters: Counters = Field(default_factory=Counters)
    gold: Optional[list[GoldTask]] = None
    hardNegatives: list[str] = Field(default_factory=list)
    hardNegativeTags: dict[str, str] = Field(default_factory=dict)   # message_id -> loại hard negative (v6 mục 10.3)
    turnLabels: list[dict[str, Any]] = Field(default_factory=list)   # turn_labels.jsonl (chấm op theo lượt, v6 mục 11.4)
    convIndex: dict[str, int] = Field(default_factory=dict)          # conversation_id -> thứ tự (id task / đề xuất)


# ---------- Đầu ra bộ trích (bản phẳng, strict) ----------


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    msg: str
    role: EvidenceRole


class Op(BaseModel):
    """Op phẳng (v6 mục 7.1): UPDATE dùng cùng trường như CREATE, trường khác null = thay đổi; danh sách dùng `_add` / `_remove`.
    Người nhận là `user_id` lấy từ <MEMBERS> hoặc dòng tin (v6 mục 5)."""

    model_config = ConfigDict(extra="forbid")
    op: Literal["CREATE", "UPDATE", "CANCEL"]
    target: Optional[str]
    anchor: Optional[str]
    kind: Optional[TaskKind]
    title: Optional[str]
    notes: Optional[str]
    notes_add: Optional[str]
    assignees: Optional[list[str]]
    assignees_add: Optional[list[str]]
    assignees_remove: Optional[list[str]]
    due_text: Optional[str]
    due_kind: Optional[DueKind]
    due_day_offset: Optional[int]
    due_weekday: Optional[int]
    due_week_offset: Optional[int]
    due_week_part: Optional[Literal["end", "start"]]
    due_abs_day: Optional[int]
    due_abs_month: Optional[int]
    due_shift_days: Optional[int]
    evidence: list[EvidenceRef]


# Thứ tự trường = thứ tự model sinh: phân loại TIN MỚI trước rồi mới ops (đã thử ops trước: recall 82% → 71%, DECISIONS #56).
ExtractKind = Literal["no_change", "new_task", "task_in_prompt"]


class ExtractOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_type: Optional[ExtractKind] = None
    ops: list[Op]


def empty_op(op: str, due: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    """Op phẳng với mọi trường = null (dùng cho FakeLLM)."""
    base: dict[str, Any] = {k: None for k in Op.model_fields}
    base.update(op=op, evidence=[])
    base.update(due or {})
    base.update(kw)
    return base


def _strict(schema: dict[str, Any]) -> dict[str, Any]:
    """Chuyển schema pydantic sang dạng strict của OpenAI: mọi trường required, không thêm trường lạ."""
    defs = schema.pop("$defs", {})

    def fix(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return fix(defs[node["$ref"].split("/")[-1]])
            # bỏ metadata "title"/"default" của schema — KHÔNG bỏ thuộc tính tên "title" trong "properties"
            node = {k: (v if k == "properties" else fix(v)) for k, v in node.items()
                    if not (k in ("title", "default") and not isinstance(v, dict))}
            if "properties" in node:
                node["properties"] = {pk: fix(pv) for pk, pv in node["properties"].items()}
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"].keys())
                node["additionalProperties"] = False
            return node
        if isinstance(node, list):
            return [fix(x) for x in node]
        return node

    return fix(schema)


EXTRACT_JSON_SCHEMA = {"name": "ledger_ops", "strict": True, "schema": _strict(ExtractOutput.model_json_schema())}
