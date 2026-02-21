"""向量存储：内存级向量矩阵 + JSON Lines 文件持久化。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# 向量存储文件名
_VECTORS_FILE = "vectors.jsonl"
_VECTORS_NPY_FILE = "vectors.npy"
_META_FILE = "vectors_meta.json"


@dataclass
class VectorRecord:
    """单条向量记录。"""

    content_hash: str
    text: str
    vector: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore:
    """内存级向量存储，支持 JSONL 持久化。

    设计原则：
    - 内存中维护 numpy 矩阵用于快速 cosine similarity 计算
    - 通过 content_hash 去重，避免重复向量化
    - 持久化到 JSONL 文件（每行一条记录）+ npy 文件（向量矩阵）
    - 支持增量追加，无需全量重写
    """

    def __init__(self, store_dir: str | Path, dimensions: int = 1536) -> None:
        self._store_dir = Path(store_dir).expanduser()
        self._dimensions = dimensions
        self._records: list[VectorRecord] = []
        self._hash_index: dict[str, int] = {}  # content_hash → index
        self._matrix: np.ndarray | None = None  # 缓存的向量矩阵
        self._dirty = False  # 是否有未持久化的变更

        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def size(self) -> int:
        """返回当前存储的向量数量。"""
        return len(self._records)

    @property
    def matrix(self) -> np.ndarray:
        """返回当前所有向量的矩阵 (N, D)，N=0 时返回空矩阵。"""
        if self._matrix is None or self._matrix.shape[0] != len(self._records):
            self._rebuild_matrix()
        return self._matrix  # type: ignore[return-value]

    def has(self, text: str) -> bool:
        """检查文本是否已存在于存储中。"""
        content_hash = self._hash_text(text)
        return content_hash in self._hash_index

    def add(
        self,
        text: str,
        vector: np.ndarray,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """添加一条向量记录。如果已存在（按 content_hash 去重）则跳过。

        Returns:
            True 表示新增成功，False 表示已存在被跳过。
        """
        content_hash = self._hash_text(text)
        if content_hash in self._hash_index:
            return False

        record = VectorRecord(
            content_hash=content_hash,
            text=text,
            vector=np.array(vector, dtype=np.float32),
            metadata=metadata or {},
        )
        idx = len(self._records)
        self._records.append(record)
        self._hash_index[content_hash] = idx
        self._matrix = None  # 标记矩阵需重建
        self._dirty = True
        return True

    def add_batch(
        self,
        texts: list[str],
        vectors: np.ndarray,
        metadata_list: list[dict[str, Any]] | None = None,
    ) -> int:
        """批量添加向量记录，返回实际新增的数量。"""
        added = 0
        meta_list = metadata_list or [{}] * len(texts)
        for i, text in enumerate(texts):
            vec = vectors[i] if i < vectors.shape[0] else np.zeros(self._dimensions, dtype=np.float32)
            meta = meta_list[i] if i < len(meta_list) else {}
            if self.add(text, vec, meta):
                added += 1
        return added

    def get_texts(self) -> list[str]:
        """返回所有已存储文本的列表。"""
        return [r.text for r in self._records]

    def get_record(self, index: int) -> VectorRecord | None:
        """按索引获取记录。"""
        if 0 <= index < len(self._records):
            return self._records[index]
        return None

    def get_metadata(self, index: int) -> dict[str, Any]:
        """按索引获取元数据。"""
        record = self.get_record(index)
        return record.metadata if record else {}

    def clear(self) -> None:
        """清空所有记录。"""
        self._records.clear()
        self._hash_index.clear()
        self._matrix = None
        self._dirty = True

    def save(self) -> None:
        """将内存中的数据持久化到文件。"""
        if not self._dirty and self._store_dir.exists():
            return

        self._store_dir.mkdir(parents=True, exist_ok=True)

        # 保存 JSONL 元数据
        jsonl_path = self._store_dir / _VECTORS_FILE
        lines: list[str] = []
        for record in self._records:
            entry = {
                "content_hash": record.content_hash,
                "text": record.text,
                "metadata": record.metadata,
            }
            lines.append(json.dumps(entry, ensure_ascii=False))

        self._atomic_write(jsonl_path, "\n".join(lines))

        # 保存 numpy 矩阵（二进制格式，加载更快）
        npy_path = self._store_dir / _VECTORS_NPY_FILE
        matrix = self.matrix
        np.save(str(npy_path), matrix)

        # 保存元信息
        meta_path = self._store_dir / _META_FILE
        meta = {
            "dimensions": self._dimensions,
            "count": len(self._records),
        }
        self._atomic_write(meta_path, json.dumps(meta, ensure_ascii=False))

        self._dirty = False
        logger.debug("VectorStore 已持久化: %d 条记录", len(self._records))

    def _load(self) -> None:
        """从文件加载数据到内存。"""
        jsonl_path = self._store_dir / _VECTORS_FILE
        npy_path = self._store_dir / _VECTORS_NPY_FILE

        if not jsonl_path.exists():
            return

        try:
            text = jsonl_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("读取向量存储文件失败: %s", jsonl_path, exc_info=True)
            return

        entries: list[dict[str, Any]] = []
        for line_num, line in enumerate(text.strip().split("\n"), 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("向量存储 JSONL 第 %d 行解析失败，已跳过", line_num)

        if not entries:
            return

        # 尝试加载 npy 矩阵
        vectors: np.ndarray | None = None
        if npy_path.exists():
            try:
                vectors = np.load(str(npy_path))
                if vectors.shape[0] != len(entries):
                    logger.warning(
                        "向量矩阵与元数据数量不匹配 (%d vs %d)，将丢弃向量缓存",
                        vectors.shape[0],
                        len(entries),
                    )
                    vectors = None
            except Exception:
                logger.warning("加载向量 npy 文件失败", exc_info=True)
                vectors = None

        for i, entry in enumerate(entries):
            content_hash = entry.get("content_hash", "")
            entry_text = entry.get("text", "")
            metadata = entry.get("metadata", {})

            if not content_hash or not entry_text:
                continue

            vec = (
                vectors[i]
                if vectors is not None and i < vectors.shape[0]
                else np.zeros(self._dimensions, dtype=np.float32)
            )

            record = VectorRecord(
                content_hash=content_hash,
                text=entry_text,
                vector=vec,
                metadata=metadata,
            )
            idx = len(self._records)
            self._records.append(record)
            self._hash_index[content_hash] = idx

        self._matrix = None  # 延迟重建
        logger.debug("VectorStore 已加载: %d 条记录", len(self._records))

    def _rebuild_matrix(self) -> None:
        """从 records 重建 numpy 矩阵。"""
        if not self._records:
            self._matrix = np.empty((0, self._dimensions), dtype=np.float32)
            return
        self._matrix = np.vstack(
            [r.vector.reshape(1, -1) for r in self._records]
        ).astype(np.float32)

    @staticmethod
    def _hash_text(text: str) -> str:
        """计算文本的 SHA-256 短哈希（16 字符）。"""
        normalized = " ".join((text or "").split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _atomic_write(filepath: Path, content: str) -> None:
        """原子写入文件。"""
        parent = filepath.parent
        fd, tmp_path = tempfile.mkstemp(dir=str(parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
                tmp_f.write(content)
                tmp_f.flush()
                os.fsync(tmp_f.fileno())
            os.replace(tmp_path, str(filepath))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
