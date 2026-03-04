"""群聊策略 & 管理员访问控制测试。

覆盖：
- ChannelMessage.chat_type 默认值和传播
- _group_policy 属性优先级（env > config_kv > 智能默认）
- _group_whitelist / _group_blacklist 读写
- _admin_users 属性（env + config_kv 合并）
- _check_group_access 准入检查
- 群聊拒绝冷却机制
- check_user 管理员放行 + 动态用户
- /admin 命令及子命令
"""

from __future__ import annotations

import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.channels.base import ChannelMessage, ChannelUser
from excelmanus.channels.message_handler import MessageHandler
from excelmanus.channels.session_store import SessionStore


# ── Fake config store ──


class _FakeConfigStore:
    """Mock GlobalConfigStore."""

    def __init__(self):
        self._kv: dict[str, str] = {}

    def get(self, key: str, default: str = "") -> str:
        return self._kv.get(key, default)

    def set(self, key: str, value: str) -> None:
        self._kv[key] = value


# ── Fixtures ──


@pytest.fixture
def mock_adapter():
    adapter = MagicMock()
    adapter.name = "telegram"
    adapter.send_text = AsyncMock()
    adapter.show_typing = AsyncMock()
    adapter.send_progress = AsyncMock()
    adapter.send_file = AsyncMock()
    adapter.send_approval_card = AsyncMock()
    adapter.send_question_card = AsyncMock()
    adapter.update_approval_result = AsyncMock()
    adapter.update_question_result = AsyncMock()
    adapter.send_staged_card = AsyncMock()
    return adapter


@pytest.fixture
def config_store():
    return _FakeConfigStore()


@pytest.fixture
def handler(mock_adapter, config_store):
    api_client = MagicMock()
    api_client.stream_chat = AsyncMock(return_value=iter([]))
    store = SessionStore()
    bind_manager = MagicMock()
    bind_manager.check_bind_status = MagicMock(return_value=None)

    h = MessageHandler(
        adapter=mock_adapter,
        api_client=api_client,
        session_store=store,
        bind_manager=bind_manager,
        config_store=config_store,
    )
    return h


def _make_msg(
    chat_type: str = "private",
    chat_id: str = "chat1",
    user_id: str = "user1",
    text: str = "hello",
    is_command: bool = False,
    command: str = "",
    command_args: list[str] | None = None,
) -> ChannelMessage:
    return ChannelMessage(
        channel="telegram",
        user=ChannelUser(user_id=user_id),
        chat_id=chat_id,
        text=text,
        is_command=is_command,
        command=command,
        command_args=command_args or [],
        chat_type=chat_type,
    )


# ── TestChatTypePlumbing ──


class TestChatTypePlumbing:
    """ChannelMessage chat_type 字段基础测试。"""

    def test_default_private(self):
        msg = ChannelMessage(
            channel="test",
            user=ChannelUser(user_id="1"),
            chat_id="100",
        )
        assert msg.chat_type == "private"

    def test_group_message(self):
        msg = _make_msg(chat_type="group")
        assert msg.chat_type == "group"

    def test_channel_message(self):
        msg = _make_msg(chat_type="channel")
        assert msg.chat_type == "channel"


# ── TestGroupPolicyProperty ──


class TestGroupPolicyProperty:
    """_group_policy 属性优先级和动态切换。"""

    def test_default_allow_without_require_bind(self, handler):
        """非绑定模式默认 allow。"""
        assert handler._group_policy == "allow"

    def test_default_deny_with_require_bind(self, handler, config_store):
        """绑定模式默认 deny。"""
        config_store.set("channel_require_bind", "true")
        assert handler._group_policy == "deny"

    @patch.dict(os.environ, {"EXCELMANUS_CHANNEL_GROUP_POLICY": "whitelist"})
    def test_env_var_override(self, handler, config_store):
        """环境变量覆盖 config_kv 和默认值。"""
        config_store.set("channel_group_policy", "blacklist")
        assert handler._group_policy == "whitelist"

    def test_config_store_override(self, handler, config_store):
        """config_kv 覆盖默认值。"""
        config_store.set("channel_group_policy", "blacklist")
        assert handler._group_policy == "blacklist"

    def test_dynamic_toggle(self, handler, config_store):
        """运行时切换立即生效。"""
        assert handler._group_policy == "allow"
        config_store.set("channel_group_policy", "deny")
        assert handler._group_policy == "deny"
        config_store.set("channel_group_policy", "allow")
        assert handler._group_policy == "allow"


