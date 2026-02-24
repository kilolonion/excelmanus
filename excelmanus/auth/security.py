"""密码哈希和 JWT 令牌创建 / 验证。"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

# ── 密码哈希 ──────────────────────────────────────


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ── JWT 配置 ─────────────────────────────────────

_JWT_SECRET_KEY: str | None = None
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24       # 24 hours
REFRESH_TOKEN_EXPIRE_MINUTES = 60 * 24 * 30  # 30 days


def _get_jwt_secret() -> str:
    """延迟加载 JWT 密钥；未设置时生成随机密钥。

    警告：如果未设置 EXCELMANUS_JWT_SECRET，将生成随机密钥。
    这意味着每次服务重启后所有令牌都会失效，
    且多 worker 部署时跨 worker 的令牌验证会失败。
    生产环境请务必设置 EXCELMANUS_JWT_SECRET。
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
    """解码并验证 JWT 令牌。返回 claims 字典或 None。"""
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


def create_token_pair(user_id: str, role: str) -> tuple[str, str, int]:
    """创建 (access_token, refresh_token, expires_in_seconds) 元组。"""
    data = {"sub": user_id, "role": role}
    access = create_access_token(data)
    refresh = create_refresh_token(data)
    return access, refresh, ACCESS_TOKEN_EXPIRE_MINUTES * 60
