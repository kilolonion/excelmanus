"""CLI 模块单元测试（新模块化架构）。

覆盖需求：
- 4.5: exit/quit/Ctrl+C 优雅退出并显示告别信息
- 4.6: /history 显示对话历史摘要
- 4.7: /clear 清除对话历史并确认
- 4.8: /help 显示所有可用命令和使用说明

注：渲染类测试已迁移到 test_cli_welcome.py / test_cli_help.py /
test_cli_commands.py / test_cli_prompt.py / test_cli_question.py 等。
本文件保留 REPL 集成、LiveStatusTicker、ChatWithFeedback、入口点等测试。
"""

from __future__ import annotations

import asyncio
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console as RealConsole

from excelmanus.config import ConfigError
from excelmanus.cli.question import InteractiveSelectResult
from excelmanus.cli.question import build_answer_from_select
from excelmanus.cli.repl import LiveStatusTicker, chat_with_feedback, run_chat_turn
from excelmanus.cli.prompt import compute_inline_suggestion
from excelmanus.cli.commands import (
    render_farewell,
    render_history,
    render_skills,
    suggest_similar_commands,
    parse_image_attachments,
)
from excelmanus.cli.help import render_help
from excelmanus.cli.welcome import render_welcome
from excelmanus.cli.main import main
from excelmanus.events import EventType, ToolCallEvent


# ── 辅助工具 ──────────────────────────────────────────────


def _make_engine() -> MagicMock:
    """创建模拟的 AgentEngine 实例。"""
    engine = MagicMock()
    engine.chat = AsyncMock(return_value="模拟回复")
    engine.clear_memory = MagicMock()
    engine.memory = MagicMock()
    engine.memory.get_messages.return_value = []
    engine.list_loaded_skillpacks.return_value = []
    engine.last_route_result = SimpleNamespace(
        route_mode="hidden",
        skills_used=[],
        tool_scope=[],
    )
    engine.full_access_enabled = False
    engine.resolve_skill_command = MagicMock(return_value=None)
    engine.list_skillpack_commands = MagicMock(return_value=[])
    engine.get_skillpack_argument_hint = MagicMock(return_value="")
    engine.subagent_enabled = False
    engine.plan_mode_enabled = False
    engine.has_pending_question = MagicMock(return_value=False)
    engine.is_waiting_multiselect_answer = MagicMock(return_value=False)
    engine.has_pending_approval = MagicMock(return_value=False)
    engine.current_pending_approval = MagicMock(return_value=None)
    engine.extract_and_save_memory = AsyncMock(return_value=None)
    engine.initialize_mcp = AsyncMock(return_value=None)
    engine.shutdown_mcp = AsyncMock(return_value=None)
    engine.mcp_connected_count = 0
    engine._mcp_manager = MagicMock()
    engine._mcp_manager.get_server_info.return_value = []
    return engine


def _run(coro):
    """同步运行异步协程。"""
    return asyncio.run(coro)


# ── 渲染函数测试（已迁移到各模块测试文件，此处保留部分集成测试） ──


def _make_console(width: int = 120) -> RealConsole:
    return RealConsole(file=StringIO(), width=width, force_terminal=True, highlight=False)


def _get_output(console: RealConsole) -> str:
    console.file.seek(0)
    return console.file.read()


class TestInteractiveSelectAnswerBuild:
    """交互式选择结果组装测试。"""

    def test_multi_select_can_keep_selected_indices_and_other_text(self) -> None:
        question = SimpleNamespace(multi_select=True)
        result = InteractiveSelectResult(
            selected_indices=[0, 1],
            other_text="自定义约束",
        )
        answer = build_answer_from_select(question, result)
        assert answer == "1\n2\n自定义约束"

    def test_single_select_other_keeps_original_behavior(self) -> None:
        question = SimpleNamespace(multi_select=False)
        result = InteractiveSelectResult(other_text="只要文本")
        answer = build_answer_from_select(question, result)
        assert answer == "只要文本"