# ── TestGroupAccessCheck ──


class TestGroupAccessCheck:
    """_check_group_access 准入检查。"""

    @pytest.mark.asyncio
    async def test_private_chat_always_allowed(self, handler, config_store, mock_adapter):
        """私聊不受群策略影响。"""
        config_store.set("channel_group_policy", "deny")
        msg = _make_msg(chat_type="private")
        await handler.handle_message(msg)
        # 不应被群策略拦截（可能有其他提示但不是群拒绝消息）
        for call in mock_adapter.send_text.call_args_list:
            assert "仅支持私聊" not in call[0][1]

    @pytest.mark.asyncio
    async def test_group_deny_blocks(self, handler, config_store, mock_adapter):
        """deny 策略拦截群消息。"""
        config_store.set("channel_group_policy", "deny")
        msg = _make_msg(chat_type="group", chat_id="group1")
        await handler.handle_message(msg)
        # 应发送拒绝消息
        mock_adapter.send_text.assert_called()
        sent = mock_adapter.send_text.call_args[0][1]
        assert "仅支持私聊" in sent

    @pytest.mark.asyncio
    async def test_group_allow_passes(self, handler, config_store, mock_adapter):
        """allow 策略放行群消息。"""
        config_store.set("channel_group_policy", "allow")
        msg = _make_msg(chat_type="group", chat_id="group1")
        await handler.handle_message(msg)
        # 不应有群拒绝消息
        for call in mock_adapter.send_text.call_args_list:
            assert "仅支持私聊" not in call[0][1]

    @pytest.mark.asyncio
    async def test_group_whitelist_pass(self, handler, config_store, mock_adapter):
        """白名单内群放行。"""
        config_store.set("channel_group_policy", "whitelist")
        config_store.set("channel_group_whitelist", json.dumps(["group1"]))
        msg = _make_msg(chat_type="group", chat_id="group1")
        await handler.handle_message(msg)
        for call in mock_adapter.send_text.call_args_list:
            assert "未获授权" not in call[0][1]

    @pytest.mark.asyncio
    async def test_group_whitelist_block(self, handler, config_store, mock_adapter):
        """白名单外群拦截。"""
        config_store.set("channel_group_policy", "whitelist")
        config_store.set("channel_group_whitelist", json.dumps(["other_group"]))
        msg = _make_msg(chat_type="group", chat_id="group1")
        await handler.handle_message(msg)
        mock_adapter.send_text.assert_called()
        sent = mock_adapter.send_text.call_args[0][1]
        assert "未获授权" in sent

    @pytest.mark.asyncio
    async def test_group_blacklist_block(self, handler, config_store, mock_adapter):
        """黑名单内群拦截。"""
        config_store.set("channel_group_policy", "blacklist")
        config_store.set("channel_group_blacklist", json.dumps(["group1"]))
        msg = _make_msg(chat_type="group", chat_id="group1")
        await handler.handle_message(msg)
        mock_adapter.send_text.assert_called()
        sent = mock_adapter.send_text.call_args[0][1]
        assert "已被禁止" in sent

    @pytest.mark.asyncio
    async def test_group_blacklist_pass(self, handler, config_store, mock_adapter):
        """黑名单外群放行。"""
        config_store.set("channel_group_policy", "blacklist")
        config_store.set("channel_group_blacklist", json.dumps(["other_group"]))
        msg = _make_msg(chat_type="group", chat_id="group1")
        await handler.handle_message(msg)
        for call in mock_adapter.send_text.call_args_list:
            assert "已被禁止" not in call[0][1]


