"""SessionSummaryStore — 会话摘要持久化存储（SQLite / PostgreSQL）。

提供 CRUD、按 user_id 查询、语义检索（embedding）、文件名匹配等能力。
Schema 由 Database 迁移系统统一管理（migration 20）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import numpy as np

from excelmanus.db_adapter import ConnectionAdapter

if TYPE_CHECKING:
    from excelmanus.database import Database

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionSummary:
    """单条会话摘要的数据对象。"""

    __slots__ = (
        "session_id",
        "user_id",
        "summary_text",
        "task_goal",
        "files_involved",
        "outcome",
        "unfinished",
        "embedding",
        "token_count",
        "created_at",
        "updated_at",
    )

    def __init__(
        self,
        session_id: str,
        summary_text: str,
        *,
        user_id: str | None = None,
        task_goal: str = "",
        files_involved: list[str] | None = None,
        outcome: str = "",
        unfinished: str = "",
        embedding: np.ndarray | None = None,
        token_count: int = 0,
        created_at: str = "",
        updated_at: str = "",
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.summary_text = summary_text
        self.task_goal = task_goal
        self.files_involved = files_involved or []
        self.outcome = outcome
        self.unfinished = unfinished
        self.embedding = embedding
        self.token_count = token_count
        self.created_at = created_at or _utc_now_iso()
        self.updated_at = updated_at or self.created_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "summary_text": self.summary_text,
            "task_goal": self.task_goal,
            "files_involved": self.files_involved,
            "outcome": self.outcome,
            "unfinished": self.unfinished,
            "token_count": self.token_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SessionSummaryStore:
    """会话摘要持久化存储。

    通过 Database 实例或 ConnectionAdapter 创建——表结构由 Database 迁移管理。
    """

    def __init__(self, conn: Any, *, user_id: str | None = None) -> None:
        if isinstance(conn, ConnectionAdapter):
            self._conn = conn
        else:
            # Database 实例
            self._conn = conn.conn
        self._user_id = user_id

    def _has_table(self) -> bool:
        return self._conn.table_exists("session_summaries")

    # ── CRUD ─────────────────────────────────────────────

    def upsert(self, summary: SessionSummary) -> None:
        """插入或更新会话摘要（按 session_id 去重）。"""
        if not self._has_table():
            return
        now = _utc_now_iso()
        files_json = json.dumps(summary.files_involved, ensure_ascii=False)
        embedding_blob = self._encode_embedding(summary.embedding)
        self._conn.execute(
            """INSERT INTO session_summaries
               (session_id, user_id, summary_text, task_goal, files_involved,
                outcome, unfinished, embedding, token_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                 summary_text = EXCLUDED.summary_text,
                 task_goal = EXCLUDED.task_goal,
                 files_involved = EXCLUDED.files_involved,
                 outcome = EXCLUDED.outcome,
                 unfinished = EXCLUDED.unfinished,
                 embedding = EXCLUDED.embedding,
                 token_count = EXCLUDED.token_count,
                 updated_at = EXCLUDED.updated_at""",
            (
                summary.session_id,
                summary.user_id,
                summary.summary_text,
                summary.task_goal,
                files_json,
                summary.outcome,
                summary.unfinished,
                embedding_blob,
                summary.token_count,
                summary.created_at or now,
                now,
            ),
        )
        self._conn.commit()

    def get_by_session(self, session_id: str) -> SessionSummary | None:
        """按 session_id 获取摘要。"""
        if not self._has_table():
            return None
        row = self._conn.execute(
            "SELECT * FROM session_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_summary(row)

    def delete(self, session_id: str) -> bool:
        """删除指定会话摘要。"""
        if not self._has_table():
            return False
        cur = self._conn.execute(
            "DELETE FROM session_summaries WHERE session_id = ?",
            (session_id,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ── 查询 ─────────────────────────────────────────────

    def list_recent(
        self,
        *,
        user_id: str | None = None,
        limit: int = 10,
    ) -> list[SessionSummary]:
        """按时间倒序列出最近的会话摘要。"""
        if not self._has_table():
            return []
        effective_uid = user_id if user_id is not None else self._user_id
        if effective_uid is not None:
            rows = self._conn.execute(
                "SELECT * FROM session_summaries WHERE user_id = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (effective_uid, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM session_summaries ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_summary(r) for r in rows]

    def search_by_files(
        self,
        file_paths: list[str],
        *,
        user_id: str | None = None,
        limit: int = 5,
    ) -> list[SessionSummary]:
        """按文件路径匹配历史摘要（files_involved 中包含任意一个目标文件名）。"""
        if not self._has_table() or not file_paths:
            return []
        effective_uid = user_id if user_id is not None else self._user_id
        # 提取文件名（不含路径前缀）用于模糊匹配
        import os
        basenames = {os.path.basename(p).lower() for p in file_paths if p}
        if not basenames:
            return []
        # 加载候选摘要后在 Python 侧筛选（避免复杂 SQL JSON 查询）
        candidates = self.list_recent(user_id=effective_uid, limit=50)
        matched: list[SessionSummary] = []
        for s in candidates:
            for f in s.files_involved:
                if os.path.basename(f).lower() in basenames:
                    matched.append(s)
                    break
            if len(matched) >= limit:
                break
        return matched

    def search_by_embedding(
        self,
        query_embedding: np.ndarray,
        *,
        user_id: str | None = None,
        top_k: int = 3,
        min_score: float = 0.25,
    ) -> list[tuple[SessionSummary, float]]:
        """语义检索历史摘要，返回 (summary, score) 列表。"""
        if not self._has_table():
            return []
        from excelmanus.embedding.search import cosine_top_k

        effective_uid = user_id if user_id is not None else self._user_id
        if effective_uid is not None:
            rows = self._conn.execute(
                "SELECT * FROM session_summaries "
                "WHERE user_id = ? AND embedding IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT 50",
                (effective_uid,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM session_summaries "
                "WHERE embedding IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT 50",
            ).fetchall()

        if not rows:
            return []

        summaries: list[SessionSummary] = []
        embeddings: list[np.ndarray] = []
        for row in rows:
            s = self._row_to_summary(row)
            if s.embedding is not None and s.embedding.shape[0] > 0:
                summaries.append(s)
                embeddings.append(s.embedding)

        if not embeddings:
            return []

        corpus = np.stack(embeddings)
        results = cosine_top_k(query_embedding, corpus, k=top_k, threshold=min_score)
        return [(summaries[r.index], r.score) for r in results]

    def count(self, *, user_id: str | None = None) -> int:
        """返回摘要总数。"""
        if not self._has_table():
            return 0
        effective_uid = user_id if user_id is not None else self._user_id
        if effective_uid is not None:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM session_summaries WHERE user_id = ?",
                (effective_uid,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM session_summaries",
            ).fetchone()
        return row[0] if row else 0

    # ── 辅助 ─────────────────────────────────────────────

    @staticmethod
    def _encode_embedding(embedding: np.ndarray | None) -> bytes | None:
        if embedding is None:
            return None
        return embedding.astype(np.float32).tobytes()

    @staticmethod
    def _decode_embedding(blob: bytes | None) -> np.ndarray | None:
        if blob is None or len(blob) == 0:
            return None
        arr = np.frombuffer(blob, dtype=np.float32)
        return arr.copy()

    def _row_to_summary(self, row: Any) -> SessionSummary:
        files_raw = row["files_involved"] if hasattr(row, "__getitem__") else ""
        try:
            files = json.loads(files_raw) if files_raw else []
        except (json.JSONDecodeError, TypeError):
            files = []

        embedding_blob = row["embedding"] if hasattr(row, "__getitem__") else None
        return SessionSummary(
            session_id=row["session_id"],
            user_id=row["user_id"] if hasattr(row, "__getitem__") else None,
            summary_text=row["summary_text"],
            task_goal=row["task_goal"] or "",
            files_involved=files,
            outcome=row["outcome"] or "",
            unfinished=row["unfinished"] or "",
            embedding=self._decode_embedding(embedding_blob),
            token_count=row["token_count"] if hasattr(row, "__getitem__") else 0,
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )
