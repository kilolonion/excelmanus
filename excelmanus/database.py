"""统一 SQLite 连接管理与 schema 迁移。"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

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