# ── TestGroupDenyCooldown ──


class TestGroupDenyCooldown:
    """群聊拒绝冷却机制。"""

    @pytest.mark.asyncio
    async def test_deny_sends_message_first_time(self, handler, config_store, mock_adapter):
        """首次拒绝发送提示。"""
        config_store.set("channel_group_policy", "deny")
        msg = _make_msg(chat_type="group", chat_id="group1")
        await handler.handle_message(msg)
        assert mock_adapter.send_text.called

    @pytest.mark.asyncio
    async def test_deny_silent_within_cooldown(self, handler, config_store, mock_adapter):
        """冷却期内静默（不重复发送拒绝消息）。"""
        config_store.set("channel_group_policy", "deny")
        # 第一次 — 应发送
        msg = _make_msg(chat_type="group", chat_id="group_cool")
        await handler.handle_message(msg)
        call_count_1 = mock_adapter.send_text.call_count

        # 第二次 — 应静默（冷却期内）
        mock_adapter.send_text.reset_mock()
        msg2 = _make_msg(chat_type="group", chat_id="group_cool")
        await handler.handle_message(msg2)
        # send_text 不应被调用（拒绝消息在冷却中）
        assert mock_adapter.send_text.call_count == 0


# ── TestAdminUsers ──


class TestAdminUsers:
    """管理员用户检测。"""

    @patch.dict(os.environ, {"EXCELMANUS_CHANNEL_ADMINS": "admin1,admin2"})
    def test_admin_from_env_var(self, handler):
        """环境变量设置管理员。"""
        assert handler._is_admin("admin1")
        assert handler._is_admin("admin2")
        assert not handler._is_admin("user1")

    def test_admin_from_config_store(self, handler, config_store):
        """config_kv 设置管理员。"""
        config_store.set("channel_admin_users", "db_admin1,db_admin2")
        assert handler._is_admin("db_admin1")
        assert handler._is_admin("db_admin2")
        assert not handler._is_admin("user1")

    @patch.dict(os.environ, {"EXCELMANUS_CHANNEL_ADMINS": "admin1"})
    def test_admin_merge_env_and_config(self, handler, config_store):
        """环境变量和 config_kv 管理员合并。"""
        config_store.set("channel_admin_users", "db_admin1")
        assert handler._is_admin("admin1")
        assert handler._is_admin("db_admin1")

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self, handler, mock_adapter):
        """非管理员执行 /admin 被拒。"""
        msg = _make_msg(
            is_command=True, command="admin", user_id="regular_user",
        )
        await handler.handle_message(msg)
        mock_adapter.send_text.assert_called()
        sent = mock_adapter.send_text.call_args[0][1]
        assert "管理员权限" in sent

    @patch.dict(os.environ, {"EXCELMANUS_CHANNEL_ADMINS": "admin1"})
    def test_admin_always_passes_check_user(self, handler):
        """管理员绕过 allowed_users 检查。"""
        handler.allowed_users = {"other_user"}
        assert handler.check_user("admin1")
        assert not handler.check_user("blocked_user")

    @patch.dict(os.environ, {"EXCELMANUS_CHANNEL_ADMINS": "admin1"})
    @pytest.mark.asyncio
    async def test_admin_bypasses_group_deny(self, handler, config_store, mock_adapter):
        """管理员在 deny 策略下仍可使用群聊。"""
        config_store.set("channel_group_policy", "deny")
        msg = _make_msg(chat_type="group", chat_id="group1", user_id="admin1")
        await handler.handle_message(msg)
        # 不应发送群拒绝消息
        for call in mock_adapter.send_text.call_args_list:
            assert "仅支持私聊" not in call[0][1]


# ── TestDynamicAllowedUsers ──


