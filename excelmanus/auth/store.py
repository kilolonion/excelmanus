"""用户持久化层 — 支持 SQLite 和 PostgreSQL 后端。

提供用户 CRUD 操作，集成现有的 Database 类。
"""

from __future__ import annotations

import logging
import random
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from excelmanus.auth.models import UserRecord

if TYPE_CHECKING:
    from excelmanus.database import Database

logger = logging.getLogger(__name__)

_SQLITE_USERS_DDL = [
    """CREATE TABLE IF NOT EXISTS users (
        id               TEXT PRIMARY KEY,
        email            TEXT NOT NULL UNIQUE,
        display_name     TEXT NOT NULL DEFAULT '',
        password_hash    TEXT,
        role             TEXT NOT NULL DEFAULT 'user',
        oauth_provider   TEXT,
        oauth_id         TEXT,
        avatar_url       TEXT,
        llm_api_key      TEXT,
        llm_base_url     TEXT,
        llm_model        TEXT,
        daily_token_limit   INTEGER DEFAULT 0,
        monthly_token_limit INTEGER DEFAULT 0,
        allowed_models   TEXT,
        max_storage_mb   INTEGER DEFAULT 0,
        max_files        INTEGER DEFAULT 0,
        is_active        INTEGER NOT NULL DEFAULT 1,
        created_at       TEXT NOT NULL,
        updated_at       TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)",
    "CREATE INDEX IF NOT EXISTS idx_users_oauth ON users(oauth_provider, oauth_id)",
    """CREATE TABLE IF NOT EXISTS user_token_usage (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        date        TEXT NOT NULL,
        tokens_used INTEGER NOT NULL DEFAULT 0,
        UNIQUE(user_id, date)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_utu_user_date ON user_token_usage(user_id, date)",
    """CREATE TABLE IF NOT EXISTS email_verifications (
        id          TEXT PRIMARY KEY,
        email       TEXT NOT NULL,
        code        TEXT NOT NULL,
        purpose     TEXT NOT NULL,
        expires_at  TEXT NOT NULL,
        used_at     TEXT,
        created_at  TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_ev_email_purpose ON email_verifications(email, purpose)",
    """CREATE TABLE IF NOT EXISTS user_oauth_links (
        id          TEXT PRIMARY KEY,
        user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        provider    TEXT NOT NULL,
        oauth_id    TEXT NOT NULL,
        display_name TEXT,
        avatar_url  TEXT,
        linked_at   TEXT NOT NULL,
        UNIQUE(provider, oauth_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_uol_user ON user_oauth_links(user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_uol_provider_oid ON user_oauth_links(provider, oauth_id)",
    """CREATE TABLE IF NOT EXISTS channel_user_links (
        id          TEXT PRIMARY KEY,
        user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        channel     TEXT NOT NULL,
        platform_id TEXT NOT NULL,
        display_name TEXT,
        linked_at   TEXT NOT NULL,
        UNIQUE(channel, platform_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cul_user ON channel_user_links(user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cul_channel_pid ON channel_user_links(channel, platform_id)",
]

_PG_USERS_DDL = [
    """CREATE TABLE IF NOT EXISTS users (
        id               TEXT PRIMARY KEY,
        email            TEXT NOT NULL UNIQUE,
        display_name     TEXT NOT NULL DEFAULT '',
        password_hash    TEXT,
        role             TEXT NOT NULL DEFAULT 'user',
        oauth_provider   TEXT,
        oauth_id         TEXT,
        avatar_url       TEXT,
        llm_api_key      TEXT,
        llm_base_url     TEXT,
        llm_model        TEXT,
        daily_token_limit   INTEGER DEFAULT 0,
        monthly_token_limit INTEGER DEFAULT 0,
        allowed_models   TEXT,
        max_storage_mb   INTEGER DEFAULT 0,
        max_files        INTEGER DEFAULT 0,
        is_active        INTEGER NOT NULL DEFAULT 1,
        created_at       TEXT NOT NULL,
        updated_at       TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)",
    "CREATE INDEX IF NOT EXISTS idx_users_oauth ON users(oauth_provider, oauth_id)",
    """CREATE TABLE IF NOT EXISTS user_token_usage (
        id          SERIAL PRIMARY KEY,
        user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        date        TEXT NOT NULL,
        tokens_used INTEGER NOT NULL DEFAULT 0,
        UNIQUE(user_id, date)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_utu_user_date ON user_token_usage(user_id, date)",
    """CREATE TABLE IF NOT EXISTS email_verifications (
        id          TEXT PRIMARY KEY,
        email       TEXT NOT NULL,
        purpose     TEXT NOT NULL,
        code        TEXT NOT NULL,
        expires_at  TEXT NOT NULL,
        used_at     TEXT,
        created_at  TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_ev_email_purpose ON email_verifications(email, purpose)",
    """CREATE TABLE IF NOT EXISTS user_oauth_links (
        id          TEXT PRIMARY KEY,
        user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        provider    TEXT NOT NULL,
        oauth_id    TEXT NOT NULL,
        display_name TEXT,
        avatar_url  TEXT,
        linked_at   TEXT NOT NULL,
        UNIQUE(provider, oauth_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_uol_user ON user_oauth_links(user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_uol_provider_oid ON user_oauth_links(provider, oauth_id)",
    """CREATE TABLE IF NOT EXISTS channel_user_links (
        id          TEXT PRIMARY KEY,
        user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        channel     TEXT NOT NULL,
        platform_id TEXT NOT NULL,
        display_name TEXT,
        linked_at   TEXT NOT NULL,
        UNIQUE(channel, platform_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cul_user ON channel_user_links(user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cul_channel_pid ON channel_user_links(channel, platform_id)",
]


class UserStore:
    """用户存储，支持 SQLite 和 PostgreSQL 后端。"""

    def __init__(self, db: "Database") -> None:
        self._conn = db.conn
        self._is_pg = db.is_pg
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        ddl_list = _PG_USERS_DDL if self._is_pg else _SQLITE_USERS_DDL
        for ddl in ddl_list:
            self._conn.execute(ddl)
        self._conn.commit()
        # 迁移：为已有数据库添加 allowed_models 列
        self._migrate_allowed_models()
        self._migrate_storage_quota()
        self._migrate_oauth_links()

    # ── CRUD ───────────────────────────────────────────────

    def _migrate_allowed_models(self) -> None:
        """为已有数据库添加 allowed_models 列（幂等）。"""
        try:
            self._conn.execute("SELECT allowed_models FROM users LIMIT 1")
        except Exception:
            try:
                self._conn.execute("ALTER TABLE users ADD COLUMN allowed_models TEXT")
                self._conn.commit()
                logger.info("迁移：已添加 users.allowed_models 列")
            except Exception:
                pass  # 列已存在或其他错误

    def _migrate_storage_quota(self) -> None:
        """为已有数据库添加 max_storage_mb / max_files 列（幂等）。"""
        for col in ("max_storage_mb", "max_files"):
            try:
                self._conn.execute(f"SELECT {col} FROM users LIMIT 1")
            except Exception:
                try:
                    self._conn.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 0")
                    self._conn.commit()
                    logger.info("迁移：已添加 users.%s 列", col)
                except Exception:
                    pass

    def _migrate_oauth_links(self) -> None:
        """将 users 表中已有的 oauth_provider/oauth_id 迁移到 user_oauth_links（幂等）。"""
        try:
            rows = self._conn.execute(
                """SELECT id, oauth_provider, oauth_id, display_name, avatar_url
                   FROM users
                   WHERE oauth_provider IS NOT NULL AND oauth_id IS NOT NULL"""
            ).fetchall()
        except Exception:
            return

        now = datetime.now(tz=timezone.utc).isoformat()
        migrated = 0
        for row in rows:
            r = dict(row)  # type: ignore[arg-type]
            # 检查是否已迁移
            existing = self._conn.execute(
                "SELECT 1 FROM user_oauth_links WHERE provider = ? AND oauth_id = ?",
                (r["oauth_provider"], r["oauth_id"]),
            ).fetchone()
            if existing:
                continue
            self._conn.execute(
                """INSERT INTO user_oauth_links
                   (id, user_id, provider, oauth_id, display_name, avatar_url, linked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()), r["id"],
                    r["oauth_provider"], r["oauth_id"],
                    r.get("display_name"), r.get("avatar_url"), now,
                ),
            )
            migrated += 1
        if migrated:
            self._conn.commit()
            logger.info("迁移：已将 %d 条旧 OAuth 绑定迁移到 user_oauth_links", migrated)

    def create_user(self, user: UserRecord) -> UserRecord:
        self._conn.execute(
            """INSERT INTO users
               (id, email, display_name, password_hash, role,
                oauth_provider, oauth_id, avatar_url,
                llm_api_key, llm_base_url, llm_model,
                daily_token_limit, monthly_token_limit,
                allowed_models,
                max_storage_mb, max_files,
                is_active, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user.id, user.email, user.display_name, user.password_hash,
                user.role, user.oauth_provider, user.oauth_id, user.avatar_url,
                user.llm_api_key, user.llm_base_url, user.llm_model,
                user.daily_token_limit, user.monthly_token_limit,
                user.allowed_models,
                user.max_storage_mb, user.max_files,
                1 if user.is_active else 0,
                user.created_at, user.updated_at,
            ),
        )
        self._conn.commit()
        return user

    def get_by_id(self, user_id: str) -> UserRecord | None:
        row = self._conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def get_by_email(self, email: str) -> UserRecord | None:
        if self._is_pg:
            row = self._conn.execute(
                "SELECT * FROM users WHERE LOWER(email) = LOWER(%s)", (email,)
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def get_by_oauth(self, provider: str, oauth_id: str) -> UserRecord | None:
        """通过 OAuth 绑定查找用户（优先查 user_oauth_links 表）。"""
        row = self._conn.execute(
            """SELECT u.* FROM users u
               JOIN user_oauth_links l ON u.id = l.user_id
               WHERE l.provider = ? AND l.oauth_id = ?""",
            (provider, oauth_id),
        ).fetchone()
        if row:
            return self._row_to_record(row)
        # 回退：兼容尚未迁移的旧数据
        row = self._conn.execute(
            "SELECT * FROM users WHERE oauth_provider = ? AND oauth_id = ?",
            (provider, oauth_id),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def update_user(self, user_id: str, **fields: object) -> bool:
        if not fields:
            return False
        fields["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [user_id]
        cur = self._conn.execute(
            f"UPDATE users SET {set_clause} WHERE id = ?", values,
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_user(self, user_id: str) -> bool:
        """彻底删除用户及其 token 用量记录。"""
        self._conn.execute(
            "DELETE FROM user_token_usage WHERE user_id = ?", (user_id,)
        )
        cur = self._conn.execute(
            "DELETE FROM users WHERE id = ?", (user_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_users(self, *, include_inactive: bool = False) -> list[UserRecord]:
        query = "SELECT * FROM users"
        if not include_inactive:
            query += " WHERE is_active = 1"
        query += " ORDER BY created_at DESC"
        rows = self._conn.execute(query).fetchall()
        return [self._row_to_record(r) for r in rows]

    def count_users(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) as c FROM users WHERE is_active = 1").fetchone()
        return row["c"] if row else 0  # type: ignore[index]

    def email_exists(self, email: str) -> bool:
        if self._is_pg:
            row = self._conn.execute(
                "SELECT 1 FROM users WHERE LOWER(email) = LOWER(%s)", (email,)
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE", (email,)
            ).fetchone()
        return row is not None

    # ── 令牌用量追踪 ──────────────────────────────

    def record_token_usage(self, user_id: str, tokens: int) -> None:
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        self._conn.execute(
            """INSERT INTO user_token_usage (user_id, date, tokens_used)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id, date)
               DO UPDATE SET tokens_used = user_token_usage.tokens_used + ?""",
            (user_id, today, tokens, tokens),
        )
        self._conn.commit()

    def get_daily_usage(self, user_id: str, date: str | None = None) -> int:
        if date is None:
            date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        row = self._conn.execute(
            "SELECT tokens_used FROM user_token_usage WHERE user_id = ? AND date = ?",
            (user_id, date),
        ).fetchone()
        return row["tokens_used"] if row else 0  # type: ignore[index]

    def get_monthly_usage(self, user_id: str, year_month: str | None = None) -> int:
        if year_month is None:
            year_month = datetime.now(tz=timezone.utc).strftime("%Y-%m")
        row = self._conn.execute(
            "SELECT COALESCE(SUM(tokens_used), 0) as total FROM user_token_usage "
            "WHERE user_id = ? AND date LIKE ?",
            (user_id, f"{year_month}%"),
        ).fetchone()
        return row["total"] if row else 0  # type: ignore[index]

    # ── 邮箱验证 ────────────────────────────────

    @staticmethod
    def _generate_code(length: int = 6) -> str:
        return "".join(random.choices(string.digits, k=length))

    def create_verification(
        self,
        email: str,
        purpose: str,
        expires_minutes: int = 10,
    ) -> tuple[str, str]:
        """创建新的验证记录。返回 (id, code)。"""
        code = self._generate_code()
        vid = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc)
        expires_at = (now + timedelta(minutes=expires_minutes)).isoformat()
        self._conn.execute(
            """INSERT INTO email_verifications
               (id, email, purpose, code, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (vid, email.lower(), purpose, code, expires_at, now.isoformat()),
        )
        self._conn.commit()
        return vid, code

    def get_valid_verification(
        self, email: str, code: str, purpose: str
    ) -> dict | None:
        """返回有效且未过期/未使用的验证记录。"""
        now = datetime.now(tz=timezone.utc).isoformat()
        row = self._conn.execute(
            """SELECT * FROM email_verifications
               WHERE email = ? AND purpose = ? AND code = ?
                 AND used_at IS NULL AND expires_at > ?
               ORDER BY created_at DESC LIMIT 1""",
            (email.lower(), purpose, code, now),
        ).fetchone()
        return dict(row) if row else None  # type: ignore[arg-type]

    def mark_verification_used(self, verification_id: str) -> None:
        used_at = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE email_verifications SET used_at = ? WHERE id = ?",
            (used_at, verification_id),
        )
        self._conn.commit()

    def invalidate_verifications(self, email: str, purpose: str) -> None:
        """将该邮箱+用途的所有未使用验证记录标记为已使用（防止重用）。"""
        used_at = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE email_verifications
               SET used_at = ?
               WHERE email = ? AND purpose = ? AND used_at IS NULL""",
            (used_at, email.lower(), purpose),
        )
        self._conn.commit()

    # ── OAuth Links ─────────────────────────────────────────

    def create_oauth_link(
        self,
        user_id: str,
        provider: str,
        oauth_id: str,
        display_name: str | None = None,
        avatar_url: str | None = None,
    ) -> str:
        """为用户创建 OAuth 绑定。返回 link id。"""
        link_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO user_oauth_links
               (id, user_id, provider, oauth_id, display_name, avatar_url, linked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (link_id, user_id, provider, oauth_id, display_name, avatar_url, now),
        )
        self._conn.commit()
        logger.info("OAuth 绑定创建: user=%s provider=%s", user_id, provider)
        return link_id

    def get_oauth_links(self, user_id: str) -> list[dict]:
        """获取用户的所有 OAuth 绑定。"""
        rows = self._conn.execute(
            "SELECT * FROM user_oauth_links WHERE user_id = ? ORDER BY linked_at",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]  # type: ignore[arg-type]

    def get_oauth_link(self, provider: str, oauth_id: str) -> dict | None:
        """通过 provider + oauth_id 查找绑定记录。"""
        row = self._conn.execute(
            "SELECT * FROM user_oauth_links WHERE provider = ? AND oauth_id = ?",
            (provider, oauth_id),
        ).fetchone()
        return dict(row) if row else None  # type: ignore[arg-type]

    def delete_oauth_link(self, user_id: str, provider: str) -> bool:
        """删除用户的某个 OAuth 绑定。"""
        cur = self._conn.execute(
            "DELETE FROM user_oauth_links WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        )
        self._conn.commit()
        if cur.rowcount > 0:
            logger.info("OAuth 绑定删除: user=%s provider=%s", user_id, provider)
        return cur.rowcount > 0

    def count_oauth_links(self, user_id: str) -> int:
        """用户绑定的 OAuth 方式数量。"""
        row = self._conn.execute(
            "SELECT COUNT(*) as c FROM user_oauth_links WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return row["c"] if row else 0  # type: ignore[index]

    # ── Channel User Links ────────────────────────────────────

    def link_channel_user(
        self,
        user_id: str,
        channel: str,
        platform_id: str,
        display_name: str | None = None,
    ) -> str:
        """将渠道平台用户绑定到后端用户。返回 link id。"""
        link_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO channel_user_links
               (id, user_id, channel, platform_id, display_name, linked_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (link_id, user_id, channel, platform_id, display_name, now),
        )
        self._conn.commit()
        logger.info(
            "渠道绑定创建: user=%s channel=%s platform_id=%s",
            user_id, channel, platform_id,
        )
        return link_id

    def get_user_by_channel(
        self, channel: str, platform_id: str,
    ) -> UserRecord | None:
        """通过渠道+平台 ID 查找已绑定的后端用户。"""
        row = self._conn.execute(
            """SELECT u.* FROM users u
               JOIN channel_user_links l ON u.id = l.user_id
               WHERE l.channel = ? AND l.platform_id = ?""",
            (channel, platform_id),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def get_channel_link(
        self, channel: str, platform_id: str,
    ) -> dict | None:
        """获取渠道绑定记录。"""
        row = self._conn.execute(
            "SELECT * FROM channel_user_links WHERE channel = ? AND platform_id = ?",
            (channel, platform_id),
        ).fetchone()
        return dict(row) if row else None  # type: ignore[arg-type]

    def get_channel_links_for_user(self, user_id: str) -> list[dict]:
        """获取用户的所有渠道绑定。"""
        rows = self._conn.execute(
            "SELECT * FROM channel_user_links WHERE user_id = ? ORDER BY linked_at",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]  # type: ignore[arg-type]

    def unlink_channel_user(
        self, user_id: str, channel: str,
    ) -> bool:
        """解除用户的某个渠道绑定。"""
        cur = self._conn.execute(
            "DELETE FROM channel_user_links WHERE user_id = ? AND channel = ?",
            (user_id, channel),
        )
        self._conn.commit()
        if cur.rowcount > 0:
            logger.info("渠道绑定删除: user=%s channel=%s", user_id, channel)
        return cur.rowcount > 0

    def unlink_channel_by_platform(
        self, channel: str, platform_id: str,
    ) -> bool:
        """通过渠道+平台 ID 解除绑定。"""
        cur = self._conn.execute(
            "DELETE FROM channel_user_links WHERE channel = ? AND platform_id = ?",
            (channel, platform_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ── 辅助方法 ────────────────────────────────────────────

    @staticmethod
    def _row_to_record(row: object) -> UserRecord:
        d = dict(row)  # type: ignore[arg-type]
        d["is_active"] = bool(d.get("is_active", 1))
        return UserRecord.from_dict(d)
