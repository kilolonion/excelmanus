"""统一 SQLite 连接管理与 schema 迁移。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# 各版本的增量 DDL。key = 版本号，value = SQL 语句列表。
_MIGRATIONS: dict[int, list[str]] = {
    1: [
        # ── 对话历史（与 chat_history.py 保持一致） ──
        """CREATE TABLE IF NOT EXISTS sessions (
            id            TEXT PRIMARY KEY,
            title         TEXT NOT NULL DEFAULT '',
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            message_count INTEGER DEFAULT 0,
            status        TEXT DEFAULT 'active'
        )""",
        """CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role        TEXT NOT NULL,
            content     TEXT,
            turn_number INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)",
        # ── 持久记忆 ──
        """CREATE TABLE IF NOT EXISTS memory_entries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            category     TEXT NOT NULL,
            content      TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            source       TEXT DEFAULT '',
            created_at   TEXT NOT NULL,
            UNIQUE(category, content_hash)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_memory_category ON memory_entries(category)",
        "CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_entries(created_at)",
        # ── 向量记录 ──
        """CREATE TABLE IF NOT EXISTS vector_records (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            content_hash TEXT UNIQUE NOT NULL,
            text         TEXT NOT NULL,
            metadata     TEXT,
            vector       BLOB,
            dimensions   INTEGER NOT NULL DEFAULT 1536,
            created_at   TEXT NOT NULL
        )""",
        # ── 审批审计 ──
        """CREATE TABLE IF NOT EXISTS approvals (
            id               TEXT PRIMARY KEY,
            tool_name        TEXT NOT NULL,
            arguments        TEXT NOT NULL,
            tool_scope       TEXT DEFAULT '[]',
            created_at_utc   TEXT NOT NULL,
            applied_at_utc   TEXT,
            execution_status TEXT DEFAULT 'pending',
            undoable         INTEGER DEFAULT 0,
            result_preview   TEXT,
            error_type       TEXT,
            error_message    TEXT,
            partial_scan     INTEGER DEFAULT 0,
            audit_dir        TEXT,
            manifest_file    TEXT,
            patch_file       TEXT,
            repo_diff_before TEXT,
            repo_diff_after  TEXT,
            changes          TEXT,
            binary_snapshots TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(execution_status)",
        "CREATE INDEX IF NOT EXISTS idx_approvals_created ON approvals(created_at_utc)",
    ],
}

_LATEST_VERSION = max(_MIGRATIONS.keys())


