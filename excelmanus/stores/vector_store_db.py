"""VectorStoreDB：基于 SQLite BLOB 的向量持久层。"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from excelmanus.database import Database

logger = logging.getLogger(__name__)


class VectorStoreDB:
    """SQLite 后端的向量记录存储，向量以 BLOB 形式持久化。"""

    def __init__(self, database: "Database", dimensions: int = 1536) -> None:
        self._conn = database.conn
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def size(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM vector_records"
        ).fetchone()
        return row["cnt"] if row else 0

    @staticmethod
    def _hash_text(text: str) -> str:
        normalized = " ".join((text or "").split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def has(self, text: str) -> bool:
        """检查文本是否已存在。"""
        content_hash = self._hash_text(text)
        row = self._conn.execute(
            "SELECT 1 FROM vector_records WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        return row is not None

    def add(
        self,
        text: str,
        vector: np.ndarray,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """添加一条向量记录。已存在则跳过。返回是否新增。"""
        content_hash = self._hash_text(text)
        vec_blob = vector.astype(np.float32).tobytes()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        try:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO vector_records "
                "(content_hash, text, metadata, vector, dimensions, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    content_hash,
                    text,
                    meta_json,
                    vec_blob,
                    self._dimensions,
                    self._now_iso(),
                ),
            )
            self._conn.commit()
            return cur.rowcount > 0
        except Exception:
            logger.warning("写入向量记录失败", exc_info=True)
            return False

    def add_batch(
        self,
        texts: list[str],
        vectors: np.ndarray,
        metadata_list: list[dict[str, Any]] | None = None,
    ) -> int:
        """批量添加，返回实际新增数量。"""
        added = 0
        meta_list = metadata_list or [{}] * len(texts)
        for i, text in enumerate(texts):
            vec = vectors[i] if i < vectors.shape[0] else np.zeros(self._dimensions, dtype=np.float32)
            meta = meta_list[i] if i < len(meta_list) else {}
            if self.add(text, vec, meta):
                added += 1
        return added

    def load_all(self) -> list[dict[str, Any]]:
        """加载所有记录，返回 dict 列表（含 text, metadata, vector）。"""
        rows = self._conn.execute(
            "SELECT content_hash, text, metadata, vector, dimensions "
            "FROM vector_records ORDER BY id ASC"
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            vec_blob = row["vector"]
            dims = row["dimensions"] or self._dimensions
            vec = (
                np.frombuffer(vec_blob, dtype=np.float32).copy()
                if vec_blob
                else np.zeros(dims, dtype=np.float32)
            )
            try:
                meta = json.loads(row["metadata"]) if row["metadata"] else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}
            results.append({
                "content_hash": row["content_hash"],
                "text": row["text"],
                "metadata": meta,
                "vector": vec,
            })
        return results

    def get_texts(self) -> list[str]:
        """返回所有已存储文本。"""
        rows = self._conn.execute(
            "SELECT text FROM vector_records ORDER BY id ASC"
        ).fetchall()
        return [row["text"] for row in rows]

    def get_metadata(self, text: str) -> dict[str, Any]:
        """按文本获取元数据。"""
        content_hash = self._hash_text(text)
        row = self._conn.execute(
            "SELECT metadata FROM vector_records WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if row is None:
            return {}
        try:
            return json.loads(row["metadata"]) if row["metadata"] else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def build_matrix(self) -> np.ndarray:
        """从 DB 构建向量矩阵 (N, D)。"""
        rows = self._conn.execute(
            "SELECT vector, dimensions FROM vector_records ORDER BY id ASC"
        ).fetchall()
        if not rows:
            return np.empty((0, self._dimensions), dtype=np.float32)
        vecs: list[np.ndarray] = []
        for row in rows:
            vec_blob = row["vector"]
            dims = row["dimensions"] or self._dimensions
            if vec_blob:
                vecs.append(np.frombuffer(vec_blob, dtype=np.float32).copy().reshape(1, -1))
            else:
                vecs.append(np.zeros((1, dims), dtype=np.float32))
        return np.vstack(vecs)

    def clear(self) -> None:
        """清空所有向量记录。"""
        self._conn.execute("DELETE FROM vector_records")
        self._conn.commit()
