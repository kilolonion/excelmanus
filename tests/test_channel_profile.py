"""渠道 Profile 单元测试：验证 build_channel_notice() 及 ContextBuilder 集成。"""

from __future__ import annotations

import pytest

from excelmanus.channels.channel_profile import (
    CHANNEL_PROFILES,
    ChannelProfile,
    build_channel_notice,
    get_channel_profile,
)


class TestChannelProfiles:
    """验证渠道 Profile 定义完整性。"""

    def test_all_profiles_are_channel_profile_instances(self) -> None:
        for name, profile in CHANNEL_PROFILES.items():
            assert isinstance(profile, ChannelProfile)
            assert profile.name == name

    def test_profiles_have_positive_max_length(self) -> None:
        for profile in CHANNEL_PROFILES.values():
            assert profile.default_max_message_length > 0

    def test_profiles_have_supports_markdown_tables_flag(self) -> None:
        assert CHANNEL_PROFILES["telegram"].supports_markdown_tables is False
        assert CHANNEL_PROFILES["qq"].supports_markdown_tables is False
        assert CHANNEL_PROFILES["feishu"].supports_markdown_tables is True

    def test_profiles_have_compact_guidelines(self) -> None:
        for profile in CHANNEL_PROFILES.values():
            assert profile.compact_guidelines, f"{profile.name} 缺少精简版指南"

    def test_expected_channels_exist(self) -> None:
        assert "telegram" in CHANNEL_PROFILES
        assert "qq" in CHANNEL_PROFILES
        assert "feishu" in CHANNEL_PROFILES


class TestBuildChannelNotice:
    """验证 build_channel_notice() 逻辑。"""

    def test_none_returns_empty(self) -> None:
        assert build_channel_notice(None) == ""

    def test_web_returns_empty(self) -> None:
        assert build_channel_notice("web") == ""

    def test_empty_string_returns_empty(self) -> None:
        assert build_channel_notice("") == ""

    def test_unknown_channel_returns_empty(self) -> None:
        assert build_channel_notice("unknown_platform") == ""

    def test_telegram_notice_contains_key_elements(self) -> None:
        notice = build_channel_notice("telegram")
        assert notice  # 非空
        assert "Telegram Bot" in notice
        assert "4096" in notice
        assert "分块友好" in notice
        assert "格式限制" in notice
        assert "Markdown 表格" in notice

    def test_qq_notice_contains_key_elements(self) -> None:
        notice = build_channel_notice("qq")
        assert notice
        assert "QQ Bot" in notice
        assert "2000" in notice
        assert "纯文本" in notice

    def test_feishu_notice_contains_key_elements(self) -> None:
        notice = build_channel_notice("feishu")
        assert notice
        assert "飞书 Bot" in notice
        assert "4000" in notice
        assert "卡片" in notice

    def test_all_bot_notices_contain_common_guidelines(self) -> None:
        for channel in ("telegram", "qq", "feishu"):
            notice = build_channel_notice(channel)
            assert "分块友好" in notice, f"{channel} 缺少通用分块指南"
            assert "emoji" in notice, f"{channel} 缺少 emoji 指南"
            assert "错误信息" in notice, f"{channel} 缺少错误信息指南"

    def test_all_bot_notices_contain_layer6_guidelines(self) -> None:
        """验证层 6 附加适配项已注入所有 Bot 渠道。"""
        for channel in ("telegram", "qq", "feishu"):
            notice = build_channel_notice(channel)
            assert "多文件操作" in notice, f"{channel} 缺少多文件变更摘要指南"
            assert "公式" in notice, f"{channel} 缺少公式/VBA 引导指南"
            assert "安全脱敏" in notice, f"{channel} 缺少安全脱敏指南"
            assert "绝对路径" in notice, f"{channel} 缺少路径脱敏提示"

    def test_notice_starts_with_heading(self) -> None:
        for channel in ("telegram", "qq", "feishu"):
            notice = build_channel_notice(channel)
            assert notice.startswith("## 输出渠道适配")

    def test_ask_user_clarification_in_guidelines(self) -> None:
        """验证 ask_user 工具和自由文本选择的区分提示。"""
        for channel in ("telegram", "qq", "feishu"):
            notice = build_channel_notice(channel)
            assert "ask_user" in notice, f"{channel} 缺少 ask_user 澄清"
            assert "自由文本" in notice, f"{channel} 缺少自由文本选择说明"