class Database:
    """统一 SQLite 连接管理，支持增量 schema 迁移。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema_version_table()
        self._migrate()

    @property
    def conn(self) -> sqlite3.Connection:
        """返回底层 SQLite 连接。"""
        return self._conn

    @property
    def db_path(self) -> str:
        """返回数据库文件路径。"""
        return self._db_path

    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()

    def _ensure_schema_version_table(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        self._conn.commit()

    def _current_version(self) -> int:
        row = self._conn.execute(
            "SELECT MAX(version) as v FROM schema_version"
        ).fetchone()
        return row["v"] if row["v"] is not None else 0

    def _migrate(self) -> None:
        current = self._current_version()
        if current >= _LATEST_VERSION:
            return
        for version in range(current + 1, _LATEST_VERSION + 1):
            statements = _MIGRATIONS.get(version, [])
            for sql in statements:
                self._conn.execute(sql)
            self._conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (version,)
            )
            self._conn.commit()
            logger.info("数据库 schema 迁移到 v%d", version)


# ── 旧数据迁移工具 ──────────────────────────────────────────

_ENTRY_HEADER_RE = re.compile(
    r"^###\s+\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})]\s+(\S+)\s*$"
)
_TIMESTAMP_FMT = "%Y-%m-%d %H:%M"


def _hash_content(text: str) -> str:
    normalized = " ".join((text or "").split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def migrate_legacy_data(
    db: Database,
    *,
    memory_dir: str | None = None,
    vectors_dir: str | None = None,
    audit_dir: str | None = None,
    old_chat_db_path: str | None = None,
) -> None:
    """从旧文件格式迁移数据到统一 SQLite 数据库。

    各参数均可选，仅传入需要迁移的路径。迁移是幂等的（通过 UNIQUE 约束去重）。
    """
    conn = db.conn

    # 1. 迁移旧 chat_history.db 中的 sessions/messages
    if old_chat_db_path and Path(old_chat_db_path).exists():
        _migrate_chat_history(conn, old_chat_db_path)

    # 2. 迁移 Markdown 记忆文件
    if memory_dir:
        _migrate_memory_files(conn, memory_dir)

    # 3. 迁移 JSONL + npy 向量文件
    if vectors_dir:
        _migrate_vector_files(conn, vectors_dir)

    # 4. 迁移 manifest.json 审批文件
    if audit_dir:
        _migrate_approval_manifests(conn, audit_dir)


def _migrate_chat_history(conn: sqlite3.Connection, old_db_path: str) -> None:
    """从旧 chat_history.db 复制 sessions 和 messages 数据。"""
    try:
        old_conn = sqlite3.connect(old_db_path)
        old_conn.row_factory = sqlite3.Row

        # 迁移 sessions
        for row in old_conn.execute("SELECT * FROM sessions").fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO sessions "
                "(id, title, created_at, updated_at, message_count, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row["id"],
                    row["title"],
                    row["created_at"],
                    row["updated_at"],
                    row["message_count"],
                    row["status"],
                ),
            )

        # 迁移 messages
        for row in old_conn.execute("SELECT * FROM messages").fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO messages "
                "(id, session_id, role, content, turn_number, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row["id"],
                    row["session_id"],
                    row["role"],
                    row["content"],
                    row["turn_number"],
                    row["created_at"],
                ),
            )

        conn.commit()
        old_conn.close()
        logger.info("已从旧 chat_history.db 迁移会话数据")
    except Exception:
        logger.warning("迁移旧 chat_history.db 失败", exc_info=True)


def _migrate_memory_files(conn: sqlite3.Connection, memory_dir: str) -> None:
    """从 Markdown 记忆文件迁移到 memory_entries 表。"""
    mem_path = Path(memory_dir)
    if not mem_path.exists():
        return

    md_files = sorted(mem_path.glob("*.md"))
    if not md_files:
        return

    migrated = 0
    for md_file in md_files:
        try:
            content = md_file.read_text(encoding="utf-8")
        except OSError:
            continue

        entries = _parse_markdown_entries(content)
        for entry in entries:
            content_hash = _hash_content(entry["content"])
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO memory_entries "
                    "(category, content, content_hash, source, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        entry["category"],
                        entry["content"],
                        content_hash,
                        f"migrated:{md_file.name}",
                        entry["created_at"],
                    ),
                )
                migrated += 1
            except Exception:
                pass

    conn.commit()
    if migrated:
        logger.info("已从 Markdown 文件迁移 %d 条记忆条目", migrated)


def _parse_markdown_entries(content: str) -> list[dict[str, str]]:
    """解析 Markdown 格式的记忆文件为条目列表。"""
    if not content or not content.strip():
        return []

    entries: list[dict[str, str]] = []
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        match = _ENTRY_HEADER_RE.match(lines[i])
        if not match:
            i += 1
            continue

        ts_str, cat_str = match.group(1), match.group(2)
        try:
            timestamp = datetime.strptime(ts_str, _TIMESTAMP_FMT)
        except ValueError:
            i += 1
            continue

        i += 1
        body_lines: list[str] = []
        while i < len(lines):
            if lines[i].strip() == "---":
                i += 1
                break
            body_lines.append(lines[i])
            i += 1

        body = "\n".join(body_lines).strip()
        if not body:
            continue

        entries.append({
            "category": cat_str,
            "content": body,
            "created_at": timestamp.isoformat(),
        })

    return entries


def _migrate_vector_files(conn: sqlite3.Connection, vectors_dir: str) -> None:
    """从 JSONL + npy 向量文件迁移到 vector_records 表。"""
    vec_path = Path(vectors_dir)
    jsonl_path = vec_path / "vectors.jsonl"
    npy_path = vec_path / "vectors.npy"

    if not jsonl_path.exists():
        return

    try:
        text = jsonl_path.read_text(encoding="utf-8")
    except OSError:
        return

    records: list[dict[str, Any]] = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not records:
        return

    # 尝试加载向量矩阵
    vectors: np.ndarray | None = None
    if npy_path.exists():
        try:
            vectors = np.load(str(npy_path))
            if vectors.shape[0] != len(records):
                vectors = None
        except Exception:
            vectors = None

    from datetime import timezone as _tz
    now_iso = datetime.now(tz=_tz.utc).isoformat()
    migrated = 0
    for i, rec in enumerate(records):
        content_hash = rec.get("content_hash", "")
        entry_text = rec.get("text", "")
        metadata = rec.get("metadata", {})

        if not content_hash or not entry_text:
            continue

        vec_blob: bytes | None = None
        dimensions = 0
        if vectors is not None and i < vectors.shape[0]:
            vec = vectors[i].astype(np.float32)
            vec_blob = vec.tobytes()
            dimensions = vec.shape[0]

        try:
            conn.execute(
                "INSERT OR IGNORE INTO vector_records "
                "(content_hash, text, metadata, vector, dimensions, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    content_hash,
                    entry_text,
                    json.dumps(metadata, ensure_ascii=False),
                    vec_blob,
                    dimensions,
                    now_iso,
                ),
            )
            migrated += 1
        except Exception:
            pass

    conn.commit()
    if migrated:
        logger.info("已从 JSONL 文件迁移 %d 条向量记录", migrated)


def _migrate_approval_manifests(conn: sqlite3.Connection, audit_dir: str) -> None:
    """从 manifest.json 审批文件迁移到 approvals 表。"""
    audit_path = Path(audit_dir)
    if not audit_path.exists():
        return

    migrated = 0
    for manifest_file in audit_path.rglob("manifest.json"):
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        approval = data.get("approval", {})
        execution = data.get("execution", {})
        artifacts = data.get("artifacts", {})

        approval_id = str(approval.get("approval_id", ""))
        if not approval_id:
            continue

        try:
            conn.execute(
                "INSERT OR IGNORE INTO approvals ("
                "  id, tool_name, arguments, tool_scope,"
                "  created_at_utc, applied_at_utc, execution_status, undoable,"
                "  result_preview, error_type, error_message, partial_scan,"
                "  audit_dir, manifest_file, changes, binary_snapshots"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    approval_id,
                    str(approval.get("tool_name", "")),
                    json.dumps(approval.get("arguments", {}), ensure_ascii=False),
                    json.dumps(approval.get("tool_scope", []), ensure_ascii=False),
                    str(approval.get("created_at_utc", "")),
                    str(approval.get("applied_at_utc", "")),
                    str(execution.get("status", "success")),
                    1 if approval.get("undoable") else 0,
                    str(execution.get("result_preview", "")),
                    execution.get("error_type"),
                    execution.get("error_message"),
                    1 if execution.get("partial_scan") else 0,
                    str(manifest_file.parent),
                    str(manifest_file),
                    json.dumps(artifacts.get("changes", []), ensure_ascii=False),
                    json.dumps(artifacts.get("binary_snapshots", []), ensure_ascii=False),
                ),
            )
            migrated += 1
        except Exception:
            pass

    conn.commit()
    if migrated:
        logger.info("已从 manifest.json 迁移 %d 条审批记录", migrated)
