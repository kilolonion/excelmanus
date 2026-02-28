"""PlaybookStore — Playbook Bullet 持久化存储（SQLite）。

提供 CRUD、语义检索、去重合并、生命周期管理。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Bullet 内容最大长度
_MAX_CONTENT_LENGTH = 500
# 语义去重阈值
DEDUP_SIMILARITY_THRESHOLD = 0.92


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PlaybookBullet:
    """单条战术手册条目。"""

    id: str
    category: str  # "cross_sheet" | "formatting" | "formula" | "data_cleaning" | "error_recovery" | "general"
    content: str
    source_task_tags: list[str] = field(default_factory=list)
    helpful_count: int = 0
    harmful_count: int = 0
    embedding: np.ndarray | None = None  # shape (D,)
    created_at: str = ""
    last_used_at: str | None = None
    origin_session_id: str = ""
    origin_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（不含 embedding）。"""
        return {
            "id": self.id,
            "category": self.category,
            "content": self.content,
            "source_task_tags": self.source_task_tags,
            "helpful_count": self.helpful_count,
            "harmful_count": self.harmful_count,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "origin_session_id": self.origin_session_id,
            "origin_summary": self.origin_summary,
        }


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS playbook_bullets (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    source_task_tags TEXT DEFAULT '[]',
    helpful_count INTEGER DEFAULT 0,
    harmful_count INTEGER DEFAULT 0,
    embedding BLOB,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    origin_session_id TEXT DEFAULT '',
    origin_summary TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_playbook_category ON playbook_bullets(category);
CREATE INDEX IF NOT EXISTS idx_playbook_helpful ON playbook_bullets(helpful_count DESC);
"""


class PlaybookStore:
    """Playbook 持久化存储（SQLite）。"""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_schema(self) -> None:
        """确保 DB 表结构存在。"""
        conn = self._get_conn()
        conn.executescript(_SCHEMA_SQL)
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── CRUD ─────────────────────────────────────────────────

    def add(self, bullet: PlaybookBullet) -> str:
        """新增条目，返回 ID。"""
        if not bullet.id:
            bullet.id = uuid.uuid4().hex[:16]
        if not bullet.created_at:
            bullet.created_at = _utc_now_iso()

        content = bullet.content[:_MAX_CONTENT_LENGTH]
        embedding_blob = self._encode_embedding(bullet.embedding)

        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO playbook_bullets
               (id, category, content, source_task_tags, helpful_count, harmful_count,
                embedding, created_at, last_used_at, origin_session_id, origin_summary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bullet.id,
                bullet.category,
                content,
                json.dumps(bullet.source_task_tags, ensure_ascii=False),
                bullet.helpful_count,
                bullet.harmful_count,
                embedding_blob,
                bullet.created_at,
                bullet.last_used_at,
                bullet.origin_session_id,
                bullet.origin_summary[:200] if bullet.origin_summary else "",
            ),
        )
        conn.commit()
        return bullet.id

    def get(self, bullet_id: str) -> PlaybookBullet | None:
        """按 ID 获取条目。"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM playbook_bullets WHERE id = ?", (bullet_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_bullet(row)

    def delete(self, bullet_id: str) -> bool:
        """删除指定条目。返回是否存在并删除。"""
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM playbook_bullets WHERE id = ?", (bullet_id,)
        )
        conn.commit()
        return cursor.rowcount > 0

    def list_all(self, limit: int = 100) -> list[PlaybookBullet]:
        """列出所有条目（按 helpful_count 降序）。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM playbook_bullets ORDER BY helpful_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_bullet(row) for row in rows]

    def count(self) -> int:
        """返回条目总数。"""
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) FROM playbook_bullets").fetchone()
        return row[0] if row else 0

    # ── 评分 ─────────────────────────────────────────────────

    def mark_helpful(self, bullet_id: str) -> None:
        """标记条目为有用。"""
        conn = self._get_conn()
        conn.execute(
            "UPDATE playbook_bullets SET helpful_count = helpful_count + 1, last_used_at = ? WHERE id = ?",
            (_utc_now_iso(), bullet_id),
        )
        conn.commit()

    def update_content(self, bullet_id: str, new_content: str) -> None:
        """更新条目内容（用于合并时选择更精炼的版本）。"""
        conn = self._get_conn()
        conn.execute(
            "UPDATE playbook_bullets SET content = ? WHERE id = ?",
            (new_content[:_MAX_CONTENT_LENGTH], bullet_id),
        )
        conn.commit()

    def mark_harmful(self, bullet_id: str) -> None:
        """标记条目为有害。"""
        conn = self._get_conn()
        conn.execute(
            "UPDATE playbook_bullets SET harmful_count = harmful_count + 1 WHERE id = ?",
            (bullet_id,),
        )
        conn.commit()

    # ── 语义检索 ─────────────────────────────────────────────

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 5,
        category: str | None = None,
        min_score: float = 0.3,
    ) -> list[PlaybookBullet]:
        """语义检索 top-k 相关条目。"""
        from excelmanus.embedding.search import cosine_top_k

        conn = self._get_conn()
        if category:
            rows = conn.execute(
                "SELECT * FROM playbook_bullets WHERE category = ? AND embedding IS NOT NULL",
                (category,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM playbook_bullets WHERE embedding IS NOT NULL"
            ).fetchall()

        if not rows:
            return []

        bullets = [self._row_to_bullet(row) for row in rows]
        embeddings = []
        valid_bullets = []
        for b in bullets:
            if b.embedding is not None and b.embedding.shape[0] > 0:
                embeddings.append(b.embedding)
                valid_bullets.append(b)

        if not embeddings:
            return []

        corpus = np.stack(embeddings)
        results = cosine_top_k(query_embedding, corpus, k=top_k, threshold=min_score)

        return [valid_bullets[r.index] for r in results]

    def find_similar(
        self,
        embedding: np.ndarray,
        threshold: float = DEDUP_SIMILARITY_THRESHOLD,
    ) -> PlaybookBullet | None:
        """查找语义最相似的已有条目（用于去重）。"""
        results = self.search(embedding, top_k=1, min_score=threshold)
        return results[0] if results else None

    # ── 清理 ─────────────────────────────────────────────────

    def prune(
        self,
        max_age_days: int = 90,
        min_helpful_ratio: float = 0.3,
        max_bullets: int = 500,
    ) -> int:
        """清理低质量/过期条目。返回清理数量。"""
        conn = self._get_conn()
        total_pruned = 0

        # 1. 删除有害 > 有用的条目
        cursor = conn.execute(
            "DELETE FROM playbook_bullets WHERE harmful_count > helpful_count AND harmful_count > 0"
        )
        total_pruned += cursor.rowcount

        # 2. 删除超过 max_age_days 未使用的
        if max_age_days > 0:
            from datetime import timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
            cursor = conn.execute(
                """DELETE FROM playbook_bullets
                   WHERE last_used_at IS NOT NULL AND last_used_at < ?
                   AND helpful_count <= 1""",
                (cutoff,),
            )
            total_pruned += cursor.rowcount

        # 3. 超出上限时按 helpful_count 最低的淘汰
        current_count = self.count()
        if current_count > max_bullets:
            excess = current_count - max_bullets
            cursor = conn.execute(
                """DELETE FROM playbook_bullets WHERE id IN (
                     SELECT id FROM playbook_bullets
                     ORDER BY helpful_count ASC, created_at ASC
                     LIMIT ?
                   )""",
                (excess,),
            )
            total_pruned += cursor.rowcount

        conn.commit()
        return total_pruned

    def clear(self) -> int:
        """清空所有条目。返回删除数量。"""
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM playbook_bullets")
        conn.commit()
        return cursor.rowcount

    def stats(self) -> dict[str, Any]:
        """返回统计信息。"""
        conn = self._get_conn()
        total = self.count()
        if total == 0:
            return {"total": 0, "categories": {}, "avg_helpful": 0.0}

        rows = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM playbook_bullets GROUP BY category"
        ).fetchall()
        categories = {row["category"]: row["cnt"] for row in rows}

        avg_row = conn.execute(
            "SELECT AVG(helpful_count) as avg_h FROM playbook_bullets"
        ).fetchone()
        avg_helpful = float(avg_row["avg_h"]) if avg_row and avg_row["avg_h"] else 0.0

        return {
            "total": total,
            "categories": categories,
            "avg_helpful": round(avg_helpful, 2),
        }

    # ── 辅助 ─────────────────────────────────────────────────

    @staticmethod
    def _encode_embedding(embedding: np.ndarray | None) -> bytes | None:
        if embedding is None:
            return None
        return embedding.astype(np.float32).tobytes()

    @staticmethod
    def _decode_embedding(blob: bytes | None, dimensions: int = 0) -> np.ndarray | None:
        if blob is None or len(blob) == 0:
            return None
        arr = np.frombuffer(blob, dtype=np.float32)
        return arr.copy()  # copy to make writable

    def _row_to_bullet(self, row: sqlite3.Row) -> PlaybookBullet:
        tags_raw = row["source_task_tags"]
        try:
            tags = json.loads(tags_raw) if tags_raw else []
        except (json.JSONDecodeError, TypeError):
            tags = []

        return PlaybookBullet(
            id=row["id"],
            category=row["category"],
            content=row["content"],
            source_task_tags=tags,
            helpful_count=row["helpful_count"],
            harmful_count=row["harmful_count"],
            embedding=self._decode_embedding(row["embedding"]),
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
            origin_session_id=row["origin_session_id"] or "",
            origin_summary=row["origin_summary"] or "",
        )
