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


def _restrict_file_permissions(filepath: Path) -> None:
    """将文件权限限制为仅当前用户可读写。

    Unix: chmod 0o600
    Windows: icacls 移除继承并仅授予当前用户完全控制
    """
    import sys
    if sys.platform == "win32":
        try:
            import subprocess
            fp = str(filepath)
            # 移除继承权限，仅保留当前用户
            subprocess.run(
                ["icacls", fp, "/inheritance:r", "/grant:r",
                 f"{os.environ.get('USERNAME', 'SYSTEM')}:F"],
                capture_output=True, check=False, timeout=10,
            )
        except Exception:
            logger.debug("Windows ACL 设置失败: %s", filepath, exc_info=True)
    else:
        try:
            filepath.chmod(0o600)
        except OSError:
            logger.debug("chmod 设置失败: %s", filepath, exc_info=True)


def derive_fernet_key() -> bytes | None:
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
        _restrict_file_permissions(key_file)
        logger.info("已生成加密密钥: %s", key_file)
        return key
    except Exception:
        logger.warning("无法生成加密密钥，敏感字段将以明文存储", exc_info=True)
        return None


class TokenCipher:
    """Token 加解密封装。

    加密不可用时，encrypt() 抛出 CipherUnavailableError 以防止明文存储。
    如需允许明文回退（仅限开发/迁移场景），请使用 encrypt_or_passthrough()。
    """

    def __init__(self) -> None:
        self._fernet = None
        key = derive_fernet_key()
        if key:
            try:
                from cryptography.fernet import Fernet
                self._fernet = Fernet(key)
            except Exception:
                logger.warning("Fernet 初始化失败", exc_info=True)

    @property
    def is_active(self) -> bool:
        """是否已启用加密。"""
        return self._fernet is not None

    def encrypt(self, plaintext: str | None) -> str | None:
        """加密明文。加密不可用时抛出 CipherUnavailableError。"""
        if not plaintext:
            return plaintext
        if self._fernet:
            return self._fernet.encrypt(plaintext.encode()).decode()
        raise CipherUnavailableError(
            "加密组件不可用（cryptography 未安装或密钥派生失败），"
            "拒绝以明文存储敏感数据。请安装 cryptography 或设置 EXCELMANUS_SECRET_KEY。"
        )

    def encrypt_or_passthrough(self, plaintext: str | None) -> str | None:
        """加密明文，加密不可用时原样返回（仅限非关键场景）。"""
        if not plaintext:
            return plaintext
        if self._fernet:
            return self._fernet.encrypt(plaintext.encode()).decode()
        return plaintext

    def decrypt(self, ciphertext: str | None) -> str | None:
        """解密密文。解密失败返回 None（适用于安全敏感场景）。"""
        if not ciphertext:
            return ciphertext
        if self._fernet:
            try:
                return self._fernet.decrypt(ciphertext.encode()).decode()
            except Exception:
                logger.warning("Token 解密失败，可能密钥已变更")
                return None
        return ciphertext

    def decrypt_or_passthrough(self, ciphertext: str | None) -> str | None:
        """解密密文，失败时返回原文（兼容明文→加密迁移过渡期）。"""
        if not ciphertext:
            return ciphertext
        if self._fernet:
            try:
                return self._fernet.decrypt(ciphertext.encode()).decode()
            except Exception:
                # 解密失败：可能是迁移前的明文值，原样返回
                return ciphertext
        return ciphertext
