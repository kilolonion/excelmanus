"""共享加解密工具：Fernet 对称加密封装。

用于 model_profiles.api_key 和 auth_profiles.access_token 等敏感字段的加密存储。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class CipherUnavailableError(RuntimeError):
    """加密组件不可用时抛出，防止敏感数据以明文存储。"""


def _restrict_windows_file_permissions(filepath: Path) -> None:
    """Set and verify a protected DACL using the current token's SID."""
    import win32api
    import win32con
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    acl = win32security.ACL()
    acl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.FILE_ALL_ACCESS, sid)
    win32security.SetNamedSecurityInfo(
        str(filepath), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, acl, None,
    )
    descriptor = win32security.GetNamedSecurityInfo(
        str(filepath), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION,
    )
    actual = descriptor.GetSecurityDescriptorDacl()
    control, _revision = descriptor.GetSecurityDescriptorControl()
    if actual is None or actual.GetAceCount() != 1 or not (control & win32security.SE_DACL_PROTECTED):
        raise CipherUnavailableError("密钥文件 ACL 校验失败：权限未限制到当前用户")
    header, mask, allowed_sid = actual.GetAce(0)
    if (header[0] != win32security.ACCESS_ALLOWED_ACE_TYPE
            or win32security.ConvertSidToStringSid(allowed_sid) != win32security.ConvertSidToStringSid(sid)
            or mask & win32con.FILE_ALL_ACCESS != win32con.FILE_ALL_ACCESS):
        raise CipherUnavailableError("密钥文件 ACL 校验失败：用户 SID 或权限不匹配")


def _restrict_file_permissions(filepath: Path) -> None:
    """Do not silently continue with an unprotected Windows encryption key."""
    import sys
    if sys.platform == "win32":
        try:
            _restrict_windows_file_permissions(filepath)
        except Exception as exc:
            logger.error("无法限制密钥文件权限: %s", filepath)
            raise CipherUnavailableError("无法设置或验证密钥文件权限；请检查目录 ACL，拒绝继续使用未保护的密钥。") from exc
    else:
        try:
            filepath.chmod(0o600)
        except OSError:
            logger.debug("chmod 设置失败: %s", filepath, exc_info=True)


def _write_protected_key(filepath: Path, payload: bytes) -> None:
    """Protect an empty temporary file BEFORE writing key material."""
    import tempfile
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".key-", dir=str(filepath.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            _restrict_file_permissions(Path(temporary))
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, filepath)
    finally:
        Path(temporary).unlink(missing_ok=True)


_UNSET = object()
_fernet_cache_key: object = _UNSET
_fernet_cache = None


def _secret_from_env() -> bytes | None:
    secret = os.environ.get("EXCELMANUS_SECRET_KEY", "").strip()
    if not secret:
        return None
    import base64
    import hashlib

    raw = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(raw)


def derive_fernet_key() -> bytes | None:
    """派生 Fernet 加密密钥。优先级：

    1. EXCELMANUS_SECRET_KEY 环境变量
    2. ``{EXCELMANUS_HOME}/.secret_key``（不跟随 DATA_ROOT）
    3. 历史路径 ``DATA_ROOT/.secret_key`` / ``~/.excelmanus/data/.secret_key``
       （读取后迁移到正式路径）
    4. 在正式路径自动生成
    """
    env_key = _secret_from_env()
    if env_key is not None:
        return env_key

    from excelmanus.data_home import get_legacy_secret_key_paths, get_secret_key_path

    key_file = get_secret_key_path()
    if key_file.exists():
        _restrict_file_permissions(key_file)
        payload = key_file.read_bytes().strip()
        if payload:
            return payload

    for legacy in get_legacy_secret_key_paths():
        if not legacy.exists():
            continue
        try:
            payload = legacy.read_bytes().strip()
        except OSError:
            continue
        if not payload:
            continue
        try:
            key_file.parent.mkdir(parents=True, exist_ok=True)
            _write_protected_key(key_file, payload)
            logger.info("已将加密密钥迁移到正式路径: %s -> %s", legacy, key_file)
        except CipherUnavailableError:
            raise
        except Exception:
            logger.warning("迁移加密密钥失败，回退旧路径: %s", legacy, exc_info=True)
        return payload

    try:
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        key_file.parent.mkdir(parents=True, exist_ok=True)
        _write_protected_key(key_file, key)
        logger.info("已生成加密密钥: %s", key_file)
        return key
    except CipherUnavailableError:
        raise
    except Exception:
        logger.warning("无法生成加密密钥，拒绝存储敏感字段", exc_info=True)
        return None


def _current_fernet():
    """按当前环境/密钥文件取 Fernet，HOME 变更时自动换钥匙。"""
    global _fernet_cache_key, _fernet_cache
    key = derive_fernet_key()
    if key == _fernet_cache_key:
        return _fernet_cache
    _fernet_cache_key = key
    if key:
        try:
            from cryptography.fernet import Fernet

            _fernet_cache = Fernet(key)
        except Exception:
            logger.warning("Fernet 初始化失败", exc_info=True)
            _fernet_cache = None
    else:
        _fernet_cache = None
    return _fernet_cache


class TokenCipher:
    """Token 加解密封装。

    加密不可用时，encrypt() 抛出 CipherUnavailableError 以防止明文存储。
    如需允许明文回退（仅限开发/迁移场景），请使用 encrypt_or_passthrough()。
    """

    def __init__(self) -> None:
        # Fernet 按当前 EXCELMANUS_HOME / SECRET_KEY 懒加载，避免 import 早于 load_runtime_env。
        pass

    @property
    def is_active(self) -> bool:
        """是否已启用加密。"""
        return _current_fernet() is not None

    def encrypt(self, plaintext: str | None) -> str | None:
        """加密明文。加密不可用时抛出 CipherUnavailableError。"""
        if not plaintext:
            return plaintext
        fernet = _current_fernet()
        if fernet:
            return fernet.encrypt(plaintext.encode()).decode()
        raise CipherUnavailableError(
            "加密组件不可用（cryptography 未安装或密钥派生失败），"
            "拒绝以明文存储敏感数据。请安装 cryptography 或设置 EXCELMANUS_SECRET_KEY。"
        )

    def encrypt_or_passthrough(self, plaintext: str | None) -> str | None:
        """加密明文，加密不可用时原样返回（仅限非关键场景）。"""
        if not plaintext:
            return plaintext
        fernet = _current_fernet()
        if fernet:
            return fernet.encrypt(plaintext.encode()).decode()
        return plaintext

    def decrypt(self, ciphertext: str | None) -> str | None:
        """解密密文。解密失败返回 None（适用于安全敏感场景）。"""
        if not ciphertext:
            return ciphertext
        fernet = _current_fernet()
        if fernet:
            try:
                return fernet.decrypt(ciphertext.encode()).decode()
            except Exception:
                logger.warning("Token 解密失败，可能密钥已变更")
                return None
        return ciphertext

    def decrypt_or_passthrough(self, ciphertext: str | None) -> str | None:
        """解密密文，失败时返回原文（兼容明文→加密迁移过渡期）。"""
        if not ciphertext:
            return ciphertext
        fernet = _current_fernet()
        if fernet:
            try:
                return fernet.decrypt(ciphertext.encode()).decode()
            except Exception:
                # 解密失败：可能是迁移前的明文值，原样返回
                return ciphertext
        return ciphertext
