"""模型配置导出/导入：支持口令加密（AES-256-GCM + PBKDF2）和简单分享两种模式。

导出格式：EMX1:<mode>:<base64_payload>
  - EMX1:P:… → 口令模式，需要密码解密
  - EMX1:S:… → 简单分享模式，内置密钥（防君子不防小人）
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_PREFIX = "EMX1"
_MODE_PASSWORD = "P"
_MODE_SIMPLE = "S"

_PBKDF2_ITERATIONS = 600_000
_SALT_LENGTH = 16
_KEY_LENGTH = 32  # AES-256
_NONCE_LENGTH = 12  # AES-GCM standard

_SIMPLE_KEY_MATERIAL = b"ExcelManus-SimpleShare-v1-obfuscated-key-material"


def _derive_key_from_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=_KEY_LENGTH,
    )


def _get_simple_key() -> bytes:
    return hashlib.sha256(_SIMPLE_KEY_MATERIAL).digest()


def _encrypt(plaintext: bytes, key: bytes) -> bytes:
    nonce = secrets.token_bytes(_NONCE_LENGTH)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return nonce + ciphertext


def _decrypt(data: bytes, key: bytes) -> bytes:
    if len(data) < _NONCE_LENGTH + 16:
        raise ValueError("密文数据过短")
    nonce = data[:_NONCE_LENGTH]
    ciphertext = data[_NONCE_LENGTH:]
    return AESGCM(key).decrypt(nonce, ciphertext, None)


# ── Public API ────────────────────────────────────────────


def export_config(
    sections: dict[str, Any],
    *,
    password: str | None = None,
    mode: str = "password",
) -> str:
    """将配置加密为令牌字符串。

    Args:
        sections: 要导出的配置区块，键可为 "main", "aux", "vlm", "profiles"。
        password: 口令模式必填。
        mode: "password" 或 "simple"。

    Returns:
        形如 ``EMX1:P:<base64>`` 的加密令牌。
    """
    payload = {
        "v": 1,
        "ts": datetime.now(timezone.utc).isoformat(),
        "sections": sections,
    }
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    if mode == "password":
        if not password:
            raise ValueError("口令模式需要提供密码")
        salt = secrets.token_bytes(_SALT_LENGTH)
        key = _derive_key_from_password(password, salt)
        encrypted = _encrypt(plaintext, key)
        encoded = base64.urlsafe_b64encode(salt + encrypted).decode("ascii")
        return f"{_PREFIX}:{_MODE_PASSWORD}:{encoded}"

    if mode == "simple":
        key = _get_simple_key()
        encrypted = _encrypt(plaintext, key)
        encoded = base64.urlsafe_b64encode(encrypted).decode("ascii")
        return f"{_PREFIX}:{_MODE_SIMPLE}:{encoded}"

    raise ValueError(f"未知加密模式: {mode}")


def import_config(token: str, *, password: str | None = None) -> dict[str, Any]:
    """解密导入令牌，返回包含 ``v``, ``ts``, ``sections`` 的字典。

    Raises:
        ValueError: 格式无效、密码错误或数据损坏。
    """
    token = token.strip()
    parts = token.split(":", 2)
    if len(parts) != 3 or parts[0] != _PREFIX:
        raise ValueError("无效的导入令牌格式（需以 EMX1: 开头）")

    mode_char, encoded = parts[1], parts[2]

    try:
        raw = base64.urlsafe_b64decode(encoded)
    except Exception:
        raise ValueError("令牌 Base64 解码失败")

    if mode_char == _MODE_PASSWORD:
        if not password:
            raise ValueError("此令牌使用口令加密，请提供密码")
        if len(raw) < _SALT_LENGTH + _NONCE_LENGTH + 16:
            raise ValueError("令牌数据过短")
        salt = raw[:_SALT_LENGTH]
        key = _derive_key_from_password(password, salt)
        try:
            plaintext = _decrypt(raw[_SALT_LENGTH:], key)
        except Exception:
            raise ValueError("密码错误或令牌已损坏")
    elif mode_char == _MODE_SIMPLE:
        key = _get_simple_key()
        try:
            plaintext = _decrypt(raw, key)
        except Exception:
            raise ValueError("令牌解密失败（数据可能已损坏）")
    else:
        raise ValueError(f"未知加密模式标识: {mode_char}")

    try:
        result = json.loads(plaintext.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("令牌内容解析失败")

    if not isinstance(result, dict) or "sections" not in result:
        raise ValueError("令牌内容格式不正确")

    return result


def detect_token_mode(token: str) -> str | None:
    """检测令牌加密模式，返回 "password" / "simple" / None（无效）。"""
    token = token.strip()
    parts = token.split(":", 2)
    if len(parts) != 3 or parts[0] != _PREFIX:
        return None
    mode_map = {_MODE_PASSWORD: "password", _MODE_SIMPLE: "simple"}
    return mode_map.get(parts[1])
