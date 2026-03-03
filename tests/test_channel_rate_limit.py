"""渠道速率限制单元测试。"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.channels.base import ChannelMessage, ChannelUser
from excelmanus.channels.message_handler import MessageHandler, PendingInteraction
from excelmanus.channels.rate_limit import (
    ChannelRateLimiter,
    RateLimitConfig,
    RateLimitResult,
    _UserState,
    _WindowEntry,
)
from excelmanus.channels.session_store import SessionStore


# ── _WindowEntry 测试 ──


class TestWindowEntry:
    def test_empty(self):
        e = _WindowEntry()
        assert e.count_in_window(60) == 0
        assert e.last_ts == 0.0

    def test_record_and_count(self):
        e = _WindowEntry()
        e.record()
        e.record()
        assert e.count_in_window(60) == 2
        assert e.last_ts > 0

    def test_expired_entries_pruned(self):
        e = _WindowEntry()
        # 手动插入过期时间戳
        old = time.monotonic() - 120
        e.timestamps = [old, old + 1]
        e.record()  # 当前
        assert e.count_in_window(60) == 1  # 只剩当前那条


# ── RateLimitConfig 测试 ──


class TestRateLimitConfig:
    def test_defaults(self):
        cfg = RateLimitConfig()
        assert cfg.chat_per_minute == 5
        assert cfg.chat_per_hour == 30
        assert cfg.auto_ban_threshold == 10

    def test_from_env(self):
        env = {
            "EXCELMANUS_CHANNEL_RATE_CHAT_PM": "3",
            "EXCELMANUS_CHANNEL_RATE_BAN_THRESHOLD": "5",
            "EXCELMANUS_CHANNEL_RATE_BAN_DURATION": "120.0",
        }
        with patch.dict("os.environ", env, clear=False):
            cfg = RateLimitConfig.from_env()
        assert cfg.chat_per_minute == 3
        assert cfg.auto_ban_threshold == 5
        assert cfg.auto_ban_duration_seconds == 120.0
        # 未设置的使用默认值
        assert cfg.command_per_minute == 15

    def test_from_env_invalid_fallback(self):
        env = {"EXCELMANUS_CHANNEL_RATE_CHAT_PM": "not_a_number"}
        with patch.dict("os.environ", env, clear=False):
            cfg = RateLimitConfig.from_env()
        assert cfg.chat_per_minute == 5  # 回退默认值


# ── ChannelRateLimiter 核心测试 ──


class TestChannelRateLimiter:
    def test_allow_within_limit(self):
        rl = ChannelRateLimiter(RateLimitConfig(chat_per_minute=3))
        for _ in range(3):
            r = rl.check("u1", "chat")
            assert r.allowed

    def test_block_over_per_minute(self):
        rl = ChannelRateLimiter(RateLimitConfig(chat_per_minute=2, chat_per_hour=100))
        assert rl.check("u1", "chat").allowed
        assert rl.check("u1", "chat").allowed
        r = rl.check("u1", "chat")
        assert not r.allowed
        assert "对话" in r.message
        assert "每分钟" in r.message

    def test_block_over_per_hour(self):
        rl = ChannelRateLimiter(RateLimitConfig(
            chat_per_minute=100, chat_per_hour=3,
        ))
        for _ in range(3):
            assert rl.check("u1", "chat").allowed
        r = rl.check("u1", "chat")
        assert not r.allowed
        assert "每小时" in r.message

    def test_command_bucket_separate(self):
        rl = ChannelRateLimiter(RateLimitConfig(
            chat_per_minute=1, command_per_minute=3, global_per_minute=100,
        ))
        # chat 到达上限
        assert rl.check("u1", "chat").allowed
        assert not rl.check("u1", "chat").allowed
        # command 仍可用
        assert rl.check("u1", "command").allowed

    def test_upload_counts_in_both_buckets(self):
        rl = ChannelRateLimiter(RateLimitConfig(
            upload_per_minute=2, chat_per_minute=3,
            upload_per_hour=100, chat_per_hour=100,
            global_per_minute=100, global_per_hour=1000,
        ))
        assert rl.check("u1", "upload").allowed
        assert rl.check("u1", "upload").allowed
        # upload 桶耗尽
        r = rl.check("u1", "upload")
        assert not r.allowed
        assert "文件上传" in r.message
        # chat 桶只用了 2 / 3，仍可
        assert rl.check("u1", "chat").allowed

    def test_global_limit(self):
        rl = ChannelRateLimiter(RateLimitConfig(
            chat_per_minute=100, command_per_minute=100,
            global_per_minute=3, global_per_hour=1000,
        ))
        assert rl.check("u1", "chat").allowed
        assert rl.check("u1", "command").allowed
        assert rl.check("u1", "chat").allowed
        r = rl.check("u1", "command")
        assert not r.allowed
        assert "全局" in r.message

    def test_per_user_isolation(self):
        rl = ChannelRateLimiter(RateLimitConfig(chat_per_minute=1))
        assert rl.check("u1", "chat").allowed
        assert not rl.check("u1", "chat").allowed
        # u2 不受影响
        assert rl.check("u2", "chat").allowed

    def test_auto_ban_after_threshold(self):
        cfg = RateLimitConfig(
            chat_per_minute=1, chat_per_hour=100,
            global_per_minute=100, global_per_hour=1000,
            auto_ban_threshold=3, auto_ban_duration_seconds=60.0,
        )
        rl = ChannelRateLimiter(cfg)
        # 消耗配额
        assert rl.check("u1", "chat").allowed
        # 连续 3 次被拒 → 触发封禁
        for _ in range(3):
            r = rl.check("u1", "chat")
            assert not r.allowed
        # 第 4 次应该看到封禁消息
        r = rl.check("u1", "chat")
        assert not r.allowed
        assert "临时限制" in r.message

    def test_auto_ban_expires(self):
        cfg = RateLimitConfig(
            chat_per_minute=1, chat_per_hour=100,
            global_per_minute=100, global_per_hour=1000,
            auto_ban_threshold=2, auto_ban_duration_seconds=0.1,
        )
        rl = ChannelRateLimiter(cfg)
        assert rl.check("u1", "chat").allowed
        rl.check("u1", "chat")
        rl.check("u1", "chat")
        # 应该被封禁
        r = rl.check("u1", "chat")
        assert not r.allowed
        assert "临时限制" in r.message
        # 等封禁过期
        time.sleep(0.15)
        # 封禁过期后，但 per-minute 窗口仍有记录 → 仍被限
        # 需要 per-minute 窗口也过期才能通过
        # 这里只验证封禁消息不再出现
        r2 = rl.check("u1", "chat")
        assert "临时限制" not in (r2.message or "")

    def test_consecutive_rejections_reset_on_success(self):
        cfg = RateLimitConfig(
            chat_per_minute=2, chat_per_hour=100,
            global_per_minute=100, global_per_hour=1000,
            auto_ban_threshold=5,
        )
        rl = ChannelRateLimiter(cfg)
        assert rl.check("u1", "chat").allowed
        assert rl.check("u1", "chat").allowed
        # 被拒 2 次
        rl.check("u1", "chat")
        rl.check("u1", "chat")
        state = rl._users["u1"]
        assert state.consecutive_rejections == 2
        # 换 command 桶（仍有配额）→ 通过 → 计数重置
        assert rl.check("u1", "command").allowed
        assert state.consecutive_rejections == 0


class TestRejectCooldown:
    def test_first_reject_allowed(self):
        rl = ChannelRateLimiter(RateLimitConfig(reject_cooldown_seconds=10.0))
        assert rl.check_reject_cooldown("u1") is True

    def test_second_reject_within_cooldown(self):
        rl = ChannelRateLimiter(RateLimitConfig(reject_cooldown_seconds=10.0))
        assert rl.check_reject_cooldown("u1") is True
        assert rl.check_reject_cooldown("u1") is False

    def test_reject_after_cooldown(self):
        rl = ChannelRateLimiter(RateLimitConfig(reject_cooldown_seconds=0.05))
        assert rl.check_reject_cooldown("u1") is True
        time.sleep(0.12)  # 留足裕量以应对 Windows 定时器精度
        assert rl.check_reject_cooldown("u1") is True


class TestCleanupStale:
    def test_cleanup_removes_stale(self):
        rl = ChannelRateLimiter()
        # 创建条目并手动老化
        rl.check("u1", "chat")
        state = rl._users["u1"]
        old = time.monotonic() - 10000
        state.chat.timestamps = [old]
        state.global_.timestamps = [old]
        removed = rl.cleanup_stale(max_age_seconds=100)
        assert removed == 1
        assert "u1" not in rl._users

    def test_cleanup_keeps_active(self):
        rl = ChannelRateLimiter()
        rl.check("u1", "chat")
        removed = rl.cleanup_stale(max_age_seconds=100)
        assert removed == 0
        assert "u1" in rl._users


# ── MessageHandler 集成测试 ──


def _make_handler(
    allowed_users: set[str] | None = None,
    rate_limit_config: RateLimitConfig | None = None,
) -> tuple[MessageHandler, AsyncMock]:
    """创建带 mock adapter 的 MessageHandler。"""
    adapter = AsyncMock()
    adapter.name = "test"
    api = AsyncMock()
    store = MagicMock(spec=SessionStore)
    store.get.return_value = None
    store.get_mode.return_value = "write"
    handler = MessageHandler(
        adapter=adapter,
        api_client=api,
        session_store=store,
        allowed_users=allowed_users,
        rate_limit_config=rate_limit_config,
    )
    return handler, adapter


def _msg(
    user_id: str = "u1",
    chat_id: str = "c1",
    text: str = "hello",
    is_command: bool = False,
    command: str = "",
    callback_data: str | None = None,
    files: list | None = None,
    images: list | None = None,
) -> ChannelMessage:
    return ChannelMessage(
        channel="test",
        user=ChannelUser(user_id=user_id),
        chat_id=chat_id,
        text=text,
        is_command=is_command,
        command=command,
        command_args=[],
        callback_data=callback_data,
        files=files or [],
        images=images or [],
    )


class TestClassifyAction:
    def test_command(self):
        msg = _msg(is_command=True, command="model")
        assert MessageHandler._classify_action(msg) == "command"

    def test_callback(self):
        msg = _msg(callback_data="approve:123")
        assert MessageHandler._classify_action(msg) == "command"

    def test_text(self):
        msg = _msg(text="分析数据")
        assert MessageHandler._classify_action(msg) == "chat"

    def test_files(self):
        from excelmanus.channels.base import FileAttachment
        msg = _msg(files=[FileAttachment("a.xlsx", b"data")])
        assert MessageHandler._classify_action(msg) == "upload"


class TestIsExempt:
    def test_abort_exempt(self):
        handler, _ = _make_handler()
        msg = _msg(is_command=True, command="abort")
        assert handler._is_exempt(msg) is True

    def test_new_exempt(self):
        handler, _ = _make_handler()
        msg = _msg(is_command=True, command="new")
        assert handler._is_exempt(msg) is True

    def test_model_not_exempt(self):
        handler, _ = _make_handler()
        msg = _msg(is_command=True, command="model")
        assert handler._is_exempt(msg) is False

    def test_callback_with_pending(self):
        handler, _ = _make_handler()
        pk = handler._pending_key("c1", "u1")
        handler._pending[pk] = PendingInteraction(
            interaction_type="approval",
            interaction_id="a1",
            session_id="s1",
            chat_id="c1",
        )
        msg = _msg(callback_data="approve:a1")
        assert handler._is_exempt(msg) is True

    def test_callback_without_pending(self):
        handler, _ = _make_handler()
        msg = _msg(callback_data="approve:a1")
        assert handler._is_exempt(msg) is False

    def test_text_answer_to_pending_question(self):
        handler, _ = _make_handler()
        pk = handler._pending_key("c1", "u1")
        handler._pending[pk] = PendingInteraction(
            interaction_type="question",
            interaction_id="q1",
            session_id="s1",
            chat_id="c1",
        )
        msg = _msg(text="选A")
        assert handler._is_exempt(msg) is True

    def test_text_no_pending_not_exempt(self):
        handler, _ = _make_handler()
        msg = _msg(text="随便聊")
        assert handler._is_exempt(msg) is False


@pytest.mark.asyncio
class TestHandleMessageRateLimit:
    async def test_unauthorized_with_cooldown(self):
        """非白名单用户首次收到拒绝消息，短时间内再发不再回复。"""
        handler, adapter = _make_handler(
            allowed_users={"allowed_user"},
            rate_limit_config=RateLimitConfig(reject_cooldown_seconds=10.0),
        )
        msg1 = _msg(user_id="intruder", text="hi")
        await handler.handle_message(msg1)
        assert adapter.send_text.call_count == 1
        assert "无权限" in adapter.send_text.call_args[0][1]

        adapter.send_text.reset_mock()
        msg2 = _msg(user_id="intruder", text="hi again")
        await handler.handle_message(msg2)
        # 冷却期内不回复
        assert adapter.send_text.call_count == 0

    async def test_chat_rate_limited(self):
        """白名单用户超出对话限额被拒。"""
        handler, adapter = _make_handler(
            rate_limit_config=RateLimitConfig(
                chat_per_minute=1, chat_per_hour=100,
                global_per_minute=100, global_per_hour=1000,
            ),
        )
        # mock stream_chat to avoid real API calls
        handler.api.stream_chat = AsyncMock(return_value=MagicMock(
            reply="ok", session_id="s1", approval=None, question=None,
            file_downloads=[], tool_calls=[],
        ))

        msg1 = _msg(text="first")
        await handler.handle_message(msg1)

        adapter.send_text.reset_mock()
        msg2 = _msg(text="second")
        await handler.handle_message(msg2)
        # 第二条被限流
        assert adapter.send_text.call_count == 1
        assert "频率过高" in adapter.send_text.call_args[0][1]

    async def test_exempt_command_bypasses_limit(self):
        """即使被限流，/abort 仍可执行。"""
        handler, adapter = _make_handler(
            rate_limit_config=RateLimitConfig(
                command_per_minute=0,  # 全部命令限流
                global_per_minute=100, global_per_hour=1000,
            ),
        )
        handler.sessions.get.return_value = "sess1"
        handler.api.abort = AsyncMock()

        msg = _msg(is_command=True, command="abort", text="/abort")
        await handler.handle_message(msg)
        # /abort 豁免，应该被执行
        handler.api.abort.assert_called_once()

    async def test_help_exempt(self):
        """/help 也是豁免命令。"""
        handler, adapter = _make_handler(
            rate_limit_config=RateLimitConfig(
                command_per_minute=0,
                global_per_minute=100, global_per_hour=1000,
            ),
        )
        msg = _msg(is_command=True, command="help", text="/help")
        await handler.handle_message(msg)
        # /help 应发送帮助文本
        assert adapter.send_text.call_count == 1
        assert "命令" in adapter.send_text.call_args[0][1]
