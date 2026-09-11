"""下载令牌 JWT（进程级，不含身份登录 / 服务令牌 / 合并令牌）。"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

_JWT_SECRET_KEY: str | None = None
JWT_ALGORITHM = "HS256"
DOWNLOAD_TOKEN_EXPIRE_MINUTES = 30


def _get_jwt_secret() -> str:
    """延迟加载 JWT 密钥（仅用于文件下载令牌）。"""
    global _JWT_SECRET_KEY
    if _JWT_SECRET_KEY is not None:
        return _JWT_SECRET_KEY

    env_secret = os.environ.get("EXCELMANUS_JWT_SECRET", "").strip()
    if env_secret:
        _JWT_SECRET_KEY = env_secret
        return _JWT_SECRET_KEY

    from pathlib import Path

    key_dir = Path.home() / ".excelmanus" / "data"
    key_file = key_dir / ".jwt_secret"
    if key_file.exists():
        try:
            stored = key_file.read_text(encoding="utf-8").strip()
            if stored:
                _JWT_SECRET_KEY = stored
                return _JWT_SECRET_KEY
        except OSError:
            pass

    import logging

    _logger = logging.getLogger(__name__)
    new_secret = secrets.token_urlsafe(64)
    try:
        key_dir.mkdir(parents=True, exist_ok=True)
        _fd = os.open(str(key_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(_fd, new_secret.encode("utf-8"))
        finally:
            os.close(_fd)
        try:
            from excelmanus.security.cipher import _restrict_file_permissions

            _restrict_file_permissions(key_file)
        except Exception:
            pass
        _logger.info("下载令牌密钥已自动生成并持久化到 %s。", key_file)
    except FileExistsError:
        try:
            stored = key_file.read_text(encoding="utf-8").strip()
            if stored:
                _JWT_SECRET_KEY = stored
                return _JWT_SECRET_KEY
        except OSError:
            pass
    except OSError:
        _logger.warning("下载令牌密钥已生成但无法持久化，服务重启后下载链接将失效。")
    _JWT_SECRET_KEY = new_secret
    return _JWT_SECRET_KEY


def decode_token(token: str) -> dict[str, Any] | None:
    """解码并验证 JWT。返回 claims 或 None。"""
    try:
        return jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None


def create_download_token(
    file_path: str,
    expires_delta: timedelta | None = None,
) -> str:
    """创建短效文件下载令牌。只绑定路径，不绑定用户。"""
    expire = datetime.now(tz=timezone.utc) + (
        expires_delta or timedelta(minutes=DOWNLOAD_TOKEN_EXPIRE_MINUTES)
    )
    payload = {
        "type": "download",
        "file_path": file_path,
        "exp": expire,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_download_token(token: str) -> dict[str, Any] | None:
    """解码并验证文件下载令牌。"""
    payload = decode_token(token)
    if payload is None or payload.get("type") != "download":
        return None
    if not payload.get("file_path"):
        return None
    return payload
