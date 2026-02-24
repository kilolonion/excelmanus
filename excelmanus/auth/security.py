"""Password hashing and JWT token creation / verification."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

# ── Password hashing ──────────────────────────────────────


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ── JWT configuration ─────────────────────────────────────

_JWT_SECRET_KEY: str | None = None
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24       # 24 hours
REFRESH_TOKEN_EXPIRE_MINUTES = 60 * 24 * 30  # 30 days


def _get_jwt_secret() -> str:
    """Lazy-load JWT secret from env; generate a random one if not set.

    WARNING: If EXCELMANUS_JWT_SECRET is not set, a random secret is generated.
    This means all tokens are invalidated on every server restart, and
    multi-worker deployments will fail token validation across workers.
    Always set EXCELMANUS_JWT_SECRET in production.
    """
    global _JWT_SECRET_KEY
    if _JWT_SECRET_KEY is None:
        env_secret = os.environ.get("EXCELMANUS_JWT_SECRET", "").strip()
        if env_secret:
            _JWT_SECRET_KEY = env_secret
        else:
            import logging
            logging.getLogger(__name__).warning(
                "EXCELMANUS_JWT_SECRET 未设置，使用随机密钥。"
                "服务重启后所有已登录用户的 token 将失效。"
                "请在生产环境中设置 EXCELMANUS_JWT_SECRET 环境变量。"
            )
            _JWT_SECRET_KEY = secrets.token_urlsafe(64)
    return _JWT_SECRET_KEY


def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> str:
    to_encode = data.copy()
    expire = datetime.now(tz=timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> str:
    to_encode = data.copy()
    expire = datetime.now(tz=timezone.utc) + (
        expires_delta or timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT token.  Returns claims dict or None."""
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


def create_token_pair(user_id: str, role: str) -> tuple[str, str, int]:
    """Create an (access_token, refresh_token, expires_in_seconds) tuple."""
    data = {"sub": user_id, "role": role}
    access = create_access_token(data)
    refresh = create_refresh_token(data)
    return access, refresh, ACCESS_TOKEN_EXPIRE_MINUTES * 60
