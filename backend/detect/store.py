"""Kho task: task được link, task nhắc gần nhất, độ gần nghĩa (embedding), chọn ba tầng cho prompt (v6 mục 6).

Mọi truy cập ĐỌC kho task đi qua lớp này, để sau này thay `state.tasks` (trong bộ nhớ) bằng DB mà không sửa chỗ khác.
Không còn BM25 / RRF / danh sách từ tay: mỗi hội thoại chỉ có vài chục task, xếp hạng bằng độ mới + cosine là đủ.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import EngineState, Member, Task
from .timeexpr import ts

OPEN_FOR_PROMPT = ("candidate", "draft", "approved")


def mentioned_at(t: Task) -> str:
    return t.last_mentioned_at or t.created_at


@dataclass
class Tiers:
    linked: list[Task]
    details: list[tuple[Task, str]]          # (task, nguồn: recent | similar)
    index: list[tuple[Task, str]]            # (task, nguồn: recent | overflow)
    index_total: int = 0                     # số task đủ điều kiện vào index trước khi cắt
    sims: dict[str, float] = field(default_factory=dict)


class TaskStore:
    def __init__(self, state: EngineState, cfg: Any = None, embedder: Any = None):
        """cfg: Config; embedder: detect.embed.Embedder. Thiếu embedder (unit test) → chỉ xếp theo độ mới."""
        self.state = state
        self.cfg = cfg
        self.embedder = embedder
        self._texts: Optional[dict[str, str]] = None

    # ---------- tiện ích ----------
    def _members(self, key: str) -> list[Member]:
        return self.state.members.get(key.split(";")[0], [])

    def _msg_texts(self) -> dict[str, str]:
        if self._texts is None:
            self._texts = {e.id: e.message.text for e in self.state.events if e.message}
        return self._texts

    def _has_pending(self, t: Task) -> bool:
        return any(c.task_id == t.task_id and c.status == "pending" for c in self.state.changes)

    def strip_mentions(self, key: str, text: str) -> str:
        """Bỏ @mention (hai phía văn bản đem embedding) — tránh giống nhau chỉ vì cùng @Tên."""
        names = [n for m in self._members(key) for n in [m.display_name, *m.aliases]]
        for n in sorted(names, key=len, reverse=True):
            text = re.sub(rf"@{re.escape(n)}(?!\w)", " ", text)
        return re.sub(r"\s{2,}", " ", text).strip()

    def embed_text(self, t: Task) -> str:
        """Văn bản để embedding: tiêu đề + ghi chú + tin giao việc (anchor), bỏ @mention.

        Không gộp mọi tin bằng chứng: tin ngắn kiểu "dạ ok anh" làm loãng vector (DECISIONS #41)."""
        texts = self._msg_texts()
        return self.strip_mentions(t.conversation_key, " ".join([t.title, t.notes or "", texts.get(t.anchor_message_id, "")]))

    def similarity(self, query: str, tasks: list[Task]) -> dict[str, float]:
        """Cosine embedding giữa truy vấn và văn bản từng task (rỗng nếu không có embedder / truy vấn rỗng)."""
        if self.embedder is None or not tasks or not query.strip():
            return {}
        from .embed import cosine

        vecs = self.embedder.embed([query] + [self.embed_text(t) for t in tasks])
        return {t.task_id: cosine(vecs[0], v) for t, v in zip(tasks, vecs[1:])}

    # ---------- truy vấn ----------
    def linked(self, key: str, message_ids: list[str], since: Optional[str]) -> list[Task]:
        """Task có bằng chứng ∈ message_ids (mọi trạng thái), theo thứ tự id gặp trước, rồi task nhắc gần nhất trước."""
        order = {mid: i for i, mid in enumerate(message_ids)}
        hits: list[tuple[int, float, Task]] = []
        for t in self.state.tasks:
            if t.conversation_key != key or (since is not None and ts(mentioned_at(t)) < ts(since)):
                continue
            idx = [order[e.message_id] for e in t.evidence if e.message_id in order]
            if idx:
                hits.append((min(idx), -ts(mentioned_at(t)), t))
        hits.sort(key=lambda x: (x[0], x[1]))
        return [t for _, _, t in hits]

    def is_open(self, t: Task) -> bool:
        return t.status in OPEN_FOR_PROMPT or self._has_pending(t)

    def recent(self, key: str, exclude: set[str], now: Optional[str] = None) -> list[Task]:
        """Task đang mở (candidate / draft / approved hoặc có đề xuất pending), nhắc gần nhất trước;
        cùng thời điểm thì task tạo sau trước (theo lượt tạo, không theo số trong task_id)."""
        rows = [t for t in self.state.tasks if t.conversation_key == key and t.task_id not in exclude and self.is_open(t)]
        rows.sort(key=lambda t: (-ts(mentioned_at(t)), -t.created_turn, t.task_id))
        return rows

    def declined_recent(self, key: str, exclude: set[str], now: str, days: float) -> list[Task]:
        """Task bị từ chối còn trong `declined_suppress_days` (để model nhận ra, không tạo lại)."""
        return [t for t in self.state.tasks if t.conversation_key == key and t.task_id not in exclude
                and t.status == "declined" and ts(now) - ts(t.status_changed_at) <= days * 86400]

    def tiers(self, key: str, linked: list[Task], query: str, now: str) -> Tiers:
        """Ba tầng (v6 mục 6): <LINKED_TASKS> (đưa vào) · <TASK_DETAILS> (recent + similar) · <TASK_INDEX> (phần còn lại).

        Mỗi task ở đúng một tầng — tầng cao nhất mà nó thuộc về. Cosine chỉ dùng để XẾP HẠNG, không có ngưỡng (DECISIONS #41)."""
        cfg = self.cfg
        taken = {t.task_id for t in linked}
        open_rows = self.recent(key, taken, now)
        dec = self.declined_recent(key, taken, now, getattr(cfg, "declined_suppress_days", 7))
        sims = self.similarity(query, open_rows + dec)
        details: list[tuple[Task, str]] = [(t, "recent") for t in open_rows[: max(0, getattr(cfg, "detail_recent_max", 3))]]
        taken |= {t.task_id for t, _ in details}
        if sims:
            rest = sorted((t for t in open_rows if t.task_id not in taken), key=lambda t: -sims.get(t.task_id, -1.0))
            details += [(t, "similar") for t in rest[: max(0, getattr(cfg, "detail_similar_max", 2))]]
            taken |= {t.task_id for t, _ in details}
        pool = [t for t in open_rows if t.task_id not in taken] + [t for t in dec if t.task_id not in taken]
        pool.sort(key=lambda t: (-ts(mentioned_at(t)), -t.created_turn, t.task_id))
        n_max = max(0, getattr(cfg, "index_tasks_max", 30))
        if len(pool) <= n_max:
            index = [(t, "recent") for t in pool]
        else:
            n_over = min(max(0, getattr(cfg, "index_overflow_max", 5)), n_max) if sims else 0
            head = pool[: n_max - n_over]
            rest = sorted(pool[n_max - n_over:], key=lambda t: -sims.get(t.task_id, -1.0))
            index = [(t, "recent") for t in head] + [(t, "overflow") for t in rest[:n_over]]
        return Tiers(linked, details, index, len(pool), sims)
