"""Kho task (mục 2, 3, 6 tài liệu "Ngữ cảnh mới").

Mọi truy cập ĐỌC kho task của chế độ `linked_recent_search` đi qua lớp này, để sau này thay `state.tasks`
(trong bộ nhớ) bằng SQLite FTS5 / Postgres full-text mà không sửa chỗ khác. Kho to bao nhiêu cũng không tốn
token — chỉ phần được đưa vào prompt mới tốn.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional

from .models import EngineState, Member, Task
from .text import norm, terms
from .timeexpr import ts

RECENT_STATUSES = ("candidate", "draft", "approved")
BM25_K1 = 1.2
BM25_B = 0.75
PEOPLE_BONUS = 1.5     # nguoi trùng người nhận / người giao
RECENCY_BONUS = 0.3    # cộng nhẹ theo độ mới (tuyến tính về 0 ở mép search_lookup_days)


@dataclass
class SearchHit:
    task: Task
    score: float
    matched: list[str]        # từ khoá đã khớp (bỏ dấu) + "người: …" + "ngữ nghĩa 0.62"
    keyword_overlap: int      # số từ khoá truy vấn khớp với văn bản task
    people_match: bool
    similarity: Optional[float] = None  # cosine embedding truy vấn ↔ task (None ở chế độ keyword)
    people_only: bool = False           # chỉ trùng người, không khớp từ khoá nào và cosine < semantic_floor


def mentioned_at(t: Task) -> str:
    return t.last_mentioned_at or t.created_at


class TaskStore:
    def __init__(self, state: EngineState, cfg: Any = None, embedder: Any = None):
        """cfg: Config (search_mode, rrf_k, semantic_floor…); embedder: detect.embed.Embedder. Thiếu → chỉ BM25."""
        self.state = state
        self.cfg = cfg
        self.embedder = embedder
        self._texts: Optional[dict[str, str]] = None

    @property
    def mode(self) -> str:
        m = getattr(self.cfg, "search_mode", "keyword") if self.cfg is not None else "keyword"
        return m if self.embedder is not None else "keyword"

    def threshold(self, kind: str = "target") -> float:
        """Ngưỡng cosine: kind = "target" (kiểm tra target) | "dedup" (kiểm trùng)."""
        key, dflt = (("dedup_semantic_min_similarity", "default_dedup_threshold") if kind == "dedup"
                     else ("semantic_min_similarity", "default_threshold"))
        v = getattr(self.cfg, key, None) if self.cfg is not None else None
        return v if v is not None else getattr(self.embedder, dflt, 0.5)

    def query_text(self, key: str, text: str) -> str:
        """Bỏ @mention khỏi truy vấn trước khi embedding (văn bản task cũng bỏ) — tránh hai bên giống nhau chỉ vì cùng @Tên."""
        names = [n for m in self._members(key) for n in [m.display_name, *m.aliases]]
        for n in sorted(names, key=len, reverse=True):
            text = re.sub(rf"@{re.escape(n)}(?!\w)", " ", text)
        return text

    def similarity(self, query: str, tasks: list[Task]) -> dict[str, float]:
        """Cosine embedding giữa truy vấn và văn bản từng task (rỗng nếu không có embedder)."""
        if self.embedder is None or not tasks:
            return {}
        from .embed import cosine

        vecs = self.embedder.embed([query] + [self.embed_text(t) for t in tasks])
        return {t.task_id: cosine(vecs[0], v) for t, v in zip(tasks, vecs[1:])}

    # ---------- tiện ích ----------
    def _members(self, key: str) -> list[Member]:
        return self.state.members.get(key.split(";")[0], [])

    def _msg_texts(self) -> dict[str, str]:
        if self._texts is None:
            self._texts = {e.id: e.message.text for e in self.state.events if e.kind == "message" and e.message}
        return self._texts

    def _has_pending(self, t: Task) -> bool:
        return any(c.task_id == t.task_id and c.status == "pending" for c in self.state.changes)

    def _in_scope(self, t: Task, key: str, since: Optional[str]) -> bool:
        return t.conversation_key == key and (since is None or ts(mentioned_at(t)) >= ts(since))

    def document(self, t: Task) -> str:
        """Văn bản được đánh chỉ mục: tiêu đề, ghi chú, nội dung tin bằng chứng (bỏ @mention).

        Tên người nhận / người giao KHÔNG đưa vào văn bản mà khớp riêng qua `people` (trùng người → cộng điểm):
        tên bỏ dấu dễ trùng từ thường ("Tuấn" → "tuan" = "tuần", "Mai" = "mai", "Nam" = "năm")."""
        texts = self._msg_texts()
        ev = [texts[e.message_id] for e in t.evidence if e.message_id in texts]
        names = [n for m in self._members(t.conversation_key) for n in [m.display_name, *m.aliases]]
        out = " ".join([t.title, t.notes or "", *dict.fromkeys(ev)])
        for n in sorted(names, key=len, reverse=True):
            out = re.sub(rf"@{re.escape(n)}(?!\w)", " ", out)
        return out

    def embed_text(self, t: Task) -> str:
        """Văn bản để embedding: tiêu đề + ghi chú + tin giao việc (anchor), bỏ @mention.

        Không lấy mọi tin bằng chứng như BM25: tin ngắn kiểu "dạ ok anh", "dạ vâng" làm loãng vector — đo được cosine
        "weekly report" ↔ task "báo cáo tuần" tụt 0.61 → 0.48 khi gộp hết bằng chứng."""
        texts = self._msg_texts()
        names = [n for m in self._members(t.conversation_key) for n in [m.display_name, *m.aliases]]
        out = " ".join([t.title, t.notes or "", texts.get(t.anchor_message_id, "")])
        for n in sorted(names, key=len, reverse=True):
            out = re.sub(rf"@{re.escape(n)}(?!\w)", " ", out)
        return out

    def names_in_query(self, key: str, query: str) -> list[str]:
        """Tên thành viên VIẾT HOA như tên riêng trong truy vấn ("Tuấn", "Linh") → user_id.

        Phân biệt hoa thường để "báo cáo tuần" / "bao cao tuan" không bị hiểu là anh Tuấn."""
        return [m.user_id for m in self._members(key)
                if any(n[:1].isupper() and re.search(rf"(^|[^\w]){re.escape(n)}($|[^\w])", query) for n in [m.display_name, *m.aliases])]

    def resolve_people(self, key: str, people: Optional[list[str]]) -> list[str]:
        """user_id hoặc tên (bỏ dấu, bỏ @) → user_id thành viên."""
        out: list[str] = []
        members = self._members(key)
        for p in people or []:
            p = p.strip().lstrip("@")
            if p.startswith("SPEAKER:"):
                continue
            hit = next((m.user_id for m in members if m.user_id == p), None) or next(
                (m.user_id for m in members if any(norm(n) == norm(p) for n in [m.display_name, *m.aliases])), None)
            if hit and hit not in out:
                out.append(hit)
        return out

    # ---------- truy vấn ----------
    def linked(self, key: str, message_ids: list[str], since: Optional[str]) -> list[Task]:
        """Task có bằng chứng ∈ message_ids (mọi trạng thái), theo thứ tự id gặp trước, rồi task nhắc gần nhất trước."""
        order = {mid: i for i, mid in enumerate(message_ids)}
        hits: list[tuple[int, float, Task]] = []
        for t in self.state.tasks:
            if not self._in_scope(t, key, since):
                continue
            idx = [order[e.message_id] for e in t.evidence if e.message_id in order]
            if idx:
                hits.append((min(idx), -ts(mentioned_at(t)), t))
        hits.sort(key=lambda x: (x[0], x[1]))
        return [t for _, _, t in hits]

    def recent(self, key: str, n: int, exclude: set[str]) -> list[Task]:
        """candidate / draft / approved hoặc có đề xuất pending; theo last_mentioned_at giảm dần."""
        rows = [t for t in self.state.tasks if t.conversation_key == key and t.task_id not in exclude
                and (t.status in RECENT_STATUSES or self._has_pending(t))]
        rows.sort(key=lambda t: (-ts(mentioned_at(t)), -(int(t.task_id[1:]) if t.task_id[1:].isdigit() else 0)))
        return rows[: max(0, n)]

    def search(self, key: str, query: str, people: Optional[list[str]], since: Optional[str], limit: int,
               now: Optional[str] = None, lookup_days: float = 30) -> list[SearchHit]:
        """Tìm task trong kho (mọi trạng thái).

        keyword: BM25 trên token bỏ dấu + điểm cộng khi trùng người + cộng nhẹ theo độ mới.
        semantic / hybrid: Reciprocal Rank Fusion của các danh sách xếp hạng — BM25 (chỉ hybrid), cosine embedding
        (task có cosine ≥ semantic_floor) và trùng người (theo độ mới); score = Σ 1/(rrf_k + hạng)."""
        docs = [t for t in self.state.tasks if self._in_scope(t, key, since)]
        if not docs:
            return []
        tok = {t.task_id: terms(self.document(t)) for t in docs}
        avgdl = sum(len(v) for v in tok.values()) / len(docs) or 1.0
        df: Counter[str] = Counter()
        for v in tok.values():
            df.update(set(v))
        name_terms = {w for m in self._members(key) for n in [m.display_name, *m.aliases] for w in terms(n)}
        q_people = self.names_in_query(key, query)
        q = [w for w in dict.fromkeys(terms(query)) if not (q_people and w in name_terms)]
        pids = set(self.resolve_people(key, people)) | set(q_people)
        names = {m.user_id: m.display_name for m in self._members(key)}
        now_ts = ts(now) if now else max(ts(mentioned_at(t)) for t in docs)
        mode = self.mode
        sims = self.similarity(self.query_text(key, query), docs) if mode != "keyword" else {}
        floor = getattr(self.cfg, "semantic_floor", 0.35)
        rows = []
        for t in docs:
            tf = Counter(tok[t.task_id])
            dl = len(tok[t.task_id]) or 1
            bm25, matched = 0.0, []
            for w in q:
                if not tf[w]:
                    continue
                idf = math.log(1 + (len(docs) - df[w] + 0.5) / (df[w] + 0.5))
                bm25 += idf * tf[w] * (BM25_K1 + 1) / (tf[w] + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl))
                matched.append(w)
            involved = set(t.assignee_ids) | ({t.assigner_id} if t.assigner_id else set())
            pm = bool(pids & involved)
            age_days = max(0.0, (now_ts - ts(mentioned_at(t))) / 86400)
            fresh = max(0.0, 1 - age_days / max(lookup_days, 1))
            sim = sims.get(t.task_id)
            rows.append((t, bm25, matched, pm, fresh, sim))

        hits: list[SearchHit] = []
        if mode == "keyword":
            for t, bm25, matched, pm, fresh, _ in rows:
                if not matched and not pm:
                    continue
                score = bm25 + (PEOPLE_BONUS if pm else 0) + RECENCY_BONUS * fresh
                m = matched + (["người: " + ", ".join(names.get(u, u) for u in sorted(pids & (set(t.assignee_ids) | {t.assigner_id})))] if pm else [])
                hits.append(SearchHit(t, round(score, 3), m, len(matched), pm, people_only=pm and not matched))
        else:
            k = getattr(self.cfg, "rrf_k", 10)
            ranks: dict[str, float] = {}

            def add(lst: list) -> None:
                for i, r in enumerate(lst):
                    ranks[r[0].task_id] = ranks.get(r[0].task_id, 0.0) + 1.0 / (k + i + 1)

            if mode == "hybrid":
                add(sorted([r for r in rows if r[1] > 0], key=lambda r: -r[1]))
            add(sorted([r for r in rows if r[5] is not None and r[5] >= floor], key=lambda r: -r[5]))
            add(sorted([r for r in rows if r[3]], key=lambda r: -r[4]))
            for t, bm25, matched, pm, fresh, sim in rows:
                if t.task_id not in ranks:
                    continue
                m = list(matched) if mode == "hybrid" else []
                if pm:
                    m.append("người: " + ", ".join(names.get(u, u) for u in sorted(pids & (set(t.assignee_ids) | {t.assigner_id}))))
                if sim is not None and sim >= floor:
                    m.append(f"ngữ nghĩa {sim:.2f}")
                semantic = sim is not None and sim >= floor
                hits.append(SearchHit(t, round(ranks[t.task_id] + 0.001 * fresh, 4), m, len(matched), pm,
                                      None if sim is None else round(sim, 3),
                                      people_only=pm and not (matched and mode == "hybrid") and not semantic))
        hits.sort(key=lambda h: (-h.score, -ts(mentioned_at(h.task))))
        return hits[: max(0, limit)]

    # ---------- v5: ứng viên trước lần gọi 1 (context_mode = retrieve_first) ----------
    def is_matched(self, h: SearchHit) -> bool:
        """Hit vào nhóm B1 (khớp nội dung): ≥ matched_min_keyword_overlap từ khoá chung HOẶC cosine ≥ semantic_floor.
        Hit chỉ trùng người không tính (mọi việc của cùng người đều "trùng người")."""
        min_kw = getattr(self.cfg, "matched_min_keyword_overlap", 1)
        floor = getattr(self.cfg, "semantic_floor", 0.35)
        kw_ok = self.mode != "semantic" and h.keyword_overlap >= max(1, min_kw)
        return not h.people_only and (kw_ok or (h.similarity is not None and h.similarity >= floor))

    def candidates(self, key: str, query: str, people: Optional[list[str]], since: Optional[str], exclude: set[str],
                   now: Optional[str] = None) -> tuple[list[SearchHit], list[Task]]:
        """(matched, recent) cho khối CANDIDATE_TASKS (tài liệu v5 mục 3.2).

        matched: task khớp nội dung TIN MỚI, trạng thái candidate / draft / approved hoặc có đề xuất pending, không nằm
        trong `exclude` (task đã ở khối được link), tối đa `matched_tasks_max`, theo điểm giảm dần.
        recent: task nhắc gần nhất, loại task đã có, lấp cho đủ `candidate_tasks_max`."""
        cfg = self.cfg
        n_match = max(0, getattr(cfg, "matched_tasks_max", 3))
        n_total = max(0, getattr(cfg, "candidate_tasks_max", 5))
        lookup = getattr(cfg, "search_lookup_days", 30)
        n_docs = sum(1 for t in self.state.tasks if t.conversation_key == key)
        hits = self.search(key, query, people, since, n_docs, now=now, lookup_days=lookup) if query.strip() and n_match else []
        matched = [h for h in hits if h.task.task_id not in exclude and self.is_matched(h)
                   and (h.task.status in RECENT_STATUSES or self._has_pending(h.task))][: min(n_match, n_total)]
        taken = set(exclude) | {h.task.task_id for h in matched}
        recent = self.recent(key, n_total - len(matched), taken)
        return matched, recent
