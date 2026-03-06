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
    """延迟加载 JWT 密钥。

    优先级：
    1. EXCELMANUS_JWT_SECRET 环境变量
    2. ~/.excelmanus/data/.jwt_secret 持久化文件
    3. 自动生成并持久化到上述文件

    多 worker 部署时请务必设置 EXCELMANUS_JWT_SECRET 环境变量，
    以确保所有 worker 使用相同的密钥。
    """
    global _JWT_SECRET_KEY
    if _JWT_SECRET_KEY is not None:
        return _JWT_SECRET_KEY

    env_secret = os.environ.get("EXCELMANUS_JWT_SECRET", "").strip()
    if env_secret:
        _JWT_SECRET_KEY = env_secret
        return _JWT_SECRET_KEY

    # 尝试从持久化文件读取
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

    # 生成新密钥并原子持久化（防止多 worker 同时写入竞态）
    import logging
    _logger = logging.getLogger(__name__)
    new_secret = secrets.token_urlsafe(64)
    try:
        key_dir.mkdir(parents=True, exist_ok=True)
        # 使用 O_CREAT | O_EXCL 原子创建：仅当文件不存在时成功，
        # 避免多 worker 同时生成不同密钥覆盖彼此
        _fd = os.open(str(key_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(_fd, new_secret.encode("utf-8"))
        finally:
            os.close(_fd)
        try:
            from excelmanus.security.cipher import _restrict_file_permissions
            _restrict_file_permissions(key_file)
        except Exception:
            pass  # 权限设置失败不影响功能
        _logger.info(
            "JWT 密钥已自动生成并持久化到 %s。"
            "多 worker 部署请改用 EXCELMANUS_JWT_SECRET 环境变量。",
            key_file,
        )
    except FileExistsError:
        # 另一个 worker 已先行创建——读取它的密钥
        try:
            stored = key_file.read_text(encoding="utf-8").strip()
            if stored:
                _JWT_SECRET_KEY = stored
                return _JWT_SECRET_KEY
        except OSError:
            pass
        # 极端情况：文件已创建但读取失败，使用自己生成的密钥
        _logger.warning(
            "JWT 密钥文件已由其他 worker 创建但无法读取，"
            "请设置 EXCELMANUS_JWT_SECRET 环境变量。"
        )
    except OSError:
        _logger.warning(
            "JWT 密钥已生成但无法持久化，服务重启后 token 将失效。"
            "请设置 EXCELMANUS_JWT_SECRET 环境变量。"
        )
    _JWT_SECRET_KEY = new_secret
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


SERVICE_TOKEN_EXPIRE_DAYS = 365  # 服务令牌有效期 1 年
SERVICE_TOKEN_RENEW_DAYS = 30   # 剩余不足此天数时自动续签


def create_service_token(
    service_name: str = "channel-bot",
    expires_delta: timedelta | None = None,
) -> str:
    """创建长期服务令牌（供 Bot 进程调用 API 使用）。

    服务令牌携带 ``type=service``，不代表任何具体用户。
    Bot 通过 ``X-On-Behalf-Of`` header 指定代理用户。
    """
    expire = datetime.now(tz=timezone.utc) + (
        expires_delta or timedelta(days=SERVICE_TOKEN_EXPIRE_DAYS)
    )
    payload = {
        "type": "service",
        "sub": service_name,
        "role": "service",
        "exp": expire,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_service_token(token: str) -> dict[str, Any] | None:
    """解码并验证服务令牌。返回 claims 字典或 None。"""
    payload = decode_token(token)
    if payload is None or payload.get("type") != "service":
        return None
    return payload


def get_or_create_service_token() -> str:
    """获取或创建服务令牌（持久化到文件）。

    优先级：
    1. EXCELMANUS_SERVICE_TOKEN 环境变量
    2. ~/.excelmanus/data/.service_token 持久化文件
    3. 自动生成并持久化到上述文件
    """
    import logging
    from pathlib import Path

    _logger = logging.getLogger(__name__)

    env_token = os.environ.get("EXCELMANUS_SERVICE_TOKEN", "").strip()
    if env_token:
        return env_token

    key_dir = Path.home() / ".excelmanus" / "data"
    token_file = key_dir / ".service_token"

    # 尝试从持久化文件读取并验证
    if token_file.exists():
        try:
            stored = token_file.read_text(encoding="utf-8").strip()
            payload = decode_service_token(stored) if stored else None
            if payload is not None:
                exp = payload.get("exp", 0)
                remaining = exp - datetime.now(tz=timezone.utc).timestamp()
                if remaining > SERVICE_TOKEN_RENEW_DAYS * 86400:
                    return stored
                _logger.info(
                    "服务令牌剩余 %d 天，不足 %d 天阈值，自动续签",
                    int(remaining / 86400), SERVICE_TOKEN_RENEW_DAYS,
                )
        except OSError:
            pass
        else:
            if payload is None:
                _logger.info("已有服务令牌无效或过期，重新生成")

    # 生成新令牌并持久化
    token = create_service_token()
    try:
        key_dir.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token, encoding="utf-8")
        from excelmanus.security.cipher import _restrict_file_permissions
        _restrict_file_permissions(token_file)
        _logger.info("服务令牌已自动生成并持久化到 %s", token_file)
    except OSError:
        _logger.warning("服务令牌已生成但无法持久化")

    return token


def rotate_service_token() -> str:
    """强制轮换服务令牌：生成新 token 并覆盖持久化文件。

    返回新 token。环境变量指定的 token 无法轮换（返回原值并警告）。
    """
    import logging
    from pathlib import Path

    _logger = logging.getLogger(__name__)

    env_token = os.environ.get("EXCELMANUS_SERVICE_TOKEN", "").strip()
    if env_token:
        _logger.warning("服务令牌由环境变量指定，无法自动轮换")
        return env_token

    token = create_service_token()
    key_dir = Path.home() / ".excelmanus" / "data"
    token_file = key_dir / ".service_token"
    try:
        key_dir.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token, encoding="utf-8")
        from excelmanus.security.cipher import _restrict_file_permissions
        _restrict_file_permissions(token_file)
        _logger.info("服务令牌已轮换并持久化到 %s", token_file)
    except OSError:
        _logger.warning("服务令牌已轮换但无法持久化")

    return token


DOWNLOAD_TOKEN_EXPIRE_MINUTES = 30  # 文件下载令牌有效期 30 分钟


def create_download_token(
    file_path: str,
    user_id: str = "",
    expires_delta: timedelta | None = None,
) -> str:
    """创建短效文件下载令牌（供 Bot 渠道生成可分享的下载链接）。

    令牌携带 ``type=download``，验证时无需额外 auth。
    """
    expire = datetime.now(tz=timezone.utc) + (
        expires_delta or timedelta(minutes=DOWNLOAD_TOKEN_EXPIRE_MINUTES)
    )
    payload = {
        "type": "download",
        "file_path": file_path,
        "sub": user_id,
        "exp": expire,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_download_token(token: str) -> dict[str, Any] | None:
    """解码并验证文件下载令牌。返回 claims 字典或 None。"""
    payload = decode_token(token)
    if payload is None or payload.get("type") != "download":
        return None
    if not payload.get("file_path"):
        return None
    return payload


MERGE_TOKEN_EXPIRE_MINUTES = 5  # 合并令牌有效期 5 分钟


def create_merge_token(
    existing_user_id: str,
    provider: str,
    oauth_id: str,
    email: str,
    display_name: str = "",
    avatar_url: str | None = None,
) -> str:
    """创建短效合并令牌，用于 OAuth 账号合并确认。"""
    expire = datetime.now(tz=timezone.utc) + timedelta(minutes=MERGE_TOKEN_EXPIRE_MINUTES)
    payload = {
        "type": "merge",
        "sub": existing_user_id,
        "provider": provider,
        "oauth_id": oauth_id,
        "email": email,
        "display_name": display_name,
        "avatar_url": avatar_url or "",
        "exp": expire,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_merge_token(token: str) -> dict[str, Any] | None:
    """解码并验证合并令牌。返回 claims 字典或 None。"""
    payload = decode_token(token)
    if payload is None or payload.get("type") != "merge":
        return None
    return payload


# ── OAuth State 签名（防篡改 + 动态回跳） ──────────

def create_oauth_state(provider: str, origin: str) -> str:
    """创建带签名的 OAuth state，格式：provider:random:origin_b64:signature
    
    Args:
        provider: OAuth 提供商（github/google/qq）
        origin: 发起登录的完整来源（如 https://kilon.top 或 http://localhost:3000）
    
    Returns:
        签名后的 state 字符串
    """
    import base64
    import hmac
    import hashlib
    
    random_token = secrets.token_urlsafe(16)
    # base64url 编码 origin，避免 URL 中的 ":" 与 state 分隔符冲突
    origin_b64 = base64.urlsafe_b64encode(origin.encode()).decode().rstrip("=")
    # 格式：provider:random:origin_b64
    unsigned = f"{provider}:{random_token}:{origin_b64}"
    # 用 JWT_SECRET 做 HMAC-SHA256 签名
    signature = hmac.new(
        _get_jwt_secret().encode(),
        unsigned.encode(),
        hashlib.sha256
    ).hexdigest()[:16]  # 取前 16 位，缩短 URL
    
    return f"{unsigned}:{signature}"


def verify_oauth_state(state: str, allowed_origins: list[str]) -> tuple[str, str] | None:
    """验证 OAuth state 签名并提取 origin
    
    Args:
        state: OAuth 回调中的 state 参数
        allowed_origins: 允许的完整来源白名单（如 https://kilon.top）
    
    Returns:
        (provider, origin) 或 None（验证失败）
    """
    import base64
    import hmac
    import hashlib
    
    parts = state.split(":")
    if len(parts) != 4:
        return None
    
    provider, random_token, origin_b64, signature = parts
    
    # 1. 验证签名
    unsigned = f"{provider}:{random_token}:{origin_b64}"
    expected_sig = hmac.new(
        _get_jwt_secret().encode(),
        unsigned.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    
    if not hmac.compare_digest(signature, expected_sig):
        return None
    
    # 2. 解码 origin
    try:
        padding = 4 - len(origin_b64) % 4
        if padding != 4:
            origin_b64 += "=" * padding
        origin = base64.urlsafe_b64decode(origin_b64).decode()
    except Exception:
        return None
    
    # 3. 验证 origin 在白名单内
    if origin not in allowed_origins:
        return None
    
    return provider, origin
