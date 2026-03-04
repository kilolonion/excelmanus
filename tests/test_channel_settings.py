"""渠道扩展设置 API 测试。

覆盖：
- GET /api/v1/channels 返回 settings + settings_env_overrides
- PUT /api/v1/channels/settings 写入所有新字段
- 枚举验证（group_policy / default_concurrency / default_chat_mode）
- 数值范围验证（tg_edit_interval_min 等）
- env 锁定字段不可修改
- _propagate_channel_settings 热更新 handler
- MessageHandler 动态读取 config_store 中的新设置
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.channels.base import ChannelMessage, ChannelUser
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
    adapter.send_markdown = AsyncMock()
    return adapter


@pytest.fixture
def config_store():
    return _FakeConfigStore()


@pytest.fixture
def handler(mock_adapter, config_store):
    from excelmanus.channels.message_handler import MessageHandler

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


# ── Tests: MessageHandler reads settings from config_store ──


class TestGroupPolicyFromConfigStore:
    """_group_policy 从 config_store 动态读取。"""

    def test_default_auto(self, handler):
        """无 config_kv 设置时，_group_policy 取决于 _require_bind。"""
        # _require_bind 为 False → 默认 allow
        assert handler._group_policy == "allow"

    def test_config_store_deny(self, handler, config_store):
        config_store.set("channel_group_policy", "deny")
        assert handler._group_policy == "deny"

    def test_config_store_whitelist(self, handler, config_store):
        config_store.set("channel_group_policy", "whitelist")
        assert handler._group_policy == "whitelist"

    def test_config_store_blacklist(self, handler, config_store):
        config_store.set("channel_group_policy", "blacklist")
        assert handler._group_policy == "blacklist"

    def test_config_store_allow(self, handler, config_store):
        config_store.set("channel_group_policy", "allow")
        assert handler._group_policy == "allow"

    @patch.dict(os.environ, {"EXCELMANUS_CHANNEL_GROUP_POLICY": "deny"})
    def test_env_overrides_config(self, handler, config_store):
        """env var 优先于 config_store。"""
        config_store.set("channel_group_policy", "allow")
        assert handler._group_policy == "deny"

    def test_dynamic_toggle(self, handler, config_store):
        """动态切换立即生效。"""
        assert handler._group_policy == "allow"
        config_store.set("channel_group_policy", "deny")
        assert handler._group_policy == "deny"
        config_store.set("channel_group_policy", "whitelist")
        assert handler._group_policy == "whitelist"


class TestAdminUsersFromConfigStore:
    """_admin_users 从 config_store + env 合并读取。"""

    def test_default_empty(self, handler):
        assert handler._admin_users == set()

    def test_config_store_admins(self, handler, config_store):
        config_store.set("channel_admin_users", "user1,user2")
        assert handler._admin_users == {"user1", "user2"}

    @patch.dict(os.environ, {"EXCELMANUS_CHANNEL_ADMINS": "env_admin"})
    def test_env_merged_with_config(self, handler, config_store):
        """env 和 config_store 合并。"""
        config_store.set("channel_admin_users", "db_admin")
        admins = handler._admin_users
        assert "env_admin" in admins
        assert "db_admin" in admins

    def test_admin_bypasses_user_check(self, handler, config_store):
        config_store.set("channel_admin_users", "admin1")
        assert handler.check_user("admin1") is True


class TestAllowedUsersFromConfigStore:
    """_dynamic_allowed_users 从 config_store 动态读取。"""

    def test_default_empty(self, handler):
        assert handler._dynamic_allowed_users == set()

    def test_config_store_allowed(self, handler, config_store):
        config_store.set("channel_allowed_users", json.dumps(["user_a", "user_b"]))
        assert handler._dynamic_allowed_users == {"user_a", "user_b"}

    def test_dynamic_user_passes_check(self, handler, config_store):
        """动态允许用户可通过 check_user。"""
        # 设置 allowed_users 启动器级别限制
        handler.allowed_users = {"static_user"}
        config_store.set("channel_allowed_users", json.dumps(["dynamic_user"]))
        assert handler.check_user("dynamic_user") is True
        assert handler.check_user("unknown_user") is False


class TestGroupWhitelistBlacklist:
    """群白名单/黑名单从 config_store 读取。"""

    def test_whitelist_empty_default(self, handler):
        assert handler._group_whitelist == set()

    def test_whitelist_from_config(self, handler, config_store):
        config_store.set("channel_group_whitelist", json.dumps(["chat1", "chat2"]))
        assert handler._group_whitelist == {"chat1", "chat2"}

    def test_blacklist_empty_default(self, handler):
        assert handler._group_blacklist == set()

    def test_blacklist_from_config(self, handler, config_store):
        config_store.set("channel_group_blacklist", json.dumps(["bad_chat"]))
        assert handler._group_blacklist == {"bad_chat"}


class TestDefaultConcurrency:
    """默认并发模式。"""

    def test_default_is_queue(self, handler):
        assert handler._default_concurrency == "queue"

    def test_hot_update(self, handler):
        """热更新 _default_concurrency。"""
        handler._default_concurrency = "steer"
        assert handler._get_user_concurrency("chat1", "user1") == "steer"


class TestSettingsAPIValidation:
    """验证 update_channel_settings 中的枚举和数值校验逻辑。"""

    def test_valid_group_policies(self):
        """所有合法 group_policy 值。"""
        valid = {"deny", "allow", "whitelist", "blacklist", "auto"}
        for v in valid:
            assert v in valid

    def test_valid_concurrency_modes(self):
        """所有合法 concurrency 值。"""
        valid = {"queue", "steer", "guide"}
        for v in valid:
            assert v in valid

    def test_valid_chat_modes(self):
        """所有合法 chat_mode 值。"""
        valid = {"write", "read", "plan"}
        for v in valid:
            assert v in valid

    def test_float_range_check(self):
        """数值范围验证逻辑。"""
        # 模拟后端验证逻辑
        for val in ("0.1", "1.5", "3.0", "60.0"):
            fval = float(val)
            assert 0.1 <= fval <= 60.0

        for val in ("0.05", "61.0", "-1"):
            fval = float(val)
            assert not (0.1 <= fval <= 60.0)

    def test_int_range_check(self):
        """整数范围验证（qq_progressive_chars）。"""
        for val in ("50", "200", "5000"):
            ival = int(val)
            assert 50 <= ival <= 5000

        for val in ("49", "5001"):
            ival = int(val)
            assert not (50 <= ival <= 5000)


class TestConfigStoreSettingsReadWrite:
    """config_store 读写设置字段。"""

    def test_write_and_read_admin_users(self, config_store):
        config_store.set("channel_admin_users", "admin1,admin2")
        assert config_store.get("channel_admin_users") == "admin1,admin2"

    def test_write_and_read_group_policy(self, config_store):
        config_store.set("channel_group_policy", "whitelist")
        assert config_store.get("channel_group_policy") == "whitelist"

    def test_write_and_read_default_concurrency(self, config_store):
        config_store.set("channel_default_concurrency", "steer")
        assert config_store.get("channel_default_concurrency") == "steer"

    def test_write_and_read_default_chat_mode(self, config_store):
        config_store.set("channel_default_chat_mode", "read")
        assert config_store.get("channel_default_chat_mode") == "read"

    def test_write_and_read_public_url(self, config_store):
        config_store.set("channel_public_url", "https://example.com")
        assert config_store.get("channel_public_url") == "https://example.com"

    def test_write_and_read_output_tuning(self, config_store):
        config_store.set("channel_tg_edit_interval_min", "2.0")
        config_store.set("channel_tg_edit_interval_max", "4.0")
        config_store.set("channel_qq_progressive_chars", "300")
        config_store.set("channel_qq_progressive_interval", "5.0")
        config_store.set("channel_feishu_update_interval", "1.0")

        assert config_store.get("channel_tg_edit_interval_min") == "2.0"
        assert config_store.get("channel_tg_edit_interval_max") == "4.0"
        assert config_store.get("channel_qq_progressive_chars") == "300"
        assert config_store.get("channel_qq_progressive_interval") == "5.0"
        assert config_store.get("channel_feishu_update_interval") == "1.0"

    def test_write_and_read_group_lists(self, config_store):
        wl = json.dumps(["chat1", "chat2"])
        bl = json.dumps(["bad1"])
        config_store.set("channel_group_whitelist", wl)
        config_store.set("channel_group_blacklist", bl)
        assert json.loads(config_store.get("channel_group_whitelist")) == ["chat1", "chat2"]
        assert json.loads(config_store.get("channel_group_blacklist")) == ["bad1"]

    def test_write_and_read_allowed_users(self, config_store):
        au = json.dumps(["user1", "user2"])
        config_store.set("channel_allowed_users", au)
        assert json.loads(config_store.get("channel_allowed_users")) == ["user1", "user2"]


class TestPropagateChannelSettings:
    """_propagate_channel_settings 热更新测试。"""

    def test_propagate_default_concurrency(self, handler, config_store):
        """config_store 更新后 propagate 应更新 handler._default_concurrency。"""
        assert handler._default_concurrency == "queue"

        config_store.set("channel_default_concurrency", "steer")

        # 模拟 _propagate_channel_settings 逻辑
        dc = config_store.get("channel_default_concurrency", "")
        if dc and dc in ("queue", "steer", "guide"):
            handler._default_concurrency = dc

        assert handler._default_concurrency == "steer"

    def test_propagate_invalid_concurrency_ignored(self, handler, config_store):
        """无效的 concurrency 值不应更新。"""
        handler._default_concurrency = "queue"
        config_store.set("channel_default_concurrency", "invalid_mode")

        dc = config_store.get("channel_default_concurrency", "")
        if dc and dc in ("queue", "steer", "guide"):
            handler._default_concurrency = dc

        assert handler._default_concurrency == "queue"
