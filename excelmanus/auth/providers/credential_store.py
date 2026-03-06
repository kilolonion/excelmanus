"""auth_profiles 表 CRUD + Fernet 加密存储。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from excelmanus.auth.providers.base import (
    AuthProfileRecord,
    AuthProfileSummary,
    ValidatedCredential,
)
from excelmanus.security.cipher import TokenCipher as _TokenCipher

if TYPE_CHECKING:
    from excelmanus.db_adapter import ConnectionAdapter

logger = logging.getLogger(__name__)


# ── CredentialStore ──────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class CredentialStore:
    """auth_profiles 表 CRUD + Fernet 加密。"""

    def __init__(self, conn: "ConnectionAdapter") -> None:
        self._conn = conn
        self._cipher = _TokenCipher()

    def upsert_profile(
        self,
        user_id: str,
        provider: str,
        profile_name: str,
        credential: ValidatedCredential,
    ) -> AuthProfileSummary:
        """插入或更新 auth profile。"""
        now = _now_iso()
        profile_id = str(uuid.uuid4())

        enc_access = self._cipher.encrypt(credential.access_token)
        enc_refresh = self._cipher.encrypt(credential.refresh_token)
        extra_json = None
        if credential.extra_data:
            import json
            extra_json = json.dumps(credential.extra_data, ensure_ascii=False)

        # 尝试更新已有 profile
        existing = self._conn.execute(
            "SELECT id FROM auth_profiles WHERE user_id = ? AND provider = ? AND profile_name = ?",
            (user_id, provider, profile_name),
        ).fetchone()

        if existing:
            profile_id = existing["id"] if isinstance(existing, dict) else existing[0]
            self._conn.execute(
                """UPDATE auth_profiles
                   SET access_token = ?, refresh_token = ?, expires_at = ?,
                       account_id = ?, plan_type = ?, credential_type = ?,
                       extra_data = ?, is_active = 1, updated_at = ?
                   WHERE id = ?""",
                (
                    enc_access, enc_refresh, credential.expires_at,
                    credential.account_id, credential.plan_type,
                    credential.credential_type, extra_json, now,
                    profile_id,
                ),
            )
        else:
            self._conn.execute(
                """INSERT INTO auth_profiles
                   (id, user_id, provider, profile_name, credential_type,
                    access_token, refresh_token, expires_at,
                    account_id, plan_type, extra_data, is_active,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                (
                    profile_id, user_id, provider, profile_name,
                    credential.credential_type,
                    enc_access, enc_refresh, credential.expires_at,
                    credential.account_id, credential.plan_type,
                    extra_json, now, now,
                ),
            )
        self._conn.commit()

        return AuthProfileSummary(
            id=profile_id,
            user_id=user_id,
            provider=provider,
            profile_name=profile_name,
            credential_type=credential.credential_type,
            expires_at=credential.expires_at,
            account_id=credential.account_id,
            plan_type=credential.plan_type,
            is_active=True,
            created_at=now,
            updated_at=now,
        )

    def get_active_profile(
        self, user_id: str, provider: str
    ) -> AuthProfileRecord | None:
        """获取用户指定 provider 的活跃 profile（解密 token）。"""
        row = self._conn.execute(
            """SELECT id, user_id, provider, profile_name, credential_type,
                      access_token, refresh_token, expires_at,
                      account_id, plan_type, extra_data, is_active,
                      created_at, updated_at
               FROM auth_profiles
               WHERE user_id = ? AND provider = ? AND is_active = 1
               ORDER BY updated_at DESC LIMIT 1""",
            (user_id, provider),
        ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def get_profile_by_name(
        self, user_id: str, provider: str, profile_name: str,
    ) -> AuthProfileRecord | None:
        """按 profile_name 精确获取活跃 profile（解密 token）。"""
        row = self._conn.execute(
            """SELECT id, user_id, provider, profile_name, credential_type,
                      access_token, refresh_token, expires_at,
                      account_id, plan_type, extra_data, is_active,
                      created_at, updated_at
               FROM auth_profiles
               WHERE user_id = ? AND provider = ? AND profile_name = ? AND is_active = 1
               LIMIT 1""",
            (user_id, provider, profile_name),
        ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def list_profiles(self, user_id: str) -> list[AuthProfileSummary]:
        """列出用户所有 profile（不含明文 token）。"""
        rows = self._conn.execute(
            """SELECT id, user_id, provider, profile_name, credential_type,
                      expires_at, account_id, plan_type, is_active,
                      created_at, updated_at
               FROM auth_profiles
               WHERE user_id = ?
               ORDER BY updated_at DESC""",
            (user_id,),
        ).fetchall()
        result: list[AuthProfileSummary] = []
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else dict(zip(
                ["id", "user_id", "provider", "profile_name", "credential_type",
                 "expires_at", "account_id", "plan_type", "is_active",
                 "created_at", "updated_at"], r,
            ))
            result.append(AuthProfileSummary(
                id=d["id"],
                user_id=d["user_id"],
                provider=d["provider"],
                profile_name=d["profile_name"],
                credential_type=d["credential_type"],
                expires_at=d.get("expires_at"),
                account_id=d.get("account_id"),
                plan_type=d.get("plan_type"),
                is_active=bool(d.get("is_active", 1)),
                created_at=d["created_at"],
                updated_at=d["updated_at"],
            ))
        return result

    def delete_profile(
        self, user_id: str, provider: str, profile_name: str = "default"
    ) -> bool:
        """删除 auth profile。"""
        cur = self._conn.execute(
            "DELETE FROM auth_profiles WHERE user_id = ? AND provider = ? AND profile_name = ?",
            (user_id, provider, profile_name),
        )
        self._conn.commit()
        deleted = cur.rowcount if hasattr(cur, "rowcount") else 0
        return deleted > 0

    def update_tokens(
        self,
        profile_id: str,
        access_token: str,
        refresh_token: str | None,
        expires_at: str,
    ) -> None:
        """刷新后更新 token（加密存储）。"""
        enc_access = self._cipher.encrypt(access_token)
        enc_refresh = self._cipher.encrypt(refresh_token) if refresh_token else None
        self._conn.execute(
            """UPDATE auth_profiles
               SET access_token = ?, refresh_token = COALESCE(?, refresh_token),
                   expires_at = ?, updated_at = ?
               WHERE id = ?""",
            (enc_access, enc_refresh, expires_at, _now_iso(), profile_id),
        )
        self._conn.commit()

    def deactivate_profile(self, profile_id: str) -> None:
        """标记 profile 为不活跃（token 失效时调用）。"""
        self._conn.execute(
            "UPDATE auth_profiles SET is_active = 0, updated_at = ? WHERE id = ?",
            (_now_iso(), profile_id),
        )
        self._conn.commit()

    # ── OAuth Pending States（PKCE 流程状态持久化）──────────────

    def _ensure_oauth_states_table(self) -> None:
        """确保 oauth_pending_states 表存在。"""
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS oauth_pending_states (
                state TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        self._conn.commit()

    def save_oauth_state(self, state: str, data: dict, ttl: int = 900) -> None:
        """保存 OAuth pending state（PKCE 流程数据）。"""
        import json as _json
        self._ensure_oauth_states_table()
        self.cleanup_expired_states(ttl)
        self._conn.execute(
            "INSERT OR REPLACE INTO oauth_pending_states (state, data, created_at) VALUES (?, ?, ?)",
            (state, _json.dumps(data, ensure_ascii=False), _now_iso()),
        )
        self._conn.commit()

    def pop_oauth_state(self, state: str, ttl: int = 900) -> dict | None:
        """取出并删除 OAuth pending state。过期返回 None。"""
        import json as _json
        self._ensure_oauth_states_table()
        row = self._conn.execute(
            "SELECT data, created_at FROM oauth_pending_states WHERE state = ?",
            (state,),
        ).fetchone()
        if not row:
            return None
        # 删除（一次性使用）
        self._conn.execute(
            "DELETE FROM oauth_pending_states WHERE state = ?", (state,),
        )
        self._conn.commit()
        # 检查 TTL
        data_str = row["data"] if isinstance(row, dict) else row[0]
        created_at = row["created_at"] if isinstance(row, dict) else row[1]
        try:
            created = datetime.fromisoformat(created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age = (datetime.now(tz=timezone.utc) - created).total_seconds()
            if age > ttl:
                return None
        except (ValueError, TypeError):
            pass
        try:
            return _json.loads(data_str)
        except Exception:
            return None

    def cleanup_expired_states(self, ttl: int = 900) -> int:
        """清理过期的 OAuth pending states。返回删除数量。"""
        self._ensure_oauth_states_table()
        cutoff = (datetime.now(tz=timezone.utc) - __import__("datetime").timedelta(seconds=ttl)).isoformat()
        cur = self._conn.execute(
            "DELETE FROM oauth_pending_states WHERE created_at < ?", (cutoff,),
        )
        self._conn.commit()
        return cur.rowcount if hasattr(cur, "rowcount") else 0

    def _row_to_record(self, row: Any) -> AuthProfileRecord:
        d = dict(row) if hasattr(row, "keys") else dict(zip(
            ["id", "user_id", "provider", "profile_name", "credential_type",
             "access_token", "refresh_token", "expires_at",
             "account_id", "plan_type", "extra_data", "is_active",
             "created_at", "updated_at"], row,
        ))
        return AuthProfileRecord(
            id=d["id"],
            user_id=d["user_id"],
            provider=d["provider"],
            profile_name=d["profile_name"],
            credential_type=d["credential_type"],
            access_token=self._cipher.decrypt(d.get("access_token")),
            refresh_token=self._cipher.decrypt(d.get("refresh_token")),
            expires_at=d.get("expires_at"),
            account_id=d.get("account_id"),
            plan_type=d.get("plan_type"),
            extra_data=d.get("extra_data"),
            is_active=bool(d.get("is_active", 1)),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )
