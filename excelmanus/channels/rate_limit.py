"""渠道 Bot 速率限制：防止恶意用户通过 Bot 高频轰炸后端 API。

使用内存滑动窗口计数器，按消息类型分桶限流，
支持自动封禁升级和非白名单用户冷却。
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from excelmanus.stores.config_store import GlobalConfigStore

logger = logging.getLogger("excelmanus.channels.rate_limit")

# config_kv 中存储速率限制配置的键
_RATE_LIMIT_CONFIG_KEY = "channel_rate_limit"


@dataclass
class RateLimitConfig:
    """渠道速率限制配置。所有值均可通过环境变量覆盖。"""

    # 对话（最昂贵：触发 LLM 调用）
    chat_per_minute: int = 5
    chat_per_hour: int = 30
    # 命令（较轻量）
    command_per_minute: int = 15
    command_per_hour: int = 120
    # 文件上传
    upload_per_minute: int = 3
    upload_per_hour: int = 20
    # 全局（所有消息类型合计）
    global_per_minute: int = 20
    global_per_hour: int = 200
    # 非白名单用户拒绝消息的冷却（秒）
    reject_cooldown_seconds: float = 10.0
    # 连续超限后的自动封禁
    auto_ban_threshold: int = 10
    auto_ban_duration_seconds: float = 600.0  # 10 分钟

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RateLimitConfig:
        """从字典反序列化。"""
        defaults = cls()
        return cls(
            chat_per_minute=int(data.get("chat_per_minute", defaults.chat_per_minute)),
            chat_per_hour=int(data.get("chat_per_hour", defaults.chat_per_hour)),
            command_per_minute=int(data.get("command_per_minute", defaults.command_per_minute)),
            command_per_hour=int(data.get("command_per_hour", defaults.command_per_hour)),
            upload_per_minute=int(data.get("upload_per_minute", defaults.upload_per_minute)),
            upload_per_hour=int(data.get("upload_per_hour", defaults.upload_per_hour)),
            global_per_minute=int(data.get("global_per_minute", defaults.global_per_minute)),
            global_per_hour=int(data.get("global_per_hour", defaults.global_per_hour)),
            reject_cooldown_seconds=float(data.get("reject_cooldown_seconds", defaults.reject_cooldown_seconds)),
            auto_ban_threshold=int(data.get("auto_ban_threshold", defaults.auto_ban_threshold)),
            auto_ban_duration_seconds=float(data.get("auto_ban_duration_seconds", defaults.auto_ban_duration_seconds)),
        )

    @classmethod
    def from_env(cls) -> RateLimitConfig:
        """从环境变量读取配置，缺失项使用默认值。"""
        def _int(key: str, default: int) -> int:
            raw = os.environ.get(key, "")
            if raw.strip():
                try:
                    return int(raw)
                except ValueError:
                    logger.warning("环境变量 %s=%r 不是有效整数，使用默认值 %d", key, raw, default)
            return default

        def _float(key: str, default: float) -> float:
            raw = os.environ.get(key, "")
            if raw.strip():
                try:
                    return float(raw)
                except ValueError:
                    logger.warning("环境变量 %s=%r 不是有效数值，使用默认值 %s", key, raw, default)
            return default

        return cls(
            chat_per_minute=_int("EXCELMANUS_CHANNEL_RATE_CHAT_PM", 5),
            chat_per_hour=_int("EXCELMANUS_CHANNEL_RATE_CHAT_PH", 30),
            command_per_minute=_int("EXCELMANUS_CHANNEL_RATE_CMD_PM", 15),
            command_per_hour=_int("EXCELMANUS_CHANNEL_RATE_CMD_PH", 120),
            upload_per_minute=_int("EXCELMANUS_CHANNEL_RATE_UPLOAD_PM", 3),
            upload_per_hour=_int("EXCELMANUS_CHANNEL_RATE_UPLOAD_PH", 20),
            global_per_minute=_int("EXCELMANUS_CHANNEL_RATE_GLOBAL_PM", 20),
            global_per_hour=_int("EXCELMANUS_CHANNEL_RATE_GLOBAL_PH", 200),
            reject_cooldown_seconds=_float("EXCELMANUS_CHANNEL_RATE_REJECT_COOLDOWN", 10.0),
            auto_ban_threshold=_int("EXCELMANUS_CHANNEL_RATE_BAN_THRESHOLD", 10),
            auto_ban_duration_seconds=_float("EXCELMANUS_CHANNEL_RATE_BAN_DURATION", 600.0),
        )

    @classmethod
    def from_store(
        cls,
        config_store: "GlobalConfigStore | None" = None,
    ) -> RateLimitConfig:
        """加载速率限制配置（优先级: 环境变量 > 数据库 > 默认值）。

        先从数据库加载持久化配置作为 base，然后环境变量覆盖对应字段。
        """
        # 1. 从 DB 加载 base
        db_cfg: dict[str, Any] = {}
        if config_store is not None:
            try:
                raw = config_store.get(_RATE_LIMIT_CONFIG_KEY)
                if raw:
                    db_cfg = json.loads(raw)
            except (json.JSONDecodeError, TypeError, Exception):
                logger.debug("加载持久化速率限制配置失败", exc_info=True)

        base = cls.from_dict(db_cfg) if db_cfg else cls()

        # 2. 环境变量覆盖
        def _int(key: str, default: int) -> int:
            raw = os.environ.get(key, "")
            if raw.strip():
                try:
                    return int(raw)
                except ValueError:
                    pass
            return default

        def _float(key: str, default: float) -> float:
            raw = os.environ.get(key, "")
            if raw.strip():
                try:
                    return float(raw)
                except ValueError:
                    pass
            return default

        return cls(
            chat_per_minute=_int("EXCELMANUS_CHANNEL_RATE_CHAT_PM", base.chat_per_minute),
            chat_per_hour=_int("EXCELMANUS_CHANNEL_RATE_CHAT_PH", base.chat_per_hour),
            command_per_minute=_int("EXCELMANUS_CHANNEL_RATE_CMD_PM", base.command_per_minute),
            command_per_hour=_int("EXCELMANUS_CHANNEL_RATE_CMD_PH", base.command_per_hour),
            upload_per_minute=_int("EXCELMANUS_CHANNEL_RATE_UPLOAD_PM", base.upload_per_minute),
            upload_per_hour=_int("EXCELMANUS_CHANNEL_RATE_UPLOAD_PH", base.upload_per_hour),
            global_per_minute=_int("EXCELMANUS_CHANNEL_RATE_GLOBAL_PM", base.global_per_minute),
            global_per_hour=_int("EXCELMANUS_CHANNEL_RATE_GLOBAL_PH", base.global_per_hour),
            reject_cooldown_seconds=_float("EXCELMANUS_CHANNEL_RATE_REJECT_COOLDOWN", base.reject_cooldown_seconds),
            auto_ban_threshold=_int("EXCELMANUS_CHANNEL_RATE_BAN_THRESHOLD", base.auto_ban_threshold),
            auto_ban_duration_seconds=_float("EXCELMANUS_CHANNEL_RATE_BAN_DURATION", base.auto_ban_duration_seconds),
        )

    @staticmethod
    def save_to_store(
        config: "RateLimitConfig",
        config_store: "GlobalConfigStore",
    ) -> None:
        """将速率限制配置持久化到 config_kv。"""
        config_store.set(
            _RATE_LIMIT_CONFIG_KEY,
            json.dumps(config.to_dict(), ensure_ascii=False),
        )

    @staticmethod
    def env_overrides() -> dict[str, str]:
        """返回当前生效的环境变量覆盖（仅已设置的）。"""
        _ENV_KEYS = {
            "chat_per_minute": "EXCELMANUS_CHANNEL_RATE_CHAT_PM",
            "chat_per_hour": "EXCELMANUS_CHANNEL_RATE_CHAT_PH",
            "command_per_minute": "EXCELMANUS_CHANNEL_RATE_CMD_PM",
            "command_per_hour": "EXCELMANUS_CHANNEL_RATE_CMD_PH",
            "upload_per_minute": "EXCELMANUS_CHANNEL_RATE_UPLOAD_PM",
            "upload_per_hour": "EXCELMANUS_CHANNEL_RATE_UPLOAD_PH",
            "global_per_minute": "EXCELMANUS_CHANNEL_RATE_GLOBAL_PM",
            "global_per_hour": "EXCELMANUS_CHANNEL_RATE_GLOBAL_PH",
            "reject_cooldown_seconds": "EXCELMANUS_CHANNEL_RATE_REJECT_COOLDOWN",
            "auto_ban_threshold": "EXCELMANUS_CHANNEL_RATE_BAN_THRESHOLD",
            "auto_ban_duration_seconds": "EXCELMANUS_CHANNEL_RATE_BAN_DURATION",
        }
        result: dict[str, str] = {}
        for field_name, env_key in _ENV_KEYS.items():
            val = os.environ.get(env_key, "").strip()
            if val:
                result[field_name] = env_key
        return result


@dataclass
class RateLimitResult:
    """速率限制检查结果。"""

    allowed: bool
    message: str = ""
    retry_after: float = 0.0  # 建议等待秒数


@dataclass
class _WindowEntry:
    """滑动窗口中追踪请求时间戳。"""

    timestamps: list[float] = field(default_factory=list)

    def count_in_window(self, window_seconds: float) -> int:
        now = time.monotonic()
        cutoff = now - window_seconds
        self.timestamps = [t for t in self.timestamps if t > cutoff]
        return len(self.timestamps)

    def record(self) -> None:
        self.timestamps.append(time.monotonic())

    @property
    def last_ts(self) -> float:
        return self.timestamps[-1] if self.timestamps else 0.0


@dataclass
class _UserState:
    """每用户的限流状态。"""

    # 分桶窗口
    chat: _WindowEntry = field(default_factory=_WindowEntry)
    command: _WindowEntry = field(default_factory=_WindowEntry)
    upload: _WindowEntry = field(default_factory=_WindowEntry)
    global_: _WindowEntry = field(default_factory=_WindowEntry)
    # 连续被限计数
    consecutive_rejections: int = 0
    # 自动封禁截止时间（monotonic）
    ban_until: float = 0.0
    # 上次发送拒绝消息的时间（monotonic），用于非白名单冷却
    last_reject_ts: float = 0.0


class ChannelRateLimiter:
    """渠道专用速率限制器。

    按消息类型分桶限流，支持自动封禁升级和非白名单冷却。
    与 auth/rate_limit.py 的 RateLimiter 算法相同，但不依赖 FastAPI。
    """

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self.config = config or RateLimitConfig()
        self._users: dict[str, _UserState] = defaultdict(_UserState)

    def check(self, user_id: str, action_type: str) -> RateLimitResult:  # noqa: C901
        """检查用户的某类操作是否被允许。

        Args:
            user_id: 平台用户 ID。
            action_type: "chat" | "command" | "upload"。

        Returns:
            RateLimitResult，allowed=False 时附带提示消息。
        """
        state = self._users[user_id]
        cfg = self.config

        # 1. 自动封禁检查
        now = time.monotonic()
        if state.ban_until > now:
            remaining = state.ban_until - now
            return RateLimitResult(
                allowed=False,
                message=f"🚫 操作过于频繁，已被临时限制，请 {int(remaining)} 秒后再试",
                retry_after=remaining,
            )

        # 2. 全局限流
        result = self._check_window(
            state.global_, cfg.global_per_minute, cfg.global_per_hour, "全局",
        )
        if not result.allowed:
            self._on_rejection(state, user_id)
            return result

        # 3. 分桶限流
        if action_type == "chat":
            result = self._check_window(
                state.chat, cfg.chat_per_minute, cfg.chat_per_hour, "对话",
            )
        elif action_type == "upload":
            # 上传同时计入 upload 桶和 chat 桶
            result = self._check_window(
                state.upload, cfg.upload_per_minute, cfg.upload_per_hour, "文件上传",
            )
            if result.allowed:
                result = self._check_window(
                    state.chat, cfg.chat_per_minute, cfg.chat_per_hour, "对话",
                )
        elif action_type == "command":
            result = self._check_window(
                state.command, cfg.command_per_minute, cfg.command_per_hour, "命令",
            )
        else:
            # 未知类型，仅受全局限流约束
            result = RateLimitResult(allowed=True)

        if not result.allowed:
            self._on_rejection(state, user_id)
            return result

        # 4. 通过 → 记录时间戳
        state.global_.record()
        if action_type == "chat":
            state.chat.record()
        elif action_type == "upload":
            state.upload.record()
            state.chat.record()
        elif action_type == "command":
            state.command.record()

        # 重置连续被限计数
        state.consecutive_rejections = 0
        return RateLimitResult(allowed=True)

    def check_reject_cooldown(self, user_id: str) -> bool:
        """检查是否可以向非白名单用户发送拒绝消息。

        返回 True 表示可以发送 "⛔ 无权限" 提示；
        返回 False 表示仍在冷却期内，应静默丢弃。
        """
        state = self._users[user_id]
        now = time.monotonic()
        if (now - state.last_reject_ts) < self.config.reject_cooldown_seconds:
            return False
        state.last_reject_ts = now
        return True

    def cleanup_stale(self, max_age_seconds: float = 7200) -> int:
        """移除无近期活动的用户条目。返回移除数量。"""
        now = time.monotonic()
        stale_keys: list[str] = []
        for uid, state in self._users.items():
            latest = max(
                state.chat.last_ts,
                state.command.last_ts,
                state.upload.last_ts,
                state.global_.last_ts,
                state.last_reject_ts,
            )
            if latest > 0 and (now - latest) > max_age_seconds:
                stale_keys.append(uid)
            elif latest == 0:
                stale_keys.append(uid)
        for k in stale_keys:
            del self._users[k]
        if stale_keys:
            logger.debug("清理 %d 个不活跃用户的限流记录", len(stale_keys))
        return len(stale_keys)

    # ── 内部方法 ──

    @staticmethod
    def _check_window(
        entry: _WindowEntry,
        per_minute: int,
        per_hour: int,
        label: str,
    ) -> RateLimitResult:
        """检查单个滑动窗口桶。"""
        count_1m = entry.count_in_window(60)
        if count_1m >= per_minute:
            return RateLimitResult(
                allowed=False,
                message=f"⏱ {label}频率过高，每分钟最多 {per_minute} 次，请稍后再试",
                retry_after=60.0,
            )
        count_1h = entry.count_in_window(3600)
        if count_1h >= per_hour:
            return RateLimitResult(
                allowed=False,
                message=f"⏱ {label}频率过高，每小时最多 {per_hour} 次，请稍后再试",
                retry_after=300.0,
            )
        return RateLimitResult(allowed=True)

    def _on_rejection(self, state: _UserState, user_id: str = "") -> None:
        """被限时的升级逻辑。"""
        state.consecutive_rejections += 1
        if state.consecutive_rejections >= self.config.auto_ban_threshold:
            state.ban_until = time.monotonic() + self.config.auto_ban_duration_seconds
            state.consecutive_rejections = 0
            logger.warning(
                "用户 %s 触发自动封禁 %ds",
                user_id,
                int(self.config.auto_ban_duration_seconds),
            )
