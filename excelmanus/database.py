"""统一数据库连接管理与 schema 迁移（支持 SQLite / PostgreSQL）。"""
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

from excelmanus.db_adapter import (
    Backend,
    ConnectionAdapter,
    create_pg_adapter,
    create_sqlite_adapter,
)

logger = logging.getLogger(__name__)

# ── SQLite 迁移 DDL ──────────────────────────────────────────

_SQLITE_MIGRATIONS: dict[int, list[str]] = {
    1: [
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
        """CREATE TABLE IF NOT EXISTS vector_records (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            content_hash TEXT UNIQUE NOT NULL,
            text         TEXT NOT NULL,
            metadata     TEXT,
            vector       BLOB,
            dimensions   INTEGER NOT NULL DEFAULT 1536,
            created_at   TEXT NOT NULL
        )""",
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
    2: [
        """CREATE TABLE IF NOT EXISTS workspace_files (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace    TEXT NOT NULL,
            path         TEXT NOT NULL,
            name         TEXT NOT NULL,
            size_bytes   INTEGER NOT NULL,
            mtime_ns     INTEGER NOT NULL,
            sheets_json  TEXT NOT NULL DEFAULT '[]',
            scanned_at   TEXT NOT NULL,
            UNIQUE(workspace, path)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wf_workspace ON workspace_files(workspace)",
        """CREATE TABLE IF NOT EXISTS tool_call_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id     TEXT,
            turn           INTEGER DEFAULT 0,
            iteration      INTEGER DEFAULT 0,
            tool_name      TEXT NOT NULL,
            arguments_hash TEXT,
            success        INTEGER NOT NULL,
            duration_ms    REAL DEFAULT 0,
            result_chars   INTEGER DEFAULT 0,
            error_type     TEXT,
            error_preview  TEXT,
            created_at     TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_tcl_session ON tool_call_log(session_id, turn)",
        "CREATE INDEX IF NOT EXISTS idx_tcl_tool ON tool_call_log(tool_name, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_tcl_created ON tool_call_log(created_at)",
    ],
    3: [
        """CREATE TABLE IF NOT EXISTS llm_call_log (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id        TEXT,
            turn              INTEGER DEFAULT 0,
            iteration         INTEGER DEFAULT 0,
            model             TEXT NOT NULL,
            prompt_tokens     INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            cached_tokens     INTEGER DEFAULT 0,
            total_tokens      INTEGER DEFAULT 0,
            has_tool_calls    INTEGER DEFAULT 0,
            thinking_chars    INTEGER DEFAULT 0,
            stream            INTEGER DEFAULT 0,
            latency_ms        REAL DEFAULT 0,
            error             TEXT,
            created_at        TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_llm_session ON llm_call_log(session_id, turn)",
        "CREATE INDEX IF NOT EXISTS idx_llm_model ON llm_call_log(model, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_llm_created ON llm_call_log(created_at)",
    ],
    4: [
        """CREATE TABLE IF NOT EXISTS session_excel_diffs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            tool_call_id    TEXT NOT NULL,
            file_path       TEXT NOT NULL,
            sheet           TEXT DEFAULT '',
            affected_range  TEXT DEFAULT '',
            changes_json    TEXT NOT NULL DEFAULT '[]',
            created_at      TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_sed_session ON session_excel_diffs(session_id)",
        """CREATE TABLE IF NOT EXISTS session_affected_files (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            file_path    TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            UNIQUE(session_id, file_path)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_saf_session ON session_affected_files(session_id)",
    ],
    5: [
        """CREATE TABLE IF NOT EXISTS session_rules (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            rule_id      TEXT NOT NULL,
            content      TEXT NOT NULL,
            enabled      INTEGER NOT NULL DEFAULT 1,
            created_at   TEXT NOT NULL,
            UNIQUE(session_id, rule_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_sr_session ON session_rules(session_id)",
    ],
    6: [
        """CREATE TABLE IF NOT EXISTS session_excel_previews (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            tool_call_id    TEXT NOT NULL UNIQUE,
            file_path       TEXT NOT NULL,
            sheet           TEXT DEFAULT '',
            columns_json    TEXT NOT NULL DEFAULT '[]',
            rows_json       TEXT NOT NULL DEFAULT '[]',
            total_rows      INTEGER DEFAULT 0,
            truncated       INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_sep_session ON session_excel_previews(session_id)",
    ],
    7: [
        """CREATE TABLE IF NOT EXISTS model_profiles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            model       TEXT NOT NULL,
            api_key     TEXT DEFAULT '',
            base_url    TEXT DEFAULT '',
            description TEXT DEFAULT '',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS config_kv (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
    ],
    8: [
        "ALTER TABLE sessions ADD COLUMN user_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)",
    ],
    9: [
        "ALTER TABLE memory_entries ADD COLUMN user_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_memory_user_id ON memory_entries(user_id)",
    ],
    10: [
        "ALTER TABLE approvals ADD COLUMN user_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_approvals_user_id ON approvals(user_id)",
        "ALTER TABLE tool_call_log ADD COLUMN user_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_tcl_user_id ON tool_call_log(user_id)",
        "ALTER TABLE llm_call_log ADD COLUMN user_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_llm_user_id ON llm_call_log(user_id)",
        "ALTER TABLE workspace_files ADD COLUMN user_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_wf_user_id ON workspace_files(user_id)",
        """CREATE TABLE IF NOT EXISTS user_config_kv (
            key        TEXT NOT NULL,
            user_id    TEXT,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(key, user_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_uckv_user ON user_config_kv(user_id)",
    ],
    11: [
        "ALTER TABLE llm_call_log ADD COLUMN ttft_ms REAL DEFAULT 0",
        "ALTER TABLE llm_call_log ADD COLUMN cache_creation_tokens INTEGER DEFAULT 0",
        "ALTER TABLE llm_call_log ADD COLUMN cache_read_tokens INTEGER DEFAULT 0",
    ],
    12: [
        """CREATE TABLE IF NOT EXISTS file_registry (
            id              TEXT PRIMARY KEY,
            workspace       TEXT NOT NULL,
            canonical_path  TEXT NOT NULL,
            original_name   TEXT NOT NULL,
            file_type       TEXT NOT NULL DEFAULT 'other',
            size_bytes      INTEGER DEFAULT 0,
            origin          TEXT NOT NULL DEFAULT 'scan',
            origin_session_id TEXT,
            origin_turn     INTEGER,
            origin_tool     TEXT,
            parent_file_id  TEXT REFERENCES file_registry(id),
            sheet_meta_json TEXT DEFAULT '[]',
            content_hash    TEXT DEFAULT '',
            mtime_ns        INTEGER DEFAULT 0,
            staging_path    TEXT,
            is_active_cow   INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            deleted_at      TEXT,
            UNIQUE(workspace, canonical_path)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_fr_workspace ON file_registry(workspace)",
        "CREATE INDEX IF NOT EXISTS idx_fr_parent ON file_registry(parent_file_id)",
        "CREATE INDEX IF NOT EXISTS idx_fr_origin ON file_registry(origin)",
        """CREATE TABLE IF NOT EXISTS file_registry_aliases (
            id          TEXT PRIMARY KEY,
            file_id     TEXT NOT NULL REFERENCES file_registry(id) ON DELETE CASCADE,
            alias_type  TEXT NOT NULL,
            alias_value TEXT NOT NULL,
            UNIQUE(file_id, alias_type, alias_value)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_fra_file ON file_registry_aliases(file_id)",
        "CREATE INDEX IF NOT EXISTS idx_fra_value ON file_registry_aliases(alias_value)",
        """CREATE TABLE IF NOT EXISTS file_registry_events (
            id           TEXT PRIMARY KEY,
            file_id      TEXT NOT NULL REFERENCES file_registry(id) ON DELETE CASCADE,
            event_type   TEXT NOT NULL,
            session_id   TEXT,
            turn         INTEGER,
            tool_name    TEXT,
            details_json TEXT DEFAULT '{}',
            created_at   TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_fre_file ON file_registry_events(file_id)",
        "CREATE INDEX IF NOT EXISTS idx_fre_session ON file_registry_events(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_fre_turn ON file_registry_events(session_id, turn)",
    ],
    13: [
        "ALTER TABLE approvals ADD COLUMN session_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_approvals_session_id ON approvals(session_id)",
    ],
    14: [
        """CREATE TABLE IF NOT EXISTS session_checkpoints (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL,
            checkpoint_type TEXT NOT NULL DEFAULT 'turn',
            state_json      TEXT NOT NULL DEFAULT '{}',
            task_list_json   TEXT NOT NULL DEFAULT '{}',
            turn_number     INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_scp_session ON session_checkpoints(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_scp_session_turn ON session_checkpoints(session_id, turn_number)",
    ],
}

# ── PostgreSQL 迁移 DDL ──────────────────────────────────────

_PG_MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE IF NOT EXISTS sessions (
            id            TEXT PRIMARY KEY,
            title         TEXT NOT NULL DEFAULT '',
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            message_count INTEGER DEFAULT 0,
            status        TEXT DEFAULT 'active'
        )""",
        """CREATE TABLE IF NOT EXISTS messages (
            id          SERIAL PRIMARY KEY,
            session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role        TEXT NOT NULL,
            content     TEXT,
            turn_number INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)",
        """CREATE TABLE IF NOT EXISTS memory_entries (
            id           SERIAL PRIMARY KEY,
            category     TEXT NOT NULL,
            content      TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            source       TEXT DEFAULT '',
            created_at   TEXT NOT NULL,
            UNIQUE(category, content_hash)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_memory_category ON memory_entries(category)",
        "CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_entries(created_at)",
        """CREATE TABLE IF NOT EXISTS vector_records (
            id           SERIAL PRIMARY KEY,
            content_hash TEXT UNIQUE NOT NULL,
            text         TEXT NOT NULL,
            metadata     TEXT,
            vector       BYTEA,
            dimensions   INTEGER NOT NULL DEFAULT 1536,
            created_at   TEXT NOT NULL
        )""",
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
    2: [
        """CREATE TABLE IF NOT EXISTS workspace_files (
            id           SERIAL PRIMARY KEY,
            workspace    TEXT NOT NULL,
            path         TEXT NOT NULL,
            name         TEXT NOT NULL,
            size_bytes   BIGINT NOT NULL,
            mtime_ns     BIGINT NOT NULL,
            sheets_json  TEXT NOT NULL DEFAULT '[]',
            scanned_at   TEXT NOT NULL,
            UNIQUE(workspace, path)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wf_workspace ON workspace_files(workspace)",
        """CREATE TABLE IF NOT EXISTS tool_call_log (
            id             SERIAL PRIMARY KEY,
            session_id     TEXT,
            turn           INTEGER DEFAULT 0,
            iteration      INTEGER DEFAULT 0,
            tool_name      TEXT NOT NULL,
            arguments_hash TEXT,
            success        INTEGER NOT NULL,
            duration_ms    DOUBLE PRECISION DEFAULT 0,
            result_chars   INTEGER DEFAULT 0,
            error_type     TEXT,
            error_preview  TEXT,
            created_at     TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_tcl_session ON tool_call_log(session_id, turn)",
        "CREATE INDEX IF NOT EXISTS idx_tcl_tool ON tool_call_log(tool_name, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_tcl_created ON tool_call_log(created_at)",
    ],
    3: [
        """CREATE TABLE IF NOT EXISTS llm_call_log (
            id                SERIAL PRIMARY KEY,
            session_id        TEXT,
            turn              INTEGER DEFAULT 0,
            iteration         INTEGER DEFAULT 0,
            model             TEXT NOT NULL,
            prompt_tokens     INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            cached_tokens     INTEGER DEFAULT 0,
            total_tokens      INTEGER DEFAULT 0,
            has_tool_calls    INTEGER DEFAULT 0,
            thinking_chars    INTEGER DEFAULT 0,
            stream            INTEGER DEFAULT 0,
            latency_ms        DOUBLE PRECISION DEFAULT 0,
            error             TEXT,
            created_at        TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_llm_session ON llm_call_log(session_id, turn)",
        "CREATE INDEX IF NOT EXISTS idx_llm_model ON llm_call_log(model, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_llm_created ON llm_call_log(created_at)",
    ],
    4: [
        """CREATE TABLE IF NOT EXISTS session_excel_diffs (
            id              SERIAL PRIMARY KEY,
            session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            tool_call_id    TEXT NOT NULL,
            file_path       TEXT NOT NULL,
            sheet           TEXT DEFAULT '',
            affected_range  TEXT DEFAULT '',
            changes_json    TEXT NOT NULL DEFAULT '[]',
            created_at      TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_sed_session ON session_excel_diffs(session_id)",
        """CREATE TABLE IF NOT EXISTS session_affected_files (
            id           SERIAL PRIMARY KEY,
            session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            file_path    TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            UNIQUE(session_id, file_path)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_saf_session ON session_affected_files(session_id)",
    ],
    5: [
        """CREATE TABLE IF NOT EXISTS session_rules (
            id           SERIAL PRIMARY KEY,
            session_id   TEXT NOT NULL,
            rule_id      TEXT NOT NULL,
            content      TEXT NOT NULL,
            enabled      INTEGER NOT NULL DEFAULT 1,
            created_at   TEXT NOT NULL,
            UNIQUE(session_id, rule_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_sr_session ON session_rules(session_id)",
    ],
    6: [
        """CREATE TABLE IF NOT EXISTS session_excel_previews (
            id              SERIAL PRIMARY KEY,
            session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            tool_call_id    TEXT NOT NULL UNIQUE,
            file_path       TEXT NOT NULL,
            sheet           TEXT DEFAULT '',
            columns_json    TEXT NOT NULL DEFAULT '[]',
            rows_json       TEXT NOT NULL DEFAULT '[]',
            total_rows      INTEGER DEFAULT 0,
            truncated       INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_sep_session ON session_excel_previews(session_id)",
    ],
    7: [
        """CREATE TABLE IF NOT EXISTS model_profiles (
            id          SERIAL PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            model       TEXT NOT NULL,
            api_key     TEXT DEFAULT '',
            base_url    TEXT DEFAULT '',
            description TEXT DEFAULT '',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS config_kv (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
    ],
    8: [
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS user_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)",
    ],
    9: [
        "ALTER TABLE memory_entries ADD COLUMN IF NOT EXISTS user_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_memory_user_id ON memory_entries(user_id)",
    ],
    10: [
        "ALTER TABLE approvals ADD COLUMN IF NOT EXISTS user_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_approvals_user_id ON approvals(user_id)",
        "ALTER TABLE tool_call_log ADD COLUMN IF NOT EXISTS user_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_tcl_user_id ON tool_call_log(user_id)",
        "ALTER TABLE llm_call_log ADD COLUMN IF NOT EXISTS user_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_llm_user_id ON llm_call_log(user_id)",
        "ALTER TABLE workspace_files ADD COLUMN IF NOT EXISTS user_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_wf_user_id ON workspace_files(user_id)",
        """CREATE TABLE IF NOT EXISTS user_config_kv (
            key        TEXT NOT NULL,
            user_id    TEXT,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(key, user_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_uckv_user ON user_config_kv(user_id)",
    ],
    11: [
        "ALTER TABLE llm_call_log ADD COLUMN IF NOT EXISTS ttft_ms REAL DEFAULT 0",
        "ALTER TABLE llm_call_log ADD COLUMN IF NOT EXISTS cache_creation_tokens INTEGER DEFAULT 0",
        "ALTER TABLE llm_call_log ADD COLUMN IF NOT EXISTS cache_read_tokens INTEGER DEFAULT 0",
    ],
    12: [
        """CREATE TABLE IF NOT EXISTS file_registry (
            id              TEXT PRIMARY KEY,
            workspace       TEXT NOT NULL,
            canonical_path  TEXT NOT NULL,
            original_name   TEXT NOT NULL,
            file_type       TEXT NOT NULL DEFAULT 'other',
            size_bytes      INTEGER DEFAULT 0,
            origin          TEXT NOT NULL DEFAULT 'scan',
            origin_session_id TEXT,
            origin_turn     INTEGER,
            origin_tool     TEXT,
            parent_file_id  TEXT REFERENCES file_registry(id),
            sheet_meta_json TEXT DEFAULT '[]',
            content_hash    TEXT DEFAULT '',
            mtime_ns        INTEGER DEFAULT 0,
            staging_path    TEXT,
            is_active_cow   INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            deleted_at      TEXT,
            UNIQUE(workspace, canonical_path)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_fr_workspace ON file_registry(workspace)",
        "CREATE INDEX IF NOT EXISTS idx_fr_parent ON file_registry(parent_file_id)",
        "CREATE INDEX IF NOT EXISTS idx_fr_origin ON file_registry(origin)",
        """CREATE TABLE IF NOT EXISTS file_registry_aliases (
            id          TEXT PRIMARY KEY,
            file_id     TEXT NOT NULL REFERENCES file_registry(id) ON DELETE CASCADE,
            alias_type  TEXT NOT NULL,
            alias_value TEXT NOT NULL,
            UNIQUE(file_id, alias_type, alias_value)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_fra_file ON file_registry_aliases(file_id)",
        "CREATE INDEX IF NOT EXISTS idx_fra_value ON file_registry_aliases(alias_value)",
        """CREATE TABLE IF NOT EXISTS file_registry_events (
            id           TEXT PRIMARY KEY,
            file_id      TEXT NOT NULL REFERENCES file_registry(id) ON DELETE CASCADE,
            event_type   TEXT NOT NULL,
            session_id   TEXT,
            turn         INTEGER,
            tool_name    TEXT,
            details_json TEXT DEFAULT '{}',
            created_at   TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_fre_file ON file_registry_events(file_id)",
        "CREATE INDEX IF NOT EXISTS idx_fre_session ON file_registry_events(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_fre_turn ON file_registry_events(session_id, turn)",
    ],
    13: [
        "ALTER TABLE approvals ADD COLUMN IF NOT EXISTS session_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_approvals_session_id ON approvals(session_id)",
    ],
    14: [
        """CREATE TABLE IF NOT EXISTS session_checkpoints (
            id              SERIAL PRIMARY KEY,
            session_id      TEXT NOT NULL,
            checkpoint_type TEXT NOT NULL DEFAULT 'turn',
            state_json      TEXT NOT NULL DEFAULT '{}',
            task_list_json   TEXT NOT NULL DEFAULT '{}',
            turn_number     INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_scp_session ON session_checkpoints(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_scp_session_turn ON session_checkpoints(session_id, turn_number)",
    ],
}

_LATEST_VERSION = max(_SQLITE_MIGRATIONS.keys())


class Database:
    """统一数据库连接管理，支持增量 schema 迁移。

    支持两种后端：
    - ``db_path`` → SQLite（默认，向后兼容）
    - ``database_url`` → PostgreSQL（以 ``postgresql://`` 开头的 URL）
    """

    def __init__(
        self,
        db_path: str = "",
        *,
        database_url: str = "",
    ) -> None:
        if database_url:
            self._backend = Backend.POSTGRES
            self._adapter = create_pg_adapter(database_url)
            self._db_path = ""
            self._database_url = database_url
        else:
            self._backend = Backend.SQLITE
            self._adapter = create_sqlite_adapter(db_path)
            self._db_path = db_path
            self._database_url = ""
        self._ensure_schema_version_table()
        self._migrate()

    @property
    def conn(self) -> ConnectionAdapter:
        """返回统一连接适配器。"""
        return self._adapter

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def is_pg(self) -> bool:
        return self._backend == Backend.POSTGRES

    @property
    def db_path(self) -> str:
        """返回数据库文件路径（仅 SQLite 有意义）。"""
        return self._db_path

    def close(self) -> None:
        """关闭数据库连接。"""
        self._adapter.close()

    def _ensure_schema_version_table(self) -> None:
        if self._backend == Backend.SQLITE:
            self._adapter.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "  version INTEGER PRIMARY KEY,"
                "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
                ")"
            )
        else:
            self._adapter.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "  version INTEGER PRIMARY KEY,"
                "  applied_at TEXT NOT NULL DEFAULT (NOW()::TEXT)"
                ")"
            )
        self._adapter.commit()

    def _current_version(self) -> int:
        row = self._adapter.execute(
            "SELECT MAX(version) as v FROM schema_version"
        ).fetchone()
        if row is None:
            return 0
        v = row["v"]
        return v if v is not None else 0

    def _migrate(self) -> None:
        current = self._current_version()
        if current >= _LATEST_VERSION:
            return
        migrations = (
            _PG_MIGRATIONS if self._backend == Backend.POSTGRES
            else _SQLITE_MIGRATIONS
        )
        for version in range(current + 1, _LATEST_VERSION + 1):
            statements = migrations.get(version, [])
            for sql in statements:
                self._adapter.execute(sql)
            if self._backend == Backend.SQLITE:
                self._adapter.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (version,),
                )
            else:
                self._adapter.execute(
                    "INSERT INTO schema_version (version) VALUES (%s)",
                    (version,),
                )
            self._adapter.commit()
            logger.info("数据库 schema 迁移到 v%d（%s）", version, self._backend)


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
    """从旧文件格式迁移数据到统一数据库。

    各参数均可选，仅传入需要迁移的路径。迁移是幂等的（通过 UNIQUE 约束去重）。
    """
    conn = db.conn

    if old_chat_db_path and Path(old_chat_db_path).exists():
        _migrate_chat_history(conn, old_chat_db_path)

    if memory_dir:
        _migrate_memory_files(conn, memory_dir)

    if vectors_dir:
        _migrate_vector_files(conn, vectors_dir)

    if audit_dir:
        _migrate_approval_manifests(conn, audit_dir)


def _migrate_chat_history(conn: ConnectionAdapter, old_db_path: str) -> None:
    """从旧 chat_history.db 复制 sessions 和 messages 数据。"""
    try:
        old_conn = sqlite3.connect(old_db_path)
        old_conn.row_factory = sqlite3.Row

        for row in old_conn.execute("SELECT * FROM sessions").fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO sessions "
                "(id, title, created_at, updated_at, message_count, status, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"],
                    row["title"],
                    row["created_at"],
                    row["updated_at"],
                    row["message_count"],
                    row["status"],
                    None,  # 旧数据无 user_id，迁移后为 NULL
                ),
            )

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


def _migrate_memory_files(conn: ConnectionAdapter, memory_dir: str) -> None:
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


def _migrate_vector_files(conn: ConnectionAdapter, vectors_dir: str) -> None:
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

        # 对 PG，需要用 psycopg2.Binary 包装 bytes
        if conn.is_pg and vec_blob is not None:
            import psycopg2
            vec_blob = psycopg2.Binary(vec_blob)  # type: ignore[assignment]

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


def _migrate_approval_manifests(conn: ConnectionAdapter, audit_dir: str) -> None:
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
