"""User persistence layer — supports SQLite and PostgreSQL backends.

Provides CRUD for users, integrating with the existing Database class.
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
]


class UserStore:
    """User storage supporting SQLite and PostgreSQL backends."""

    def __init__(self, db: "Database") -> None:
        self._conn = db.conn
        self._is_pg = db.is_pg
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        ddl_list = _PG_USERS_DDL if self._is_pg else _SQLITE_USERS_DDL
        for ddl in ddl_list:
            self._conn.execute(ddl)
        self._conn.commit()

    # ── CRUD ───────────────────────────────────────────────

    def create_user(self, user: UserRecord) -> UserRecord:
        self._conn.execute(
            """INSERT INTO users
               (id, email, display_name, password_hash, role,
                oauth_provider, oauth_id, avatar_url,
                llm_api_key, llm_base_url, llm_model,
                daily_token_limit, monthly_token_limit,
                is_active, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user.id, user.email, user.display_name, user.password_hash,
                user.role, user.oauth_provider, user.oauth_id, user.avatar_url,
                user.llm_api_key, user.llm_base_url, user.llm_model,
                user.daily_token_limit, user.monthly_token_limit,
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

    # ── Token usage tracking ──────────────────────────────

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

    # ── Email verification ────────────────────────────────

    @staticmethod
    def _generate_code(length: int = 6) -> str:
        return "".join(random.choices(string.digits, k=length))

    def create_verification(
        self,
        email: str,
        purpose: str,
        expires_minutes: int = 10,
    ) -> tuple[str, str]:
        """Create a new verification record. Returns (id, code)."""
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
        """Return verification record if code is valid and not expired/used."""
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
        """Mark all unused verifications for this email+purpose as used (prevents reuse)."""
        used_at = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE email_verifications
               SET used_at = ?
               WHERE email = ? AND purpose = ? AND used_at IS NULL""",
            (used_at, email.lower(), purpose),
        )
        self._conn.commit()

    # ── Helpers ────────────────────────────────────────────

    @staticmethod
    def _row_to_record(row: object) -> UserRecord:
        d = dict(row)  # type: ignore[arg-type]
        d["is_active"] = bool(d.get("is_active", 1))
        return UserRecord.from_dict(d)
