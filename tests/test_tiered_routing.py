"""路由测试：问候与普通任务同一主循环，斜杠直连，非斜杠全量工具。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from excelmanus.engine import _tool_access_from_chat_mode
from excelmanus.skillpacks.models import SkillMatchResult
from excelmanus.skillpacks.router import SkillRouter
from excelmanus.tools.policy import MUTATING_ALL_TOOLS, READ_ONLY_SAFE_TOOLS

_READ_TOOL = "inspect_spreadsheet"
_WRITE_TOOL = "write_text_file"


def _make_router() -> SkillRouter:
    config = MagicMock()
    config.skills_context_char_budget = 8000
    loader = MagicMock()
    loader.get_skillpacks.return_value = {
        "dummy": MagicMock(
            name="dummy",
            description="test",
            instructions="test",
            disable_model_invocation=False,
            user_invocable=True,
        )
    }
    loader.load_all.return_value = loader.get_skillpacks.return_value
    return SkillRouter(config, loader)


def _assert_main_loop_route(route_result: SkillMatchResult, *, chat_mode: str = "write") -> None:
    assert route_result.route_mode == "all_tools"
    access = _tool_access_from_chat_mode(chat_mode)
    assert access == "may_write"
    assert _READ_TOOL in READ_ONLY_SAFE_TOOLS
    assert _WRITE_TOOL in MUTATING_ALL_TOOLS


class TestGreetingUsesMainLoop:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "你好",
        "hi",
        "Hello!",
        "谢谢",
        "ok",
        "你是谁？",
        "help",
        "早上好",
    ])
    async def test_greeting_uses_all_tools_route(self, message: str) -> None:
        result = await _make_router().parse_slash_skill(None)
        _assert_main_loop_route(result, chat_mode="write")
        assert result.system_contexts == []

    @pytest.mark.asyncio
    async def test_greeting_plan_mode_still_all_tools(self) -> None:
        result = await _make_router().parse_slash_skill(None)
        _assert_main_loop_route(result, chat_mode="plan")


class TestTaskMessagesKeepTools:
    @pytest.mark.asyncio
    async def test_long_message_keeps_main_loop(self) -> None:
        result = await _make_router().parse_slash_skill(None)
        _assert_main_loop_route(result, chat_mode="write")

    @pytest.mark.asyncio
    async def test_message_with_file_path_keeps_main_loop(self) -> None:
        result = await _make_router().parse_slash_skill(None)
        _assert_main_loop_route(result, chat_mode="write")

    @pytest.mark.asyncio
    async def test_message_with_images_keeps_main_loop(self) -> None:
        result = await _make_router().parse_slash_skill(None)
        _assert_main_loop_route(result, chat_mode="write")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "你好，帮我读取 data.xlsx",
        "hello, please format the table",
        "hi 帮我创建图表",
        "谢谢，再帮我加个图表",
    ])
    async def test_task_messages_use_all_tools(self, message: str) -> None:
        result = await _make_router().parse_slash_skill(None)
        _assert_main_loop_route(result, chat_mode="write")

    @pytest.mark.asyncio
    async def test_read_mode_still_all_tools_route(self) -> None:
        result = await _make_router().parse_slash_skill(None)
        _assert_main_loop_route(result, chat_mode="read")


class TestNoHiddenFastPath:
    def test_engine_has_no_chitchat_fast_path(self) -> None:
        src = Path(__import__("excelmanus.engine", fromlist=["dummy"]).__file__).read_text(
            encoding="utf-8",
        )
        assert 'route_mode == "chitchat"' not in src
        assert "_is_chitchat_route" not in src

    def test_router_does_not_emit_chitchat_route_mode(self) -> None:
        src = Path(
            __import__("excelmanus.skillpacks.router", fromlist=["dummy"]).__file__,
        ).read_text(encoding="utf-8")
        assert 'route_mode="chitchat"' not in src
        assert 'route_mode = "chitchat"' not in src
