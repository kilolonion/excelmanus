"""渠道绑定码管理：生成、验证、过期清理。

Bot 用户通过 /bind 命令获取一次性绑定码，
在 Web 前端输入绑定码完成渠道账号 ↔ ExcelManus 账号的绑定。

流程：
  1. Bot: /bind → 生成 6 位绑定码（TTL 5 分钟）→ 回复用户
  2. Web: 设置页 → 输入绑定码 → POST /api/v1/auth/channel/bind/confirm
  3. 后端验证码有效 + 未过期 → 写入 channel_user_links → 返回成功
"""

from __future__ import annotations

import logging
import random
import string
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from excelmanus.auth.store import UserStore

logger = logging.getLogger(__name__)

# 绑定码默认 TTL（秒）
BIND_CODE_TTL = 300  # 5 分钟

# 绑定码长度
BIND_CODE_LENGTH = 6


@dataclass
class BindCodeEntry:
    """待验证的绑定码条目。"""

    code: str
    channel: str
    platform_id: str
    platform_display_name: str
    created_at: float


class ChannelBindManager:
    """渠道绑定码的生成、验证和过期管理。

    绑定码存储在内存中（进程级），重启后失效。
    对于分布式部署可扩展为 Redis 存储。
    """

    def __init__(
        self,
        user_store: "UserStore | None" = None,
        ttl: float = BIND_CODE_TTL,
    ) -> None:
        self._user_store = user_store
        self._ttl = ttl
        # code → BindCodeEntry
        self._pending: dict[str, BindCodeEntry] = {}
        # (channel, platform_id) → code — 反向索引，防重复生成
        self._reverse: dict[tuple[str, str], str] = {}

    def set_user_store(self, user_store: "UserStore") -> None:
        """延迟注入 UserStore。"""
        self._user_store = user_store

    def _cleanup_expired(self) -> None:
        """清理过期绑定码。"""
        now = time.monotonic()
        expired_codes = [
            code for code, entry in self._pending.items()
            if (now - entry.created_at) > self._ttl
        ]
        for code in expired_codes:
            entry = self._pending.pop(code)
            self._reverse.pop((entry.channel, entry.platform_id), None)
        if expired_codes:
            logger.debug("清理 %d 个过期绑定码", len(expired_codes))

    @staticmethod
    def _generate_code() -> str:
        """生成 6 位数字绑定码。"""
        return "".join(random.choices(string.digits, k=BIND_CODE_LENGTH))

    def create_bind_code(
        self,
        channel: str,
        platform_id: str,
        platform_display_name: str = "",
    ) -> str:
        """为渠道用户生成绑定码。

        每次调用生成新码，旧码自动作废。
        """
        self._cleanup_expired()

        # 作废已有未过期码，重新生成（用户重新 /bind 应得到新码）
        key = (channel, platform_id)
        existing_code = self._reverse.pop(key, None)
        if existing_code:
            self._pending.pop(existing_code, None)

        # 生成不冲突的新码
        for _ in range(100):
            code = self._generate_code()
            if code not in self._pending:
                break
        else:
            raise RuntimeError("无法生成唯一绑定码（请稍后重试）")

        entry = BindCodeEntry(
            code=code,
            channel=channel,
            platform_id=platform_id,
            platform_display_name=platform_display_name,
            created_at=time.monotonic(),
        )
        self._pending[code] = entry
        self._reverse[key] = code
        logger.info(
            "生成绑定码: channel=%s platform_id=%s code=%s",
            channel, platform_id, code,
        )
        return code

    def get_bind_info(self, code: str) -> BindCodeEntry | None:
        """查询绑定码信息（不消费）。

        返回 None 表示无效或已过期。
        """
        self._cleanup_expired()
        return self._pending.get(code)

    def confirm_bind(
        self,
        code: str,
        user_id: str,
    ) -> dict:
        """确认绑定：验证码 + 写入 channel_user_links。

        Args:
            code: 用户输入的绑定码。
            user_id: 当前登录用户的 auth user_id。

        Returns:
            {"ok": True, "channel": ..., "platform_id": ...} 或
            {"ok": False, "error": ...}
        """
        self._cleanup_expired()

        entry = self._pending.get(code)
        if entry is None:
            return {"ok": False, "error": "绑定码无效或已过期"}

        if self._user_store is None:
            return {"ok": False, "error": "用户服务未初始化"}

        # 检查该平台账号是否已绑定其他用户
        existing = self._user_store.get_channel_link(
            entry.channel, entry.platform_id,
        )
        if existing:
            if existing["user_id"] == user_id:
                # 已绑定到同一用户 — 消费码，返回成功
                self._consume_code(code)
                return {
                    "ok": True,
                    "channel": entry.channel,
                    "platform_id": entry.platform_id,
                    "message": "该渠道账号已绑定到当前用户",
                }
            return {
                "ok": False,
                "error": "该渠道账号已绑定到其他用户，请先在原账号解绑",
            }

        # 写入绑定
        try:
            self._user_store.link_channel_user(
                user_id=user_id,
                channel=entry.channel,
                platform_id=entry.platform_id,
                display_name=entry.platform_display_name or None,
            )
        except Exception as exc:
            logger.error("绑定写入失败: %s", exc, exc_info=True)
            return {"ok": False, "error": f"绑定失败: {exc}"}

        # 消费绑定码
        self._consume_code(code)

        return {
            "ok": True,
            "channel": entry.channel,
            "platform_id": entry.platform_id,
        }

    def _consume_code(self, code: str) -> None:
        """消费（删除）绑定码。"""
        entry = self._pending.pop(code, None)
        if entry:
            self._reverse.pop((entry.channel, entry.platform_id), None)

    def check_bind_status(
        self,
        channel: str,
        platform_id: str,
    ) -> str | None:
        """检查渠道用户是否已绑定，返回 auth user_id 或 None。"""
        if self._user_store is None:
            return None
        user = self._user_store.get_user_by_channel(channel, platform_id)
        return user.id if user else None

    def unbind_channel(
        self,
        channel: str,
        platform_id: str,
    ) -> bool:
        """解除指定渠道平台用户的绑定。

        Returns:
            True 表示成功解绑，False 表示未找到绑定或 UserStore 不可用。
        """
        if self._user_store is None:
            return False
        return self._user_store.unlink_channel_by_platform(channel, platform_id)
