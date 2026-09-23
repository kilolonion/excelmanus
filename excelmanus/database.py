"""统一数据库连接管理与 schema 迁移（SQLite）。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import sqlite3
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

from excelmanus.db_adapter import (
    Backend,
    ConnectionAdapter,
    create_sqlite_adapter,
)

logger = logging.getLogger(__name__)

# ── SQLite 迁移 DDL（squash：旧 1→25 最终形态即为唯一 v1）────────

_SQLITE_MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE IF NOT EXISTS sessions (
            id            TEXT PRIMARY KEY,
            title         TEXT NOT NULL DEFAULT '',
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            message_count INTEGER DEFAULT 0,
            user_id       TEXT,
            title_source  TEXT DEFAULT 'auto'
        )""",
        "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)",
        """CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role        TEXT NOT NULL,
            content     TEXT,
            turn_number INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL,
            message_id  TEXT NOT NULL DEFAULT ''
        )""",
        "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_session_message_id "
        "ON messages(session_id, message_id)",
        """CREATE TABLE IF NOT EXISTS memory_entries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            category     TEXT NOT NULL,
            content      TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            source       TEXT DEFAULT '',
            created_at   TEXT NOT NULL,
            user_id      TEXT,
            UNIQUE(category, content_hash)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_memory_category ON memory_entries(category)",
        "CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_entries(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_memory_user_id ON memory_entries(user_id)",
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
            binary_snapshots TEXT,
            user_id          TEXT,
            session_id       TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(execution_status)",
        "CREATE INDEX IF NOT EXISTS idx_approvals_created ON approvals(created_at_utc)",
        "CREATE INDEX IF NOT EXISTS idx_approvals_user_id ON approvals(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_approvals_session_id ON approvals(session_id)",
        """CREATE TABLE IF NOT EXISTS workspace_files (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace    TEXT NOT NULL,
            path         TEXT NOT NULL,
            name         TEXT NOT NULL,
            size_bytes   INTEGER NOT NULL,
            mtime_ns     INTEGER NOT NULL,
            sheets_json  TEXT NOT NULL DEFAULT '[]',
            scanned_at   TEXT NOT NULL,
            user_id      TEXT,
            UNIQUE(workspace, path)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wf_workspace ON workspace_files(workspace)",
        "CREATE INDEX IF NOT EXISTS idx_wf_user_id ON workspace_files(user_id)",
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
            parent_call_id TEXT,
            call_id        TEXT,
            created_at     TEXT NOT NULL,
            user_id        TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_tcl_session ON tool_call_log(session_id, turn)",
        "CREATE INDEX IF NOT EXISTS idx_tcl_parent ON tool_call_log(parent_call_id)",
        "CREATE INDEX IF NOT EXISTS idx_tcl_call_id ON tool_call_log(call_id)",
        "CREATE INDEX IF NOT EXISTS idx_tcl_tool ON tool_call_log(tool_name, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_tcl_created ON tool_call_log(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_tcl_user_id ON tool_call_log(user_id)",
        """CREATE TABLE IF NOT EXISTS llm_call_log (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id            TEXT,
            turn                  INTEGER DEFAULT 0,
            iteration             INTEGER DEFAULT 0,
            model                 TEXT NOT NULL,
            prompt_tokens         INTEGER DEFAULT 0,
            completion_tokens     INTEGER DEFAULT 0,
            cached_tokens         INTEGER DEFAULT 0,
            total_tokens          INTEGER DEFAULT 0,
            has_tool_calls        INTEGER DEFAULT 0,
            thinking_chars        INTEGER DEFAULT 0,
            stream                INTEGER DEFAULT 0,
            latency_ms            REAL DEFAULT 0,
            error                 TEXT,
            created_at            TEXT NOT NULL,
            user_id               TEXT,
            ttft_ms               REAL DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0,
            cache_read_tokens     INTEGER DEFAULT 0
        )""",
        "CREATE INDEX IF NOT EXISTS idx_llm_session ON llm_call_log(session_id, turn)",
        "CREATE INDEX IF NOT EXISTS idx_llm_model ON llm_call_log(model, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_llm_created ON llm_call_log(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_llm_user_id ON llm_call_log(user_id)",
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
        """CREATE TABLE IF NOT EXISTS model_profiles (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            name                 TEXT NOT NULL UNIQUE,
            model                TEXT NOT NULL,
            api_key              TEXT DEFAULT '',
            base_url             TEXT DEFAULT '',
            description          TEXT DEFAULT '',
            created_at           TEXT NOT NULL,
            updated_at           TEXT NOT NULL,
            protocol             TEXT DEFAULT 'auto',
            thinking_mode        TEXT DEFAULT 'auto',
            model_family         TEXT DEFAULT '',
            custom_extra_body    TEXT DEFAULT '',
            custom_extra_headers TEXT DEFAULT '',
            canonical_model      TEXT DEFAULT ''
        )""",
        """CREATE TABLE IF NOT EXISTS config_kv (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS user_config_kv (
            key        TEXT NOT NULL,
            user_id    TEXT,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(key, user_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_uckv_user ON user_config_kv(user_id)",
        """CREATE TABLE IF NOT EXISTS file_registry (
            id                TEXT PRIMARY KEY,
            workspace         TEXT NOT NULL,
            canonical_path    TEXT NOT NULL,
            original_name     TEXT NOT NULL,
            file_type         TEXT NOT NULL DEFAULT 'other',
            size_bytes        INTEGER DEFAULT 0,
            origin            TEXT NOT NULL DEFAULT 'scan',
            origin_session_id TEXT,
            origin_turn       INTEGER,
            origin_tool       TEXT,
            parent_file_id    TEXT REFERENCES file_registry(id),
            sheet_meta_json   TEXT DEFAULT '[]',
            content_hash      TEXT DEFAULT '',
            mtime_ns          INTEGER DEFAULT 0,
            staging_path      TEXT,
            is_active_cow     INTEGER DEFAULT 0,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            deleted_at        TEXT,
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
        """CREATE TABLE IF NOT EXISTS session_checkpoints (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL,
            checkpoint_type TEXT NOT NULL DEFAULT 'turn',
            state_json      TEXT NOT NULL DEFAULT '{}',
            task_list_json  TEXT NOT NULL DEFAULT '{}',
            turn_number     INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_scp_session ON session_checkpoints(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_scp_session_turn ON session_checkpoints(session_id, turn_number)",
        """CREATE TABLE IF NOT EXISTS auth_profiles (
            id              TEXT PRIMARY KEY,
            user_id         TEXT NOT NULL,
            provider        TEXT NOT NULL,
            profile_name    TEXT NOT NULL DEFAULT 'default',
            credential_type TEXT NOT NULL DEFAULT 'oauth',
            access_token    TEXT,
            refresh_token   TEXT,
            expires_at      TEXT,
            account_id      TEXT,
            plan_type       TEXT,
            extra_data      TEXT,
            is_active       INTEGER DEFAULT 1,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            UNIQUE(user_id, provider, profile_name)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_ap_user ON auth_profiles(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_ap_provider ON auth_profiles(user_id, provider)",
        """CREATE TABLE IF NOT EXISTS file_groups (
            id          TEXT PRIMARY KEY,
            workspace   TEXT NOT NULL,
            name        TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_fg_workspace ON file_groups(workspace)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_fg_workspace_name ON file_groups(workspace, name)",
        """CREATE TABLE IF NOT EXISTS file_group_members (
            group_id TEXT NOT NULL REFERENCES file_groups(id) ON DELETE CASCADE,
            file_id  TEXT NOT NULL REFERENCES file_registry(id) ON DELETE CASCADE,
            role     TEXT DEFAULT 'member',
            added_at TEXT NOT NULL,
            PRIMARY KEY (group_id, file_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_fgm_group ON file_group_members(group_id)",
        "CREATE INDEX IF NOT EXISTS idx_fgm_file ON file_group_members(file_id)",
        """CREATE TABLE IF NOT EXISTS session_summaries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL UNIQUE,
            user_id         TEXT,
            summary_text    TEXT NOT NULL,
            task_goal       TEXT DEFAULT '',
            files_involved  TEXT DEFAULT '[]',
            outcome         TEXT DEFAULT '',
            unfinished      TEXT DEFAULT '',
            token_count     INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_ss_user ON session_summaries(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_ss_updated ON session_summaries(updated_at DESC)",
        """CREATE TABLE IF NOT EXISTS pool_accounts (
            id                   TEXT PRIMARY KEY,
            label                TEXT NOT NULL DEFAULT '',
            provider             TEXT NOT NULL DEFAULT 'openai-codex',
            account_id           TEXT DEFAULT '',
            plan_type            TEXT DEFAULT '',
            status               TEXT NOT NULL DEFAULT 'active',
            daily_budget_tokens  INTEGER NOT NULL DEFAULT 0,
            weekly_budget_tokens INTEGER NOT NULL DEFAULT 0,
            timezone             TEXT NOT NULL DEFAULT 'Asia/Shanghai',
            health_signal        TEXT NOT NULL DEFAULT 'ok',
            health_confidence    REAL NOT NULL DEFAULT 0.0,
            health_updated_at    TEXT DEFAULT '',
            created_at           TEXT NOT NULL,
            updated_at           TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS pool_usage_ledger (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            pool_account_id   TEXT NOT NULL REFERENCES pool_accounts(id) ON DELETE CASCADE,
            session_id        TEXT DEFAULT '',
            user_id           TEXT DEFAULT '',
            model             TEXT NOT NULL DEFAULT '',
            prompt_tokens     INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens      INTEGER NOT NULL DEFAULT 0,
            outcome           TEXT NOT NULL DEFAULT 'success',
            error_code        TEXT DEFAULT '',
            created_at        TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_pool_ledger_account ON pool_usage_ledger(pool_account_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_pool_ledger_created ON pool_usage_ledger(created_at)",
        """CREATE TABLE IF NOT EXISTS pool_budget_snapshots (
            pool_account_id    TEXT PRIMARY KEY REFERENCES pool_accounts(id) ON DELETE CASCADE,
            day_window_tokens  INTEGER NOT NULL DEFAULT 0,
            week_window_tokens INTEGER NOT NULL DEFAULT 0,
            daily_remaining    INTEGER NOT NULL DEFAULT 0,
            weekly_remaining   INTEGER NOT NULL DEFAULT 0,
            snapshot_at        TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS pool_manual_active (
            provider         TEXT NOT NULL,
            model_pattern    TEXT NOT NULL DEFAULT '*',
            pool_account_id  TEXT NOT NULL REFERENCES pool_accounts(id) ON DELETE CASCADE,
            activated_by     TEXT DEFAULT '',
            activated_at     TEXT NOT NULL,
            PRIMARY KEY (provider, model_pattern)
        )""",
        """CREATE TABLE IF NOT EXISTS pool_auto_policies (
            id                    TEXT PRIMARY KEY,
            provider              TEXT NOT NULL DEFAULT 'openai-codex',
            model_pattern         TEXT NOT NULL DEFAULT '*',
            enabled               INTEGER NOT NULL DEFAULT 1,
            low_watermark         REAL NOT NULL DEFAULT 0.15,
            rate_limit_threshold  INTEGER NOT NULL DEFAULT 3,
            transient_threshold   INTEGER NOT NULL DEFAULT 5,
            error_window_minutes  INTEGER NOT NULL DEFAULT 5,
            cooldown_seconds      INTEGER NOT NULL DEFAULT 300,
            fallback_to_default   INTEGER NOT NULL DEFAULT 1,
            created_at            TEXT NOT NULL,
            updated_at            TEXT NOT NULL,
            hysteresis_delta      REAL NOT NULL DEFAULT 0.12,
            min_dwell_seconds     INTEGER NOT NULL DEFAULT 180,
            breaker_open_seconds  INTEGER NOT NULL DEFAULT 120,
            UNIQUE(provider, model_pattern)
        )""",
        """CREATE TABLE IF NOT EXISTS pool_rotation_events (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            provider          TEXT NOT NULL,
            model_pattern     TEXT NOT NULL DEFAULT '*',
            from_account_id   TEXT DEFAULT '',
            to_account_id     TEXT DEFAULT '',
            reason            TEXT NOT NULL DEFAULT '',
            trigger           TEXT NOT NULL DEFAULT 'hard',
            fallback_used     INTEGER NOT NULL DEFAULT 0,
            created_at        TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_rotation_events_time ON pool_rotation_events(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_rotation_events_scope ON pool_rotation_events(provider, model_pattern)",
        """CREATE TABLE IF NOT EXISTS pool_scope_state (
            provider           TEXT NOT NULL,
            model_pattern      TEXT NOT NULL DEFAULT '*',
            mode               TEXT NOT NULL DEFAULT 'auto',
            current_account_id TEXT DEFAULT '',
            current_score      REAL NOT NULL DEFAULT 0.0,
            activated_at       TEXT DEFAULT '',
            cooldown_until     TEXT DEFAULT '',
            last_rotation_at   TEXT DEFAULT '',
            updated_at         TEXT NOT NULL,
            PRIMARY KEY (provider, model_pattern)
        )""",
        """CREATE TABLE IF NOT EXISTS pool_account_breakers (
            pool_account_id       TEXT PRIMARY KEY,
            consecutive_failures  INTEGER NOT NULL DEFAULT 0,
            breaker_state         TEXT NOT NULL DEFAULT 'closed',
            open_until            TEXT DEFAULT '',
            last_failure_at       TEXT DEFAULT '',
            updated_at            TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS pool_rotation_metrics_minute (
            minute_bucket   TEXT NOT NULL,
            provider        TEXT NOT NULL,
            model_pattern   TEXT NOT NULL DEFAULT '*',
            total_requests  INTEGER NOT NULL DEFAULT 0,
            success_count   INTEGER NOT NULL DEFAULT 0,
            error_429       INTEGER NOT NULL DEFAULT 0,
            error_5xx       INTEGER NOT NULL DEFAULT 0,
            rotations       INTEGER NOT NULL DEFAULT 0,
            fallbacks       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (minute_bucket, provider, model_pattern)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_metrics_minute ON pool_rotation_metrics_minute(minute_bucket DESC)",
        """CREATE TABLE IF NOT EXISTS memory_meta (
            key     TEXT PRIMARY KEY,
            value   TEXT,
            user_id TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS oauth_pending_states (
            state      TEXT PRIMARY KEY,
            data       TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
    ],
    2: [
        "ALTER TABLE sessions ADD COLUMN workspace_path TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE sessions ADD COLUMN workspace_id TEXT",
        "ALTER TABLE sessions ADD COLUMN blank INTEGER NOT NULL DEFAULT 1",
        "UPDATE sessions SET blank = CASE WHEN COALESCE(message_count, 0) = 0 THEN 1 ELSE 0 END",
        "CREATE INDEX IF NOT EXISTS idx_sessions_workspace_path ON sessions(workspace_path)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_workspace_id ON sessions(workspace_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_blank ON sessions(blank)",
        """CREATE TABLE IF NOT EXISTS workspaces (
            id         TEXT PRIMARY KEY,
            path       TEXT NOT NULL UNIQUE,
            title      TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sort_index INTEGER NOT NULL DEFAULT 0
        )""",
        "CREATE INDEX IF NOT EXISTS idx_workspaces_sort ON workspaces(sort_index, created_at)",
    ],
    3: [
        "DROP TABLE IF EXISTS vector_records",
    ],
    4: [
        "ALTER TABLE session_summaries DROP COLUMN embedding",
    ],
    5: [
        """CREATE TABLE IF NOT EXISTS session_state_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL,
            checkpoint_type TEXT NOT NULL DEFAULT 'turn',
            state_json      TEXT NOT NULL DEFAULT '{}',
            task_list_json  TEXT NOT NULL DEFAULT '{}',
            turn_number     INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL
        )""",
        # 旧库 stamp / 部分迁移重试时旧表可能已经不存在；先补空表，
        # 复制与删除都变成幂等操作。
        """CREATE TABLE IF NOT EXISTS session_checkpoints (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL,
            checkpoint_type TEXT NOT NULL DEFAULT 'turn',
            state_json      TEXT NOT NULL DEFAULT '{}',
            task_list_json  TEXT NOT NULL DEFAULT '{}',
            turn_number     INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL
        )""",
        """INSERT OR IGNORE INTO session_state_snapshots
            (id, session_id, checkpoint_type, state_json, task_list_json, turn_number, created_at)
            SELECT id, session_id, checkpoint_type, state_json, task_list_json, turn_number, created_at
            FROM session_checkpoints""",
        "DROP TABLE IF EXISTS session_checkpoints",
        "CREATE INDEX IF NOT EXISTS idx_sss_session ON session_state_snapshots(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_sss_session_turn ON session_state_snapshots(session_id, turn_number)",
    ],
    6: [
        # append-only 会话事件日志：唯一事实源；messages 表降级为 surface 快照。
        """CREATE TABLE IF NOT EXISTS session_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            seq           INTEGER NOT NULL,
            kind          TEXT NOT NULL,
            turn          INTEGER DEFAULT 0,
            step          INTEGER DEFAULT 0,
            payload       TEXT,
            surface_op    TEXT,
            shadow_start  INTEGER,
            shadow_end    INTEGER,
            source_seqs   TEXT,
            created_at    TEXT NOT NULL
        )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_session_events_session_seq "
        "ON session_events(session_id, seq)",
        "CREATE INDEX IF NOT EXISTS idx_session_events_session "
        "ON session_events(session_id, id)",
    ],
    7: [
        # Code Mode 子调用父子关联：tool_call_log 记录父 run_code 的 tool_call_id。
        "ALTER TABLE tool_call_log ADD COLUMN parent_call_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_tcl_parent ON tool_call_log(parent_call_id)",
    ],
    8: [
        "ALTER TABLE tool_call_log ADD COLUMN call_id TEXT",
        "CREATE INDEX IF NOT EXISTS idx_tcl_call_id ON tool_call_log(call_id)",
    ],
    9: [
        # Automatic/default registrations do not authorize exposing source code.
        "ALTER TABLE workspaces ADD COLUMN source_access INTEGER NOT NULL DEFAULT 0",
    ],
    10: [
        # Jev 智能匹配：档案绑定到的已知规范模型名（不改写上游 Model ID）。
        "ALTER TABLE model_profiles ADD COLUMN canonical_model TEXT DEFAULT ''",
    ],
}

_LATEST_VERSION = max(_SQLITE_MIGRATIONS.keys())
# 旧梯子 1→25 squash 后的基线版本。stamp 必须写成 1，不能写成当时的 _LATEST_VERSION，
# 否则以后加 v2 时旧库 MAX(schema_version)=25 会跳过真正的 v2 DDL。
_SCHEMA_SQUASH_VERSION = 1

_CURRENT_FORM_TABLES = (
    "sessions",
    "messages",
    "memory_entries",
    "approvals",
    "workspace_files",
    "tool_call_log",
    "llm_call_log",
    "session_excel_diffs",
    "session_affected_files",
    "session_rules",
    "session_excel_previews",
    "model_profiles",
    "config_kv",
    "user_config_kv",
    "file_registry",
    "file_registry_aliases",
    "file_registry_events",
    # session_checkpoints is accepted during legacy stamp only;
    # migration v5 renames it to session_state_snapshots.
    "auth_profiles",
    "file_groups",
    "file_group_members",
    "session_summaries",
    "pool_accounts",
    "pool_usage_ledger",
    "pool_budget_snapshots",
    "pool_manual_active",
    "pool_auto_policies",
    "pool_rotation_events",
    "pool_scope_state",
    "pool_account_breakers",
    "pool_rotation_metrics_minute",
)


class Database:
    """统一数据库连接管理，支持增量 schema 迁移。"""

    def __init__(self, db_path: str = "") -> None:
        self._backend = Backend.SQLITE
        self._adapter = create_sqlite_adapter(db_path)
        self._db_path = db_path
        # Multiple uvicorn workers can open the same SQLite file at once.  A
        # schema migration must be serialized outside SQLite's transaction;
        # otherwise both workers can observe the same version and race on an
        # ALTER TABLE (for example, duplicate canonical_model columns).
        lock_context = nullcontext()
        if db_path and str(db_path) != ":memory:":
            from excelmanus.data_home import _file_lock

            lock_context = _file_lock(
                f"{Path(db_path).expanduser()}.schema.lock",
                timeout=30.0,
            )
        with lock_context:
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
    def db_path(self) -> str:
        """返回数据库文件路径。"""
        return self._db_path

    def close(self) -> None:
        """关闭数据库连接。"""
        self._adapter.close()

    def _ensure_schema_version_table(self) -> None:
        self._adapter.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
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

    # ── SQLite ALTER TABLE 幂等保护 ──────────────────────────

    _ALTER_ADD_COL_RE = re.compile(
        r"ALTER\s+TABLE\s+(\S+)\s+ADD\s+COLUMN\s+(\S+)",
        re.IGNORECASE,
    )
    _ALTER_DROP_COL_RE = re.compile(
        r"ALTER\s+TABLE\s+(\S+)\s+DROP\s+COLUMN(?:\s+IF\s+EXISTS)?\s+(\S+)",
        re.IGNORECASE,
    )
    _CREATE_INDEX_ON_RE = re.compile(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\S+\s+ON\s+(\S+)\s*\(",
        re.IGNORECASE,
    )

    def _sqlite_column_exists(self, table: str, column: str) -> bool:
        """检查 SQLite 表中是否已存在指定列。"""
        try:
            rows = self._adapter.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
            return any(row[1] == column for row in rows)
        except Exception:
            return False

    def _safe_execute_sql(self, sql: str) -> None:
        """安全执行单条迁移 SQL，处理 SQLite ALTER TABLE 幂等问题。

        SQLite 不支持 ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``，
        因此在执行前检查列是否已存在，已存在则跳过；DROP COLUMN 反向处理，
        列不存在时跳过，旧 SQLite 不支持 DROP COLUMN 时保留旧列并告警。
        """
        m = self._ALTER_ADD_COL_RE.search(sql)
        if m:
            table, column = m.group(1), m.group(2).strip("\"'`[]")
            if not self._adapter.table_exists(table):
                logger.warning(
                    "跳过迁移：表 %s 不存在（%s）", table, sql,
                )
                return
            if self._sqlite_column_exists(table, column):
                logger.debug(
                    "跳过已存在的列: %s.%s", table, column,
                )
                return
        drop = self._ALTER_DROP_COL_RE.search(sql)
        if drop:
            table, column = drop.group(1), drop.group(2).strip("\"'`[]")
            if not self._sqlite_column_exists(table, column):
                logger.debug(
                    "跳过不存在的列: %s.%s", table, column,
                )
                return
            try:
                self._adapter.execute(sql)
            except Exception as exc:
                # SQLite < 3.35 不支持 DROP COLUMN；保留旧列不影响新代码。
                if "syntax error" in str(exc).lower() or "near \"drop\"" in str(exc).lower():
                    logger.warning(
                        "当前 SQLite 不支持 DROP COLUMN，跳过旧列: %s", sql,
                    )
                    return
                raise
            return
        idx = self._CREATE_INDEX_ON_RE.search(sql)
        if idx:
            table = idx.group(1).strip("\"'`[]")
            if not self._adapter.table_exists(table):
                logger.warning(
                    "跳过迁移：索引目标表 %s 不存在（%s）", table, sql,
                )
                return
        self._adapter.execute(sql)

    def _schema_is_current_form(self) -> bool:
        """旧梯子 MAX(schema_version)=25 的库是否已是 squash 后的当前形态。"""
        for name in _CURRENT_FORM_TABLES:
            if not self._adapter.table_exists(name):
                return False
        if not (
            self._adapter.table_exists("session_checkpoints")
            or self._adapter.table_exists("session_state_snapshots")
        ):
            return False
        if self._sqlite_column_exists("sessions", "status"):
            return False
        if not self._sqlite_column_exists("messages", "message_id"):
            return False
        for col in ("hysteresis_delta", "min_dwell_seconds", "breaker_open_seconds"):
            if not self._sqlite_column_exists("pool_auto_policies", col):
                return False
        row = self._adapter.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' "
            "AND name='idx_messages_session_message_id'"
        ).fetchone()
        return row is not None

    def _stamp_legacy_schema(self, current: int) -> None:
        """把旧梯子版本号（如 25）收成只保留 squash 基线 v1，保留用户数据。"""
        if not self._schema_is_current_form():
            raise RuntimeError(
                f"数据库 schema_version={current} 高于当前 v{_LATEST_VERSION}，"
                "但表结构不是当前形态，无法 stamp。请从备份恢复。"
            )
        self._backup_before_migrate(current, _SCHEMA_SQUASH_VERSION)
        self._adapter.execute("DELETE FROM schema_version")
        self._adapter.execute(
            "INSERT INTO schema_version (version) VALUES (?)",
            (_SCHEMA_SQUASH_VERSION,),
        )
        self._adapter.commit()
        logger.info(
            "已将旧 schema_version（max=%d）stamp 为 v%d",
            current, _SCHEMA_SQUASH_VERSION,
        )

    # ── 迁移前备份 ────────────────────────────────────────────

    def _backup_before_migrate(self, from_version: int, to_version: int) -> str | None:
        """迁移前自动备份 SQLite 数据库文件。

        返回备份路径；备份失败时返回 None。
        """
        if not self._db_path:
            return None
        if from_version == 0:
            return None  # 全新数据库无用户数据，无需备份
        src = Path(self._db_path)
        if not src.exists() or src.stat().st_size == 0:
            return None
        backup_path = src.with_suffix(f".v{from_version}_to_v{to_version}.bak")
        try:
            shutil.copy2(str(src), str(backup_path))
            # 同时备份 WAL / SHM 文件（如果存在）
            for suffix in ("-wal", "-shm"):
                wal = Path(str(src) + suffix)
                if wal.exists():
                    shutil.copy2(str(wal), str(backup_path) + suffix)
            logger.info(
                "迁移前备份: %s (v%d → v%d)",
                backup_path.name, from_version, to_version,
            )
            return str(backup_path)
        except Exception:
            logger.warning("迁移前备份失败", exc_info=True)
            return None

    @staticmethod
    def cleanup_migration_backups(db_path: str, keep: int = 2) -> None:
        """清理旧的迁移备份文件，仅保留最近 *keep* 个。"""
        p = Path(db_path)
        backups = sorted(
            p.parent.glob(f"{p.stem}.v*_to_v*.bak"),
            key=lambda f: f.stat().st_mtime,
        )
        for old in backups[:-keep] if len(backups) > keep else []:
            try:
                old.unlink()
                for suffix in ("-wal", "-shm"):
                    sidecar = Path(str(old) + suffix)
                    if sidecar.exists():
                        sidecar.unlink()
            except OSError:
                pass

    # ── 核心迁移 ──────────────────────────────────────────────

    def _migrate(self) -> None:
        current = self._current_version()
        if current > _LATEST_VERSION:
            self._stamp_legacy_schema(current)
            current = self._current_version()
        if current >= _LATEST_VERSION:
            return

        # 迁移前自动备份
        self._backup_before_migrate(current, _LATEST_VERSION)

        for version in range(current + 1, _LATEST_VERSION + 1):
            statements = _SQLITE_MIGRATIONS.get(version, [])
            try:
                for sql in statements:
                    self._safe_execute_sql(sql)
                self._adapter.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (version,),
                )
                self._adapter.commit()
                logger.info("数据库 schema 迁移到 v%d", version)
            except Exception:
                logger.error(
                    "数据库 schema 迁移 v%d 失败，后续版本将跳过",
                    version, exc_info=True,
                )
                try:
                    self._adapter.commit()
                except Exception:
                    pass
                raise RuntimeError(
                    f"数据库 schema 迁移 v{version} 失败，请检查日志或从备份恢复"
                ) from None

        # 迁移成功后清理旧备份
        if self._db_path:
            self.cleanup_migration_backups(self._db_path)


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

    if audit_dir:
        _migrate_approval_manifests(conn, audit_dir)


def _migrate_chat_history(conn: ConnectionAdapter, old_db_path: str) -> None:
    """从旧 chat_history.db 复制 sessions 和 messages 数据。失败必须抛出。"""
    old_conn = sqlite3.connect(old_db_path)
    old_conn.row_factory = sqlite3.Row
    try:
        for row in old_conn.execute("SELECT * FROM sessions").fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO sessions "
                "(id, title, created_at, updated_at, message_count, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row["id"],
                    row["title"],
                    row["created_at"],
                    row["updated_at"],
                    row["message_count"],
                    None,  # 旧数据无 user_id，迁移后为 NULL
                ),
            )

        for row in old_conn.execute("SELECT * FROM messages").fetchall():
            raw = row["content"]
            try:
                msg = json.loads(raw) if raw else {}
            except (json.JSONDecodeError, TypeError):
                msg = {}
            mid = ""
            if isinstance(msg, dict) and msg.get("message_id"):
                mid = str(msg.get("message_id"))
            if not mid:
                mid = f"legacy:{row['id']}"
            conn.execute(
                "INSERT OR IGNORE INTO messages "
                "(id, session_id, role, content, turn_number, created_at, message_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"],
                    row["session_id"],
                    row["role"],
                    row["content"],
                    row["turn_number"],
                    row["created_at"],
                    mid,
                ),
            )

        conn.commit()
        logger.info("已从旧 chat_history.db 迁移会话数据")
    except Exception:
        logger.exception("迁移旧 chat_history.db 失败")
        raise
    finally:
        old_conn.close()


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