class TestDynamicAllowedUsers:
    """动态允许用户（config_kv）。"""

    def test_dynamic_users_from_config(self, handler, config_store):
        """从 config_kv 读取动态用户。"""
        config_store.set("channel_allowed_users", json.dumps(["u1", "u2"]))
        assert handler._dynamic_allowed_users == {"u1", "u2"}

    def test_dynamic_users_empty(self, handler):
        """无配置时返回空集合。"""
        assert handler._dynamic_allowed_users == set()

    def test_check_user_with_dynamic(self, handler, config_store):
        """check_user 同时检查静态和动态用户。"""
        handler.allowed_users = {"static_user"}
        config_store.set("channel_allowed_users", json.dumps(["dynamic_user"]))
        assert handler.check_user("static_user")
        assert handler.check_user("dynamic_user")
        assert not handler.check_user("unknown_user")


# ── TestAdminCommands ──


@patch.dict(os.environ, {"EXCELMANUS_CHANNEL_ADMINS": "admin1"})
class TestAdminCommands:
    """管理员命令测试。"""

    @pytest.mark.asyncio
    async def test_admin_status(self, handler, mock_adapter):
        """/admin 无参数 → 显示状态汇总。"""
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
        )
        await handler.handle_message(msg)
        mock_adapter.send_text.assert_called()
        sent = mock_adapter.send_text.call_args[0][1]
        assert "管理状态" in sent
        assert "群聊策略" in sent

    @pytest.mark.asyncio
    async def test_admin_group_set_policy(self, handler, config_store, mock_adapter):
        """/admin group deny → 设置群聊策略。"""
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
            command_args=["group", "deny"],
        )
        await handler.handle_message(msg)
        assert config_store.get("channel_group_policy") == "deny"
        sent = mock_adapter.send_text.call_args[0][1]
        assert "deny" in sent

    @pytest.mark.asyncio
    async def test_admin_group_invalid_policy(self, handler, mock_adapter):
        """/admin group invalid → 报错。"""
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
            command_args=["group", "invalid"],
        )
        await handler.handle_message(msg)
        sent = mock_adapter.send_text.call_args[0][1]
        assert "无效策略" in sent

    @pytest.mark.asyncio
    async def test_admin_allowgroup(self, handler, config_store, mock_adapter):
        """/admin allowgroup → 白名单当前群。"""
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
            chat_id="group123", command_args=["allowgroup"],
        )
        await handler.handle_message(msg)
        wl = json.loads(config_store.get("channel_group_whitelist", "[]"))
        assert "group123" in wl
        sent = mock_adapter.send_text.call_args[0][1]
        assert "白名单" in sent

    @pytest.mark.asyncio
    async def test_admin_blockgroup(self, handler, config_store, mock_adapter):
        """/admin blockgroup → 黑名单当前群。"""
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
            chat_id="group456", command_args=["blockgroup"],
        )
        await handler.handle_message(msg)
        bl = json.loads(config_store.get("channel_group_blacklist", "[]"))
        assert "group456" in bl
        sent = mock_adapter.send_text.call_args[0][1]
        assert "黑名单" in sent

    @pytest.mark.asyncio
    async def test_admin_removegroup(self, handler, config_store, mock_adapter):
        """/admin removegroup → 从白/黑名单移除。"""
        config_store.set("channel_group_whitelist", json.dumps(["group789"]))
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
            command_args=["removegroup", "group789"],
        )
        await handler.handle_message(msg)
        wl = json.loads(config_store.get("channel_group_whitelist", "[]"))
        assert "group789" not in wl
        sent = mock_adapter.send_text.call_args[0][1]
        assert "移除" in sent

    @pytest.mark.asyncio
    async def test_admin_listgroups(self, handler, config_store, mock_adapter):
        """/admin listgroups → 列出名单。"""
        config_store.set("channel_group_whitelist", json.dumps(["wl1"]))
        config_store.set("channel_group_blacklist", json.dumps(["bl1"]))
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
            command_args=["listgroups"],
        )
        await handler.handle_message(msg)
        sent = mock_adapter.send_text.call_args[0][1]
        assert "wl1" in sent
        assert "bl1" in sent

    @pytest.mark.asyncio
    async def test_admin_adduser(self, handler, config_store, mock_adapter):
        """/admin adduser → 添加动态用户。"""
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
            command_args=["adduser", "new_user"],
        )
        await handler.handle_message(msg)
        users = json.loads(config_store.get("channel_allowed_users", "[]"))
        assert "new_user" in users
        sent = mock_adapter.send_text.call_args[0][1]
        assert "new_user" in sent

    @pytest.mark.asyncio
    async def test_admin_removeuser(self, handler, config_store, mock_adapter):
        """/admin removeuser → 移除动态用户。"""
        config_store.set("channel_allowed_users", json.dumps(["old_user"]))
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
            command_args=["removeuser", "old_user"],
        )
        await handler.handle_message(msg)
        users = json.loads(config_store.get("channel_allowed_users", "[]"))
        assert "old_user" not in users

    @pytest.mark.asyncio
    async def test_admin_listusers(self, handler, config_store, mock_adapter):
        """/admin listusers → 列出用户。"""
        handler.allowed_users = {"static1"}
        config_store.set("channel_allowed_users", json.dumps(["dynamic1"]))
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
            command_args=["listusers"],
        )
        await handler.handle_message(msg)
        sent = mock_adapter.send_text.call_args[0][1]
        assert "static1" in sent
        assert "dynamic1" in sent

    @pytest.mark.asyncio
    async def test_admin_addadmin(self, handler, config_store, mock_adapter):
        """/admin addadmin → 添加管理员。"""
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
            command_args=["addadmin", "new_admin"],
        )
        await handler.handle_message(msg)
        raw = config_store.get("channel_admin_users", "")
        assert "new_admin" in raw
        sent = mock_adapter.send_text.call_args[0][1]
        assert "new_admin" in sent

    @pytest.mark.asyncio
    async def test_admin_removeadmin(self, handler, config_store, mock_adapter):
        """/admin removeadmin → 移除 config_kv 管理员。"""
        config_store.set("channel_admin_users", "db_admin")
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
            command_args=["removeadmin", "db_admin"],
        )
        await handler.handle_message(msg)
        raw = config_store.get("channel_admin_users", "")
        assert "db_admin" not in raw

    @pytest.mark.asyncio
    async def test_admin_removeadmin_env_protected(self, handler, mock_adapter):
        """/admin removeadmin 环境变量管理员 → 被拒。"""
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
            command_args=["removeadmin", "admin1"],
        )
        await handler.handle_message(msg)
        sent = mock_adapter.send_text.call_args[0][1]
        assert "环境变量管理员" in sent

    @pytest.mark.asyncio
    async def test_admin_unknown_subcommand(self, handler, mock_adapter):
        """/admin unknown → 提示可用子命令。"""
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
            command_args=["unknown"],
        )
        await handler.handle_message(msg)
        sent = mock_adapter.send_text.call_args[0][1]
        assert "未知子命令" in sent

    @pytest.mark.asyncio
    async def test_admin_allowgroup_with_explicit_id(self, handler, config_store, mock_adapter):
        """/admin allowgroup <chat_id> → 白名单指定群。"""
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
            chat_id="my_chat", command_args=["allowgroup", "explicit_group"],
        )
        await handler.handle_message(msg)
        wl = json.loads(config_store.get("channel_group_whitelist", "[]"))
        assert "explicit_group" in wl

    @pytest.mark.asyncio
    async def test_admin_allowgroup_duplicate(self, handler, config_store, mock_adapter):
        """/admin allowgroup 重复添加 → 提示已存在。"""
        config_store.set("channel_group_whitelist", json.dumps(["group1"]))
        msg = _make_msg(
            is_command=True, command="admin", user_id="admin1",
            command_args=["allowgroup", "group1"],
        )
        await handler.handle_message(msg)
        sent = mock_adapter.send_text.call_args[0][1]
        assert "已在白名单" in sent