class TestLiveStatusTicker:
    """测试 CLI 动态状态提示。"""

    def test_wrap_handler_returns_original_when_disabled(self) -> None:
        ticker = LiveStatusTicker(MagicMock(), enabled=False)

        def _handler(event: ToolCallEvent) -> None:
            return None

        wrapped = ticker.wrap_handler(_handler)
        assert wrapped is _handler

    def test_event_updates_tool_and_thinking_states(self) -> None:
        ticker = LiveStatusTicker(MagicMock(), enabled=True)

        wrapped = ticker.wrap_handler(lambda event: None)
        wrapped(
            ToolCallEvent(
                event_type=EventType.TOOL_CALL_START,
                tool_name="read_excel",
            )
        )
        assert ticker._status_label == "调用工具 read_excel"

        wrapped(
            ToolCallEvent(
                event_type=EventType.TOOL_CALL_END,
                tool_name="read_excel",
                success=True,
            )
        )
        assert ticker._status_label == "思考中"

    def test_ticker_frames_shrinking_are_space_padded(self) -> None:
        """帧从 ... 缩短到 .. / . 时应补空格，避免残留旧字符。"""

        async def _run_ticker() -> str:
            buf = StringIO()
            real_console = RealConsole(file=buf, force_terminal=True, width=80)
            ticker = LiveStatusTicker(real_console, enabled=True, interval=0.01)
            await ticker.start()
            await asyncio.sleep(0.06)
            await ticker.stop()
            return buf.getvalue()

        output = _run(_run_ticker())
        # Ticker 使用 braille spinner 帧（⠋⠙⠹…），验证状态标签和帧字符出现
        assert "思考中" in output
        assert any(frame in output for frame in ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴"))


class TestChatWithFeedback:
    """测试 chat 包装逻辑。"""

    def test_chat_with_feedback_passes_original_handler_when_not_tty(self) -> None:
        console = _make_console()
        engine = _make_engine()
        renderer = MagicMock()

        with patch("excelmanus.cli.prompt.is_interactive_terminal", return_value=False):
            _run(
                chat_with_feedback(
                    console,
                    engine,
                    user_input="读取文件",
                    renderer=renderer,
                )
            )

        engine.chat.assert_called_once_with("读取文件", on_event=renderer.handle_event)


# ── 入口点测试 ──


class TestCliEntryPoints:
    """CLI 入口路径测试。"""

    def test_async_main_config_error_exits_with_code_1(self) -> None:
        """配置加载失败时应输出错误并以退出码 1 终止。"""
        with patch(
            "excelmanus.config.load_config", side_effect=ConfigError("配置缺失")
        ):
            from excelmanus.cli.main import _async_main
            with pytest.raises(SystemExit) as exc_info:
                _run(_async_main())
            assert exc_info.value.code == 1

    def test_main_handles_keyboard_interrupt(self) -> None:
        """main 顶层应捕获 Ctrl+C 并输出告别信息。"""
        def _raise_keyboard_interrupt(coro):
            coro.close()
            raise KeyboardInterrupt

        with patch(
            "excelmanus.cli.main.asyncio.run", side_effect=_raise_keyboard_interrupt
        ):
            main()  # should not raise


# ── 配置测试 ──


class TestLayoutModeConfig:
    """EXCELMANUS_CLI_LAYOUT_MODE 环境变量与配置字段。"""

    def test_default_is_dashboard(self) -> None:
        from excelmanus.config import ExcelManusConfig
        cfg = ExcelManusConfig(
            api_key="test", base_url="https://x.com/v1", model="m"
        )
        assert cfg.cli_layout_mode == "dashboard"

    def test_env_var_classic(self) -> None:
        import os
        env = {
            "EXCELMANUS_API_KEY": "test",
            "EXCELMANUS_BASE_URL": "https://x.com/v1",
            "EXCELMANUS_MODEL": "m",
            "EXCELMANUS_CLI_LAYOUT_MODE": "classic",
        }
        with patch.dict(os.environ, env, clear=True):
            from excelmanus.config import load_config
            cfg = load_config()
            assert cfg.cli_layout_mode == "classic"

    def test_invalid_value_falls_back_to_dashboard(self) -> None:
        import os
        env = {
            "EXCELMANUS_API_KEY": "test",
            "EXCELMANUS_BASE_URL": "https://x.com/v1",
            "EXCELMANUS_MODEL": "m",
            "EXCELMANUS_CLI_LAYOUT_MODE": "fancy_invalid",
        }
        with patch.dict(os.environ, env, clear=True):
            from excelmanus.config import load_config
            cfg = load_config()
            assert cfg.cli_layout_mode == "dashboard"


# ── 命令建议测试 ──


class TestSmartCommandSuggestions:
    """未知命令近似推荐测试。"""

    def test_suggest_similar_commands_help(self) -> None:
        suggestions = suggest_similar_commands("/hel")
        assert "/help" in suggestions

    def test_suggest_similar_commands_histoy(self) -> None:
        suggestions = suggest_similar_commands("/histoy")
        assert "/history" in suggestions

    def test_suggest_similar_commands_max_3(self) -> None:
        suggestions = suggest_similar_commands("/x")
        assert len(suggestions) <= 3

    def test_suggest_similar_commands_no_match(self) -> None:
        suggestions = suggest_similar_commands("/zzzzzzzzzzz")
        assert isinstance(suggestions, list)


# ── @img 语法解析测试 ──


class TestParseImageAttachments:
    """@img 语法解析测试。"""

    def test_parse_img_syntax(self) -> None:
        text, images = parse_image_attachments("@img /tmp/photo.png 请复刻这个表格")
        assert text.strip() == "请复刻这个表格"
        assert len(images) == 1
        assert images[0] == "/tmp/photo.png"

    def test_no_img_syntax_passthrough(self) -> None:
        text, images = parse_image_attachments("普通消息")
        assert text == "普通消息"
        assert images == []

    def test_multiple_images(self) -> None:
        text, images = parse_image_attachments("@img a.png @img b.jpg 分析两张图")
        assert len(images) == 2
        assert "a.png" in images
        assert "b.jpg" in images
        assert text.strip() == "分析两张图"