class TestGetChannelProfile:
    """验证 get_channel_profile() 工厂方法。"""

    def test_returns_profile_for_known_channel(self) -> None:
        p = get_channel_profile("telegram")
        assert p is not None
        assert p.name == "telegram"

    def test_returns_none_for_web(self) -> None:
        assert get_channel_profile("web") is None

    def test_returns_none_for_none(self) -> None:
        assert get_channel_profile(None) is None

    def test_returns_none_for_unknown(self) -> None:
        assert get_channel_profile("slack") is None


class TestMaxMessageLengthOverride:
    """验证 max_message_length 运行时覆盖。"""

    def test_default_max_used_when_no_override(self) -> None:
        notice = build_channel_notice("telegram")
        assert "4096" in notice

    def test_override_max_message_length(self) -> None:
        notice = build_channel_notice("telegram", max_message_length=3000)
        # 头部应使用覆盖值
        assert "消息上限约 3000 字符" in notice

    def test_override_for_qq(self) -> None:
        notice = build_channel_notice("qq", max_message_length=1500)
        assert "1500" in notice


class TestCompactNotice:
    """验证精简版 notice。"""

    def test_compact_is_shorter(self) -> None:
        full = build_channel_notice("telegram")
        compact = build_channel_notice("telegram", compact=True)
        assert compact
        assert len(compact) < len(full)

    def test_compact_still_has_heading(self) -> None:
        compact = build_channel_notice("telegram", compact=True)
        assert compact.startswith("## 输出渠道适配")
        assert "Telegram Bot" in compact

    def test_compact_with_override_max(self) -> None:
        compact = build_channel_notice("qq", max_message_length=1800, compact=True)
        assert "1800" in compact

    def test_compact_false_returns_full(self) -> None:
        full = build_channel_notice("telegram", compact=False)
        assert "分块友好" in full


class TestDegradeTables:
    """验证 Markdown 表格降级。"""

    def test_table_converted_to_code_block(self) -> None:
        from excelmanus.channels.chunking import degrade_tables
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = degrade_tables(md)
        assert "```" in result
        assert "| A | B |" in result
        # 分隔行被替换为虚线
        assert "|---|---|" not in result

    def test_non_table_text_unchanged(self) -> None:
        from excelmanus.channels.chunking import degrade_tables
        text = "Hello world\n\nThis is **bold**"
        assert degrade_tables(text) == text

    def test_mixed_content(self) -> None:
        from excelmanus.channels.chunking import degrade_tables
        md = "Before\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nAfter"
        result = degrade_tables(md)
        assert "Before" in result
        assert "After" in result
        assert "```" in result


class TestContextBuilderChannelNotice:
    """验证 ContextBuilder._build_channel_notice() 集成。"""

    def test_build_channel_notice_web(self) -> None:
        """Web 渠道应返回空字符串。"""
        from unittest.mock import MagicMock

        mock_engine = MagicMock()
        mock_engine._channel_context = "web"

        from excelmanus.engine_core.context_builder import ContextBuilder
        builder = ContextBuilder.__new__(ContextBuilder)
        builder._engine = mock_engine

        result = builder._build_channel_notice()
        assert result == ""

    def test_build_channel_notice_none(self) -> None:
        """未设置渠道应返回空字符串。"""
        from unittest.mock import MagicMock

        mock_engine = MagicMock()
        mock_engine._channel_context = None

        from excelmanus.engine_core.context_builder import ContextBuilder
        builder = ContextBuilder.__new__(ContextBuilder)
        builder._engine = mock_engine

        result = builder._build_channel_notice()
        assert result == ""

    def test_build_channel_notice_telegram(self) -> None:
        """Telegram 渠道应返回非空提示词。"""
        from unittest.mock import MagicMock

        mock_engine = MagicMock()
        mock_engine._channel_context = "telegram"

        from excelmanus.engine_core.context_builder import ContextBuilder
        builder = ContextBuilder.__new__(ContextBuilder)
        builder._engine = mock_engine

        result = builder._build_channel_notice()
        assert result
        assert "Telegram Bot" in result

    def test_channel_cache_key_includes_channel_name(self) -> None:
        """缓存 key 应包含渠道名，防止不同渠道间缓存污染。"""
        from unittest.mock import MagicMock
        from excelmanus.engine_core.context_builder import ContextBuilder

        mock_engine = MagicMock()
        builder = ContextBuilder.__new__(ContextBuilder)
        builder._engine = mock_engine

        mock_engine._channel_context = "telegram"
        assert builder._channel_cache_key == "channel:telegram"

        mock_engine._channel_context = "qq"
        assert builder._channel_cache_key == "channel:qq"

        mock_engine._channel_context = None
        assert builder._channel_cache_key == "channel:web"
