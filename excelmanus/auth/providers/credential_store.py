"""auth_profiles 表 CRUD + Fernet 加密存储。"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from excelmanus.auth.providers.base import (
    AuthProfileRecord,
    AuthProfileSummary,
    ValidatedCredential,
)

if TYPE_CHECKING:
    from excelmanus.db_adapter import ConnectionAdapter

logger = logging.getLogger(__name__)

# ── Fernet 加密 ──────────────────────────────────────────────


def _derive_fernet_key() -> bytes | None:
    """派生 Fernet 加密密钥。优先级：

    1. EXCELMANUS_SECRET_KEY 环境变量
    2. ~/.excelmanus/data/.secret_key 自动生成
    3. None（不加密，仅开发环境）
    """
    secret = os.environ.get("EXCELMANUS_SECRET_KEY")
    if secret:
        import hashlib
        raw = hashlib.sha256(secret.encode()).digest()
        import base64
        return base64.urlsafe_b64encode(raw)

    key_dir = Path.home() / ".excelmanus" / "data"
    key_file = key_dir / ".secret_key"
    if key_file.exists():
        return key_file.read_bytes().strip()

    try:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        key_dir.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(key)
        key_file.chmod(0o600)
        logger.info("已生成加密密钥: %s", key_file)
        return key
    except Exception:
        logger.warning("无法生成加密密钥，token 将以明文存储", exc_info=True)
        return None


class _TokenCipher:
    """Token 加解密封装。无密钥时退化为明文。"""

    def __init__(self) -> None:
        self._fernet = None
        key = _derive_fernet_key()
        if key:
            try:
                from cryptography.fernet import Fernet
                self._fernet = Fernet(key)
            except Exception:
                logger.warning("Fernet 初始化失败", exc_info=True)

    def encrypt(self, plaintext: str | None) -> str | None:
        if not plaintext:
            return plaintext
        if self._fernet:
            return self._fernet.encrypt(plaintext.encode()).decode()
        return plaintext

    def decrypt(self, ciphertext: str | None) -> str | None:
        if not ciphertext:
            return ciphertext
        if self._fernet:
            try:
                return self._fernet.decrypt(ciphertext.encode()).decode()
            except Exception:
                logger.warning("Token 解密失败，可能密钥已变更")
                return None
        return ciphertext


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
