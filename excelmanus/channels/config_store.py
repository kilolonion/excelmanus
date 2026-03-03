"""渠道配置持久化：将渠道凭证和设置存储到 config_kv 表。

支持前端热配置渠道，凭证字段（token / secret）使用 TokenCipher 加密存储。
兼容环境变量配置：环境变量优先级高于持久化配置。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from excelmanus.security.cipher import TokenCipher

if TYPE_CHECKING:
    from excelmanus.stores.config_store import GlobalConfigStore

logger = logging.getLogger(__name__)

_cipher = TokenCipher()

# config_kv 中存储渠道配置的键前缀
_CHANNEL_CONFIG_KEY = "channel_config"

# 每个渠道需要的凭证字段定义
CHANNEL_CREDENTIAL_FIELDS: dict[str, list[dict[str, Any]]] = {
    "telegram": [
        {"key": "token", "label": "Bot Token", "hint": "从 @BotFather 获取", "required": True, "secret": True},
        {"key": "allowed_users", "label": "允许的用户 ID", "hint": "逗号分隔，留空=不限制", "required": False, "secret": False},
    ],
    "qq": [
        {"key": "app_id", "label": "AppID", "hint": "从 QQ 开放平台获取", "required": True, "secret": False},
        {"key": "secret", "label": "AppSecret", "hint": "从 QQ 开放平台获取", "required": True, "secret": True},
        {"key": "allowed_users", "label": "允许的用户 ID", "hint": "逗号分隔，留空=不限制", "required": False, "secret": False},
        {"key": "sandbox", "label": "沙盒模式", "hint": "调试用，连接沙盒环境", "required": False, "secret": False, "type": "boolean"},
    ],
    "feishu": [
        {"key": "app_id", "label": "App ID", "hint": "从飞书开放平台获取", "required": True, "secret": False},
        {"key": "app_secret", "label": "App Secret", "hint": "从飞书开放平台获取", "required": True, "secret": True},
        {"key": "verification_token", "label": "Verification Token", "hint": "事件订阅的验证 Token（可选，用于验证回调来源）", "required": False, "secret": True},
        {"key": "encrypt_key", "label": "Encrypt Key", "hint": "事件订阅的加密密钥（可选，启用后事件体加密传输）", "required": False, "secret": True},
        {"key": "allowed_users", "label": "允许的用户 ID", "hint": "逗号分隔，留空=不限制", "required": False, "secret": False},
    ],
}

# 需要加密存储的字段名
_SECRET_FIELDS = {"token", "secret", "app_secret", "verification_token", "encrypt_key"}


@dataclass
class ChannelConfig:
    """单个渠道的配置。"""

    name: str  # telegram | qq | feishu
    enabled: bool = False  # 是否自动启动
    credentials: dict[str, str] = field(default_factory=dict)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChannelConfig:
        return cls(
            name=data.get("name", ""),
            enabled=data.get("enabled", False),
            credentials=data.get("credentials", {}),
            updated_at=data.get("updated_at", ""),
        )

    def has_required_credentials(self) -> bool:
        """检查是否填写了所有必填凭证。"""
        fields = CHANNEL_CREDENTIAL_FIELDS.get(self.name, [])
        for f in fields:
            if f.get("required") and not self.credentials.get(f["key"]):
                return False
        return True

    def get_missing_fields(self) -> list[str]:
        """返回缺失的必填字段名列表。"""
        fields = CHANNEL_CREDENTIAL_FIELDS.get(self.name, [])
        missing = []
        for f in fields:
            if f.get("required") and not self.credentials.get(f["key"]):
                missing.append(f["key"])
        return missing


class ChannelConfigStore:
    """渠道配置持久化管理。

    配置以 JSON 形式存储在 config_kv 表的 ``channel_config`` 键中。
    敏感字段使用 TokenCipher 加密。
    """

    def __init__(self, config_store: "GlobalConfigStore") -> None:
        self._store = config_store

    def _encrypt_credentials(self, creds: dict[str, str]) -> dict[str, str]:
        """加密敏感凭证字段。"""
        result = dict(creds)
        for key in _SECRET_FIELDS:
            val = result.get(key)
            if val:
                try:
                    result[key] = _cipher.encrypt(val)
                except Exception:
                    logger.warning("加密渠道凭证字段 %s 失败", key)
        return result

    def _decrypt_credentials(self, creds: dict[str, str]) -> dict[str, str]:
        """解密敏感凭证字段（兼容明文）。"""
        result = dict(creds)
        for key in _SECRET_FIELDS:
            val = result.get(key)
            if val:
                result[key] = _cipher.decrypt_or_passthrough(val)
        return result

    def load_all(self) -> dict[str, ChannelConfig]:
        """加载所有渠道配置。"""
        raw = self._store.get(_CHANNEL_CONFIG_KEY)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("渠道配置 JSON 解析失败，返回空配置")
            return {}

        configs: dict[str, ChannelConfig] = {}
        for name, item in data.items():
            if not isinstance(item, dict):
                continue
            item["name"] = name
            if "credentials" in item:
                item["credentials"] = self._decrypt_credentials(item["credentials"])
            configs[name] = ChannelConfig.from_dict(item)
        return configs

    def save_all(self, configs: dict[str, ChannelConfig]) -> None:
        """保存所有渠道配置。"""
        data: dict[str, Any] = {}
        for name, cfg in configs.items():
            d = cfg.to_dict()
            d["credentials"] = self._encrypt_credentials(d.get("credentials", {}))
            d.pop("name", None)
            data[name] = d
        self._store.set(_CHANNEL_CONFIG_KEY, json.dumps(data, ensure_ascii=False))

    def get(self, name: str) -> ChannelConfig | None:
        """获取单个渠道配置。"""
        configs = self.load_all()
        return configs.get(name)

    def save(self, config: ChannelConfig) -> None:
        """保存单个渠道配置（合并到全量配置中）。"""
        configs = self.load_all()
        config.updated_at = datetime.now(timezone.utc).isoformat()
        configs[config.name] = config
        self.save_all(configs)

    def delete(self, name: str) -> bool:
        """删除单个渠道配置。"""
        configs = self.load_all()
        if name not in configs:
            return False
        del configs[name]
        self.save_all(configs)
        return True

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """设置渠道是否自动启动。"""
        configs = self.load_all()
        cfg = configs.get(name)
        if cfg is None:
            return False
        cfg.enabled = enabled
        cfg.updated_at = datetime.now(timezone.utc).isoformat()
        self.save_all(configs)
        return True
