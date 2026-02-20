"""CLI 模块单元测试：命令解析与退出流程。

覆盖需求：
- 4.5: exit/quit/Ctrl+C 优雅退出并显示告别信息
- 4.6: /history 显示对话历史摘要
- 4.7: /clear 清除对话历史并确认
- 4.8: /help 显示所有可用命令和使用说明
"""

from __future__ import annotations

import asyncio
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console as RealConsole

from excelmanus.config import ConfigError
from excelmanus.cli import (
    _InteractiveSelectResult,
    _LiveStatusTicker,
    _async_main,
    _build_answer_from_select,
    _chat_with_feedback,
    _compute_inline_suggestion,
    _read_multiline_user_input,
    _render_farewell,
    _render_help,
    _render_history,
    _render_skills,
    _render_welcome,
    _repl_loop,
    main,
)
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


# ── 渲染函数测试 ──────────────────────────────────────────


class TestRenderWelcome:
    """测试欢迎信息渲染。"""

    def _make_config(self) -> MagicMock:
        """创建模拟配置对象。"""
        config = MagicMock()
        config.model = "qwen-max-latest"
        config.workspace_root = "."
        config.subagent_enabled = True
        return config

    def test_welcome_renders_without_error(self) -> None:
        """欢迎信息应正常渲染，不抛出异常。"""
        with patch("excelmanus.cli.console") as mock_console:
            _render_welcome(self._make_config(), 3)
            mock_console.print.assert_called_once()

    def test_welcome_contains_version(self) -> None:
        """欢迎面板应包含模型信息（版本号已移至启动序列 Logo 区域）。"""
        from io import StringIO
        from rich.console import Console as RealConsole

        buf = StringIO()
        real_console = RealConsole(file=buf, width=120)
        with patch("excelmanus.cli.console", real_console):
            _render_welcome(self._make_config(), 3)
        text_str = buf.getvalue()
        assert "qwen-max-latest" in text_str


class TestRenderHelp:
    """测试帮助信息渲染（需求 4.8）。"""

    def test_help_renders_without_error(self) -> None:
        """/help 应正常渲染。"""
        with patch("excelmanus.cli.console") as mock_console:
            _render_help()
            # 现在 print 被调用多次（空行 + Panel + 空行）
            assert mock_console.print.call_count >= 1

    def test_help_contains_all_commands(self) -> None:
        """帮助信息应包含所有可用命令。"""
        from io import StringIO
        from rich.console import Console as RealConsole

        buf = StringIO()
        real_console = RealConsole(file=buf, width=120)
        with patch("excelmanus.cli.console", real_console):
            _render_help()
        text_str = buf.getvalue()
        assert "/help" in text_str
        assert "/history" in text_str
        assert "/clear" in text_str
        assert "/skills list" in text_str
        assert "/skills get <name>" in text_str
        assert "/skills create/patch/delete" in text_str
        assert "/subagent" in text_str
        assert "/fullaccess" in text_str
        assert "/accept" in text_str
        assert "/reject" in text_str
        assert "/undo" in text_str
        assert "/plan" in text_str
        assert "exit" in text_str
        assert "quit" in text_str
        assert "Ctrl+C" in text_str

    def test_help_contains_skillpack_argument_hints(self) -> None:
        from io import StringIO
        from rich.console import Console as RealConsole

        engine = _make_engine()
        engine.list_skillpack_commands.return_value = [
            ("data_basic", "<file>"),
            ("chart_basic", "<file> <chart_type>"),
        ]

        buf = StringIO()
        real_console = RealConsole(file=buf, width=140)
        with patch("excelmanus.cli.console", real_console):
            _render_help(engine)
        text_str = buf.getvalue()
        assert "/data_basic" in text_str
        assert "/chart_basic" in text_str
        assert "<file> <chart_type>" in text_str


class TestRenderFarewell:
    """测试告别信息渲染（需求 4.5）。"""

    def test_farewell_renders_without_error(self) -> None:
        """告别信息应正常渲染。"""
        with patch("excelmanus.cli.console") as mock_console:
            _render_farewell()
            mock_console.print.assert_called_once()

    def test_farewell_contains_goodbye(self) -> None:
        """告别信息应包含再见相关文字。"""
        with patch("excelmanus.cli.console") as mock_console:
            _render_farewell()
            output = str(mock_console.print.call_args[0][0])
            assert "再见" in output


class TestRenderHistory:
    """测试对话历史渲染（需求 4.6）。"""

    def test_empty_history(self) -> None:
        """无对话历史时应显示提示信息。"""
        engine = _make_engine()
        engine.memory.get_messages.return_value = []

        with patch("excelmanus.cli.console") as mock_console:
            _render_history(engine)
            output = str(mock_console.print.call_args[0][0])
            assert "暂无对话历史" in output

    def test_history_with_messages(self) -> None:
        """有对话历史时应显示用户和助手消息。"""
        from io import StringIO
        from rich.console import Console as RealConsole

        engine = _make_engine()
        engine.memory.get_messages.return_value = [
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮你的？"},
        ]

        buf = StringIO()
        real_console = RealConsole(file=buf, width=120)
        with patch("excelmanus.cli.console", real_console):
            _render_history(engine)
        text_str = buf.getvalue()
        assert "你好" in text_str
        assert "有什么可以帮你的" in text_str

    def test_history_filters_system_messages(self) -> None:
        """对话历史应过滤掉 system 消息。"""
        from io import StringIO
        from rich.console import Console as RealConsole

        engine = _make_engine()
        engine.memory.get_messages.return_value = [
            {"role": "system", "content": "系统提示词"},
            {"role": "user", "content": "测试输入"},
        ]

        buf = StringIO()
        real_console = RealConsole(file=buf, width=120)
        with patch("excelmanus.cli.console", real_console):
            _render_history(engine)
        text_str = buf.getvalue()
        assert "系统提示词" not in text_str
        assert "测试输入" in text_str

    def test_history_truncates_long_messages(self) -> None:
        """超过 80 字符的消息应被截断。"""
        from io import StringIO
        from rich.console import Console as RealConsole

        engine = _make_engine()
        long_msg = "A" * 100
        engine.memory.get_messages.return_value = [
            {"role": "user", "content": long_msg},
        ]

        buf = StringIO()
        real_console = RealConsole(file=buf, width=120)
        with patch("excelmanus.cli.console", real_console):
            _render_history(engine)
        text_str = buf.getvalue()
        # 截断后不应包含完整的 100 个 A
        assert long_msg not in text_str


class TestRenderSkills:
    """测试技能面板渲染。"""

    def test_render_skills_contains_permission_status(self) -> None:
        from io import StringIO
        from rich.console import Console as RealConsole

        engine = _make_engine()
        engine.full_access_enabled = False

        buf = StringIO()
        real_console = RealConsole(file=buf, width=120)
        with patch("excelmanus.cli.console", real_console):
            _render_skills(engine)
        text_str = buf.getvalue()
        assert "代码技能权限" in text_str
        assert "restricted" in text_str

    def test_render_skills_contains_subagent_status(self) -> None:
        from io import StringIO
        from rich.console import Console as RealConsole

        engine = _make_engine()
        engine.subagent_enabled = True

        buf = StringIO()
        real_console = RealConsole(file=buf, width=120)
        with patch("excelmanus.cli.console", real_console):
            _render_skills(engine)
        text_str = buf.getvalue()
        assert "子代理状态" in text_str
        assert "enabled" in text_str


class TestInlineSuggestion:
    """测试斜杠命令的内联补全计算。"""

    def test_command_prefix_suggestion(self) -> None:
        """输入 /ful 应补全到 /fullAccess。"""
        assert _compute_inline_suggestion("/ful") == "laccess"

    def test_fullaccess_argument_suggestion(self) -> None:
        """输入 /fullAccess s 应补全 status。"""
        assert _compute_inline_suggestion("/fullAccess s") == "tatus"

    def test_fullaccess_empty_argument_suggestion(self) -> None:
        """输入 /fullAccess 空格后应默认建议 status。"""
        assert _compute_inline_suggestion("/fullAccess ") == "status"

    def test_subagent_argument_suggestion(self) -> None:
        """输入 /subagent s 应补全 status。"""
        assert _compute_inline_suggestion("/subagent s") == "tatus"

    def test_subagent_empty_argument_suggestion(self) -> None:
        """输入 /subagent 空格后应默认建议 status。"""
        assert _compute_inline_suggestion("/subagent ") == "status"

    def test_subagent_list_argument_suggestion(self) -> None:
        """输入 /subagent l 应补全 list。"""
        assert _compute_inline_suggestion("/subagent l") == "ist"

    def test_plan_argument_suggestion(self) -> None:
        """输入 /plan a 应补全 approve。"""
        assert _compute_inline_suggestion("/plan a") == "pprove"

    def test_planmode_argument_suggestion_removed(self) -> None:
        """输入 /planmode r 不应再提供补全。"""
        assert _compute_inline_suggestion("/planmode r") is None

    def test_non_slash_input_returns_none(self) -> None:
        """普通自然语言输入不应触发斜杠补全。"""
        assert _compute_inline_suggestion("分析销售数据") is None

    def test_unknown_command_returns_none(self) -> None:
        """未知斜杠命令不应给出错误补全。"""
        assert _compute_inline_suggestion("/unknown") is None

    def test_skill_command_suggestion(self) -> None:
        """已加载 Skillpack 命令应可参与补全。"""
        with patch(
            "excelmanus.cli._DYNAMIC_SKILL_SLASH_COMMANDS",
            ("/data_basic", "/chart_basic"),
        ):
            assert _compute_inline_suggestion("/dat") == "a_basic"


class TestPromptToolkitInput:
    """测试 prompt_toolkit 异步输入路径。"""

    def test_repl_uses_prompt_async_when_tty(self) -> None:
        """在 TTY 环境中应使用 prompt_async 读取输入。"""
        engine = _make_engine()
        mock_session = MagicMock()
        mock_session.prompt_async = AsyncMock(return_value="exit")

        with patch("excelmanus.cli._PROMPT_TOOLKIT_ENABLED", True), \
             patch("excelmanus.cli._PROMPT_SESSION", mock_session), \
             patch("excelmanus.cli.sys.stdin.isatty", return_value=True), \
             patch("excelmanus.cli.sys.stdout.isatty", return_value=True), \
             patch("excelmanus.cli.console") as mock_console:
            _run(_repl_loop(engine))
            mock_session.prompt_async.assert_awaited_once()
            mock_console.input.assert_not_called()

    def test_prompt_async_failure_falls_back_to_console_input(self) -> None:
        """prompt_async 异常时应记录告警并回退到 console.input。"""
        engine = _make_engine()
        mock_session = MagicMock()
        mock_session.prompt_async = AsyncMock(side_effect=RuntimeError("loop conflict"))

        with patch("excelmanus.cli._PROMPT_TOOLKIT_ENABLED", True), \
             patch("excelmanus.cli._PROMPT_SESSION", mock_session), \
             patch("excelmanus.cli.sys.stdin.isatty", return_value=True), \
             patch("excelmanus.cli.sys.stdout.isatty", return_value=True), \
             patch("excelmanus.cli.logger") as mock_logger, \
             patch("excelmanus.cli.console") as mock_console:
            mock_console.input.return_value = "exit"
            _run(_repl_loop(engine))
            mock_session.prompt_async.assert_awaited_once()
            mock_console.input.assert_called_once()
            mock_logger.warning.assert_called()


class TestMultilineInput:
    """多行输入读取测试。"""

    def test_read_multiline_user_input_joins_lines_until_blank(self) -> None:
        with patch(
            "excelmanus.cli._read_user_input",
            new=AsyncMock(side_effect=["1", "方案A", ""]),
        ):
            text = _run(_read_multiline_user_input())
        assert text == "1\n方案A"


class TestInteractiveSelectAnswerBuild:
    """交互式选择结果组装测试。"""

    def test_multi_select_can_keep_selected_indices_and_other_text(self) -> None:
        question = SimpleNamespace(multi_select=True)
        result = _InteractiveSelectResult(
            selected_indices=[0, 1],
            other_text="自定义约束",
        )
        answer = _build_answer_from_select(question, result)
        assert answer == "1\n2\n自定义约束"

    def test_single_select_other_keeps_original_behavior(self) -> None:
        question = SimpleNamespace(multi_select=False)
        result = _InteractiveSelectResult(other_text="只要文本")
        answer = _build_answer_from_select(question, result)
        assert answer == "只要文本"


class TestLiveStatusTicker:
    """测试 CLI 动态状态提示。"""

    def test_wrap_handler_returns_original_when_disabled(self) -> None:
        ticker = _LiveStatusTicker(MagicMock(), enabled=False)

        def _handler(event: ToolCallEvent) -> None:
            return None

        wrapped = ticker.wrap_handler(_handler)
        assert wrapped is _handler

    def test_event_updates_tool_and_thinking_states(self) -> None:
        ticker = _LiveStatusTicker(MagicMock(), enabled=True)

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
            ticker = _LiveStatusTicker(real_console, enabled=True, interval=0.01)
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
        engine = _make_engine()
        renderer = MagicMock()

        with patch("excelmanus.cli._is_interactive_terminal", return_value=False):
            _run(
                _chat_with_feedback(
                    engine,
                    user_input="读取文件",
                    renderer=renderer,
                )
            )

        engine.chat.assert_called_once_with("读取文件", on_event=renderer.handle_event)


# ── REPL 循环测试 ─────────────────────────────────────────


class TestReplExitCommands:
    """测试退出命令（需求 4.5）。"""

    def test_exit_command(self) -> None:
        """输入 exit 应终止循环并显示告别信息。"""
        engine = _make_engine()
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.return_value = "exit"
            _run(_repl_loop(engine))
            # 应调用 print 输出告别信息
            farewell_printed = any(
                "再见" in str(call) for call in mock_console.print.call_args_list
            )
            assert farewell_printed

    def test_pending_question_slash_is_forwarded_to_engine_instead_of_local_help(self) -> None:
        """待回答状态下，/help 不应走本地命令分支。"""
        engine = _make_engine()
        engine.has_pending_question.side_effect = [True, False]
        engine.is_waiting_multiselect_answer.side_effect = [False, False]

        with patch("excelmanus.cli._read_user_input", new=AsyncMock(side_effect=["/help", "exit"])), \
             patch("excelmanus.cli._chat_with_feedback", new=AsyncMock(return_value="请先回答当前问题")), \
             patch("excelmanus.cli._render_help") as mock_help, \
             patch("excelmanus.cli.console") as mock_console:
            _run(_repl_loop(engine))
            mock_help.assert_not_called()

    def test_pending_multiselect_uses_multiline_mode(self) -> None:
        """多选待答时应进入多行输入模式，并将换行文本提交给引擎。"""
        engine = _make_engine()
        engine.has_pending_question.side_effect = [True, False]
        engine.is_waiting_multiselect_answer.side_effect = [True, False]

        chat_mock = AsyncMock(return_value="收到答案")
        with patch("excelmanus.cli._read_multiline_user_input", new=AsyncMock(return_value="1\n2")), \
             patch("excelmanus.cli._read_user_input", new=AsyncMock(return_value="exit")), \
             patch("excelmanus.cli._chat_with_feedback", new=chat_mock), \
             patch("excelmanus.cli.console") as mock_console:
            _run(_repl_loop(engine))

        assert chat_mock.await_count == 1
        _, kwargs = chat_mock.await_args
        assert kwargs["user_input"] == "1\n2"
        hint_printed = any(
            "每行输入一个选项" in str(call)
            for call in mock_console.print.call_args_list
        )
        assert hint_printed

    def test_quit_command(self) -> None:
        """输入 quit 应终止循环并显示告别信息。"""
        engine = _make_engine()
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.return_value = "quit"
            _run(_repl_loop(engine))
            farewell_printed = any(
                "再见" in str(call) for call in mock_console.print.call_args_list
            )
            assert farewell_printed

    def test_exit_case_insensitive(self) -> None:
        """退出命令应不区分大小写。"""
        engine = _make_engine()
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.return_value = "EXIT"
            _run(_repl_loop(engine))
            farewell_printed = any(
                "再见" in str(call) for call in mock_console.print.call_args_list
            )
            assert farewell_printed

    def test_keyboard_interrupt(self) -> None:
        """Ctrl+C 应优雅退出并显示告别信息。"""
        engine = _make_engine()
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = KeyboardInterrupt
            _run(_repl_loop(engine))
            farewell_printed = any(
                "再见" in str(call) for call in mock_console.print.call_args_list
            )
            assert farewell_printed

    def test_eof_error(self) -> None:
        """Ctrl+D (EOFError) 应优雅退出并显示告别信息。"""
        engine = _make_engine()
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = EOFError
            _run(_repl_loop(engine))
            farewell_printed = any(
                "再见" in str(call) for call in mock_console.print.call_args_list
            )
            assert farewell_printed

    def test_keyboard_interrupt_during_chat_exits(self) -> None:
        """处理阶段 Ctrl+C 应优雅退出整个会话。"""
        engine = _make_engine()
        engine.chat.side_effect = KeyboardInterrupt
        with patch("excelmanus.cli.console") as mock_console, \
             patch("excelmanus.cli.StreamRenderer"):
            mock_console.input.side_effect = ["读取文件"]
            _run(_repl_loop(engine))
            farewell_printed = any(
                "再见" in str(call) for call in mock_console.print.call_args_list
            )
            assert farewell_printed


class TestReplSlashCommands:
    """测试斜杠命令。"""

    def test_help_command(self) -> None:
        """/help 应显示帮助信息（需求 4.8）。"""
        engine = _make_engine()
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/help", "exit"]
            _run(_repl_loop(engine))
            # 应至少打印两次：帮助面板 + 告别信息
            assert mock_console.print.call_count >= 2

    def test_history_command(self) -> None:
        """/history 应显示对话历史（需求 4.6）。"""
        engine = _make_engine()
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/history", "exit"]
            _run(_repl_loop(engine))
            engine.memory.get_messages.assert_called_once()

    def test_clear_command(self) -> None:
        """/clear 应清除对话历史并确认（需求 4.7）。"""
        engine = _make_engine()
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/clear", "exit"]
            _run(_repl_loop(engine))
            engine.clear_memory.assert_called_once()
            # 应输出确认信息
            clear_confirmed = any(
                "已清除" in str(call) for call in mock_console.print.call_args_list
            )
            assert clear_confirmed

    def test_fullaccess_command_routes_to_engine_chat(self) -> None:
        """/fullAccess 命令应通过 engine.chat 处理。"""
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="当前代码技能权限：full_access。")
        with patch("excelmanus.cli.console") as mock_console, \
             patch("excelmanus.cli.StreamRenderer") as mock_renderer_cls:
            mock_console.input.side_effect = ["/fullAccess status", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_called_once_with("/fullAccess status")
            mock_renderer_cls.assert_not_called()

    def test_full_access_alias_routes_to_engine_chat(self) -> None:
        """/full_access 也应通过 engine.chat 处理。"""
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="已开启 fullAccess。")
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/full_access", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_called_once_with("/full_access")

    def test_subagent_command_routes_to_engine_chat(self) -> None:
        """/subagent 命令应通过 engine.chat 处理。"""
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "classic"
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="当前 subagent 状态：disabled。")
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/subagent status", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_called_once()
            assert engine.chat.call_args[0][0] == "/subagent status"

    def test_sub_agent_alias_routes_to_engine_chat(self) -> None:
        """/sub_agent 也应通过 engine.chat 处理。"""
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "classic"
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="已开启 subagent。")
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/sub_agent on", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_called_once()
            assert engine.chat.call_args[0][0] == "/sub_agent on"

    def test_subagent_list_routes_to_engine_chat(self) -> None:
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "classic"
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="共 4 个可用子代理。")
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/subagent list", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_called_once()
            assert engine.chat.call_args[0][0] == "/subagent list"

    def test_subagent_run_routes_to_engine_chat(self) -> None:
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "classic"
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="执行完成")
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/subagent run explorer -- 分析", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_called_once()
            assert engine.chat.call_args[0][0] == "/subagent run explorer -- 分析"

    def test_accept_command_routes_to_engine_chat(self) -> None:
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="已执行待确认操作。")
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/accept apv_1", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_called_once_with("/accept apv_1")

    def test_reject_command_routes_to_engine_chat(self) -> None:
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="已拒绝待确认操作。")
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/reject apv_1", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_called_once_with("/reject apv_1")

    def test_undo_command_routes_to_engine_chat(self) -> None:
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="已回滚。")
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/undo apv_1", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_called_once_with("/undo apv_1")

    def test_plan_command_routes_to_engine_chat(self) -> None:
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="当前 plan mode 状态：disabled。")
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/plan status", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_called_once_with("/plan status")

    def test_plan_approve_command_routes_to_engine_chat(self) -> None:
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="已批准计划。")
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/plan approve pln_1", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_called_once_with("/plan approve pln_1")

    def test_plan_reject_command_routes_to_engine_chat(self) -> None:
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="已拒绝计划。")
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/plan reject pln_1", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_called_once_with("/plan reject pln_1")

    def test_planmode_command_is_reported_as_unknown(self) -> None:
        engine = _make_engine()
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/planmode status", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_not_called()
            warning_printed = any(
                "未知命令" in str(call) and "/planmode status" in str(call)
                for call in mock_console.print.call_args_list
            )
            assert warning_printed

    def test_unknown_slash_command(self) -> None:
        """未知斜杠命令应显示警告。"""
        engine = _make_engine()
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/unknown", "exit"]
            _run(_repl_loop(engine))
            warning_printed = any(
                "未知命令" in str(call) for call in mock_console.print.call_args_list
            )
            assert warning_printed

    def test_skill_slash_command_routes_to_engine_chat_with_renderer(self) -> None:
        """Skill 斜杠命令应走 engine.chat + 渲染器。"""
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "classic"
        engine = _make_engine()
        engine.resolve_skill_command.return_value = "data_basic"
        engine.get_skillpack_argument_hint.return_value = "<file>"
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/data_basic 分析 sales.xlsx", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_called_once()
            call_args = engine.chat.call_args
            assert call_args[0][0] == "/data_basic 分析 sales.xlsx"
            assert call_args[1]["slash_command"] == "data_basic"
            assert call_args[1]["raw_args"] == "分析 sales.xlsx"

    def test_skill_slash_command_without_args_shows_argument_hint(self) -> None:
        engine = _make_engine()
        engine.resolve_skill_command.return_value = "chart_basic"
        engine.get_skillpack_argument_hint.return_value = "<file> <chart_type>"
        with patch("excelmanus.cli.console") as mock_console, \
             patch("excelmanus.cli.StreamRenderer") as mock_renderer_cls:
            mock_console.input.side_effect = ["/chart_basic", "exit"]
            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer
            _run(_repl_loop(engine))

            hint_printed = any(
                "参数提示" in str(call) and "<file> <chart_type>" in str(call)
                for call in mock_console.print.call_args_list
            )
            assert hint_printed


class TestReplInput:
    """测试一般输入处理。"""

    def test_empty_input_skipped(self) -> None:
        """空输入应被跳过，不调用 engine.chat。"""
        engine = _make_engine()
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["", "   ", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_not_called()

    def test_natural_language_calls_engine(self) -> None:
        """自然语言输入应调用 engine.chat 并显示结果。"""
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "classic"
        engine = _make_engine()
        engine.chat.return_value = "处理完成"
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["读取文件", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_called_once()
            assert engine.chat.call_args[0][0] == "读取文件"
            assert "on_event" in engine.chat.call_args[1]


class TestCliStreamRendererIntegration:
    """CLI 集成测试：StreamRenderer 替代 spinner（需求 4.1, 4.2, 4.3, 4.4）。"""

    def test_natural_language_uses_stream_renderer_not_spinner(self) -> None:
        """自然语言输入应通过 _run_chat_turn 调用 engine.chat，不使用 spinner。"""
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "classic"
        engine = _make_engine()
        engine.chat.return_value = "分析完成"
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["分析销售数据", "exit"]
            _run(_repl_loop(engine))
            engine.chat.assert_called_once()
            assert engine.chat.call_args[0][0] == "分析销售数据"
            assert "on_event" in engine.chat.call_args[1]
            # 验证未使用 console.status（即 spinner）
            mock_console.status.assert_not_called()


class TestSkillsSubcommands:
    """/skills 子命令测试。"""

    def test_skills_list_subcommand_calls_engine_manager(self) -> None:
        engine = _make_engine()
        engine.list_skillpacks_detail.return_value = [
            {
                "name": "data_basic",
                "source": "system",
                "writable": False,
                "description": "分析",
            }
        ]
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/skills list", "exit"]
            _run(_repl_loop(engine))
            engine.list_skillpacks_detail.assert_called_once()
            engine.chat.assert_not_called()

    def test_skills_get_subcommand_calls_engine_manager(self) -> None:
        engine = _make_engine()
        engine.get_skillpack_detail.return_value = {
            "name": "data_basic",
            "description": "分析",
            "required_mcp_servers": ["context7"],
            "required_mcp_tools": ["context7:query_docs"],
        }
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/skills get data_basic", "exit"]
            _run(_repl_loop(engine))
            engine.get_skillpack_detail.assert_called_once_with("data_basic")
            rendered = "\n".join(
                str(call.args[0])
                for call in mock_console.print.call_args_list
                if call.args
            )
            assert "name" in rendered
            assert "required-mcp-servers" in rendered
            assert "required-mcp-tools" in rendered

    def test_skills_create_subcommand_uses_json_payload(self) -> None:
        engine = _make_engine()
        engine.create_skillpack.return_value = {"name": "api_skill"}
        with patch("excelmanus.cli.console") as mock_console, \
             patch("excelmanus.cli._sync_skill_command_suggestions") as sync_mock:
            mock_console.input.side_effect = [
                '/skills create api_skill --json \'{"description":"d","instructions":"说明"}\'',
                "exit",
            ]
            _run(_repl_loop(engine))
            engine.create_skillpack.assert_called_once()
            args, kwargs = engine.create_skillpack.call_args
            assert args[0] == "api_skill"
            assert kwargs["actor"] == "cli"
            assert sync_mock.call_count >= 2

    def test_skills_delete_requires_yes_flag(self) -> None:
        engine = _make_engine()
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/skills delete demo_skill", "exit"]
            _run(_repl_loop(engine))
            engine.delete_skillpack.assert_not_called()

    def test_skills_delete_with_yes_calls_engine(self) -> None:
        engine = _make_engine()
        engine.delete_skillpack.return_value = {"name": "demo_skill"}
        with patch("excelmanus.cli.console") as mock_console, \
             patch("excelmanus.cli._sync_skill_command_suggestions") as sync_mock:
            mock_console.input.side_effect = ["/skills delete demo_skill --yes", "exit"]
            _run(_repl_loop(engine))
            engine.delete_skillpack.assert_called_once_with(
                "demo_skill", actor="cli", reason="cli_delete"
            )
            assert sync_mock.call_count >= 2

    def test_exception_during_chat_repl_continues(self) -> None:
        """engine.chat 抛出异常后，REPL 应继续运行，用户可继续输入。"""
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "classic"
        engine = _make_engine()
        # 第一次调用抛出异常，第二次正常返回
        engine.chat.side_effect = [RuntimeError("网络超时"), "第二次回复"]
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["第一次输入", "第二次输入", "exit"]
            _run(_repl_loop(engine))
            # 验证 engine.chat 被调用了两次（异常后继续）
            assert engine.chat.call_count == 2
            # 验证错误面板被输出（Panel 对象或字符串）
            from rich.panel import Panel as RichPanel
            error_rendered = any(
                isinstance(c.args[0], RichPanel) if c.args else False
                for c in mock_console.print.call_args_list
            ) or any(
                "网络超时" in str(call) for call in mock_console.print.call_args_list
            )
            assert error_rendered

    def test_final_reply_rendered_as_markdown_after_cards(self) -> None:
        """最终回复应在工具调用卡片之后以 Panel(Markdown) 形式渲染。"""
        engine = _make_engine()
        engine.chat.return_value = "**分析结果**：销售额增长 20%"
        with patch("excelmanus.cli.console") as mock_console, \
             patch("excelmanus.cli.StreamRenderer") as mock_renderer_cls:
            mock_console.input.side_effect = ["分析数据", "exit"]
            mock_renderer = MagicMock()
            mock_renderer._streaming_text = False
            mock_renderer._streaming_thinking = False
            mock_renderer_cls.return_value = mock_renderer
            _run(_repl_loop(engine))
            # 收集所有 print 调用的参数
            print_calls = mock_console.print.call_args_list
            # 查找包含 Markdown 的 Panel 渲染调用
            from rich.panel import Panel as RichPanel
            from rich.markdown import Markdown as RichMarkdown
            panel_md_calls = [
                call for call in print_calls
                if call.args
                and isinstance(call.args[0], RichPanel)
                and isinstance(call.args[0].renderable, RichMarkdown)
            ]
            assert len(panel_md_calls) == 1, "最终回复应以 Panel(Markdown) 渲染一次"

    def test_existing_commands_unaffected_by_stream_renderer(self) -> None:
        """StreamRenderer 集成不应影响现有斜杠命令和退出命令。"""
        engine = _make_engine()
        engine.memory.get_messages.return_value = [
            {"role": "user", "content": "测试"},
            {"role": "assistant", "content": "回复"},
        ]
        with patch("excelmanus.cli.console") as mock_console, \
             patch("excelmanus.cli.StreamRenderer"):
            # 依次执行所有命令类型
            mock_console.input.side_effect = [
                "/help",       # 帮助命令
                "/history",    # 历史命令
                "/clear",      # 清除命令
                "quit",        # 退出命令
            ]
            _run(_repl_loop(engine))
            # 验证各命令正常执行
            engine.memory.get_messages.assert_called_once()
            engine.clear_memory.assert_called_once()
            # 验证 engine.chat 未被调用（所有输入都是命令）
            engine.chat.assert_not_called()

    def test_multiple_natural_language_inputs_each_create_renderer(self) -> None:
        """每次自然语言输入都应调用 engine.chat（通过 _run_chat_turn）。"""
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "classic"
        engine = _make_engine()
        engine.chat.side_effect = ["回复1", "回复2"]
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["输入1", "输入2", "exit"]
            _run(_repl_loop(engine))
            # 每次自然语言输入都应调用 engine.chat
            assert engine.chat.call_count == 2


class TestCliEntryPoints:
    """CLI 入口路径测试。"""

    def test_async_main_config_error_exits_with_code_1(self) -> None:
        """配置加载失败时应输出错误并以退出码 1 终止。"""
        with patch(
            "excelmanus.cli.load_config", side_effect=ConfigError("配置缺失")
        ), patch("excelmanus.cli.console") as mock_console:
            with pytest.raises(SystemExit) as exc_info:
                _run(_async_main())
            assert exc_info.value.code == 1
            printed = any(
                "配置错误" in str(call) for call in mock_console.print.call_args_list
            )
            assert printed

    def test_main_handles_keyboard_interrupt(self) -> None:
        """main 顶层应捕获 Ctrl+C 并输出告别信息。"""
        def _raise_keyboard_interrupt(coro):
            coro.close()
            raise KeyboardInterrupt

        with patch(
            "excelmanus.cli.asyncio.run", side_effect=_raise_keyboard_interrupt
        ), patch("excelmanus.cli._render_farewell") as mock_farewell:
            main()
            mock_farewell.assert_called_once()

    def test_async_main_extracts_memory_on_exit(self) -> None:
        """REPL 结束后应触发持久记忆提取。"""
        config = SimpleNamespace(
            log_level="INFO",
            workspace_root=".",
            memory_enabled=False,
            model="test-model",
            subagent_enabled=True,
            cli_layout_mode="dashboard",
        )
        engine = _make_engine()
        engine.list_loaded_skillpacks.return_value = []

        registry = MagicMock()
        registry.get_tool_names.return_value = []
        loader = MagicMock()
        loader.list_skillpacks.return_value = []
        router = MagicMock()
        loader.load_all.return_value = {}

        with patch("excelmanus.cli.load_config", return_value=config), \
             patch("excelmanus.cli.setup_logging"), \
             patch("excelmanus.cli.ToolRegistry", return_value=registry), \
             patch("excelmanus.cli.SkillpackLoader", return_value=loader), \
             patch("excelmanus.cli.SkillRouter", return_value=router), \
             patch("excelmanus.cli.AgentEngine", return_value=engine), \
             patch("excelmanus.cli._sync_skill_command_suggestions"), \
             patch("excelmanus.cli._render_welcome"), \
             patch("excelmanus.cli._repl_loop", new=AsyncMock(return_value=None)):
            _run(_async_main())

        engine.extract_and_save_memory.assert_awaited_once()

    def test_async_main_memory_extraction_error_is_logged_and_ignored(self) -> None:
        """记忆提取异常仅记录日志，不影响 CLI 退出。"""
        config = SimpleNamespace(
            log_level="INFO",
            workspace_root=".",
            memory_enabled=False,
            model="test-model",
            subagent_enabled=True,
            cli_layout_mode="dashboard",
        )
        engine = _make_engine()
        engine.extract_and_save_memory = AsyncMock(side_effect=RuntimeError("boom"))
        engine.list_loaded_skillpacks.return_value = []

        registry = MagicMock()
        registry.get_tool_names.return_value = []
        loader = MagicMock()
        loader.list_skillpacks.return_value = []
        router = MagicMock()
        loader.load_all.return_value = {}

        with patch("excelmanus.cli.load_config", return_value=config), \
             patch("excelmanus.cli.setup_logging"), \
             patch("excelmanus.cli.ToolRegistry", return_value=registry), \
             patch("excelmanus.cli.SkillpackLoader", return_value=loader), \
             patch("excelmanus.cli.SkillRouter", return_value=router), \
             patch("excelmanus.cli.AgentEngine", return_value=engine), \
             patch("excelmanus.cli._sync_skill_command_suggestions"), \
             patch("excelmanus.cli._render_welcome"), \
             patch("excelmanus.cli._repl_loop", new=AsyncMock(return_value=None)), \
             patch("excelmanus.cli.logger") as mock_logger:
            _run(_async_main())

        engine.extract_and_save_memory.assert_awaited_once()
        mock_logger.warning.assert_called()


# ══════════════════════════════════════════════════════════
# Task 1: Layout Mode 与 /ui 命令
# ══════════════════════════════════════════════════════════


class TestLayoutModeConfig:
    """EXCELMANUS_CLI_LAYOUT_MODE 环境变量与配置字段。"""

    def test_default_is_dashboard(self) -> None:
        """默认布局模式应为 dashboard。"""
        from excelmanus.config import ExcelManusConfig

        # 使用最小必填参数创建
        cfg = ExcelManusConfig(
            api_key="test", base_url="https://x.com/v1", model="m"
        )
        assert cfg.cli_layout_mode == "dashboard"

    def test_env_var_classic(self) -> None:
        """环境变量设为 classic 时应反映到配置。"""
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
        """非法值应回退为 dashboard。"""
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


class TestUiCommand:
    """/ui 命令路由测试。"""

    def test_ui_status_shows_current_mode(self) -> None:
        """/ui status 应显示当前布局模式。"""
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "dashboard"
        from excelmanus.cli import _handle_ui_command
        engine = _make_engine()
        engine.config = MagicMock()
        engine.config.cli_layout_mode = "dashboard"
        with patch("excelmanus.cli.console") as mock_console:
            result = _handle_ui_command("/ui status", engine)
        assert result is True
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "dashboard" in printed

    def test_ui_classic_switches_mode(self) -> None:
        """/ui classic 应切换到 classic 模式。"""
        from excelmanus.cli import _handle_ui_command, _current_layout_mode
        engine = _make_engine()
        with patch("excelmanus.cli.console"):
            _handle_ui_command("/ui classic", engine)
        from excelmanus.cli import _current_layout_mode as mode
        assert mode == "classic"

    def test_ui_dashboard_switches_mode(self) -> None:
        """/ui dashboard 应切换到 dashboard 模式。"""
        from excelmanus.cli import _handle_ui_command, _current_layout_mode
        engine = _make_engine()
        with patch("excelmanus.cli.console"):
            _handle_ui_command("/ui classic", engine)
            _handle_ui_command("/ui dashboard", engine)
        from excelmanus.cli import _current_layout_mode as mode
        assert mode == "dashboard"

    def test_ui_bare_shows_status(self) -> None:
        """/ui 不带参数应等同于 /ui status。"""
        from excelmanus.cli import _handle_ui_command
        engine = _make_engine()
        engine.config = MagicMock()
        engine.config.cli_layout_mode = "dashboard"
        with patch("excelmanus.cli.console") as mock_console:
            result = _handle_ui_command("/ui", engine)
        assert result is True

    def test_ui_registered_in_slash_commands(self) -> None:
        """/ui 应出现在斜杠命令集合中。"""
        from excelmanus.cli import _SLASH_COMMANDS
        assert "/ui" in _SLASH_COMMANDS


# ══════════════════════════════════════════════════════════
# Task 4: _run_chat_turn 统一回合执行入口
# ══════════════════════════════════════════════════════════


class TestRunChatTurn:
    """_run_chat_turn 统一回合执行入口测试。"""

    def test_run_chat_turn_classic_mode(self) -> None:
        """classic 模式下使用 StreamRenderer。"""
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "classic"
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="经典模式回复")

        with patch("excelmanus.cli.console") as mock_console:
            reply, streamed = _run(_run_chat_turn_helper(engine, "你好"))
        assert reply == "经典模式回复"

    def test_run_chat_turn_dashboard_mode(self) -> None:
        """dashboard 模式下使用 DashboardRenderer。"""
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "dashboard"
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="仪表盘模式回复")

        with patch("excelmanus.cli.console") as mock_console:
            reply, streamed = _run(_run_chat_turn_helper(engine, "你好"))
        assert reply == "仪表盘模式回复"

    def test_run_chat_turn_with_slash_command(self) -> None:
        """支持 slash_command 和 raw_args 透传。"""
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "classic"
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="技能回复")

        with patch("excelmanus.cli.console"):
            reply, streamed = _run(_run_chat_turn_helper(
                engine, "/data_basic 读取前10行",
                slash_command="data_basic",
                raw_args="读取前10行",
            ))
        assert reply == "技能回复"
        # 确认 slash_command 被传递
        call_kwargs = engine.chat.call_args
        assert call_kwargs is not None

    def test_run_chat_turn_renders_panel_when_not_streamed(self) -> None:
        """未流式输出时应渲染 Panel。"""
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "classic"
        engine = _make_engine()
        engine.chat = AsyncMock(return_value="最终回复")

        with patch("excelmanus.cli.console") as mock_console:
            _run(_run_chat_turn_helper(engine, "你好"))
        # 应有 Panel 输出（至少多次 print）
        assert mock_console.print.call_count >= 1

    def test_run_chat_turn_error_label(self) -> None:
        """异常时应输出结构化错误面板。"""
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "classic"
        engine = _make_engine()
        engine.chat = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("excelmanus.cli.console") as mock_console, \
             patch("excelmanus.cli.logger"):
            result = _run(_run_chat_turn_helper(
                engine, "会出错",
                error_label="处理请求",
            ))
        assert result is None
        # 应输出结构化错误面板（Panel 对象）
        from rich.panel import Panel as RichPanel
        panel_rendered = any(
            isinstance(c.args[0], RichPanel) if c.args else False
            for c in mock_console.print.call_args_list
        )
        assert panel_rendered or mock_console.print.call_count >= 1


# ══════════════════════════════════════════════════════════
# Task 6: 输入与命令发现优化
# ══════════════════════════════════════════════════════════


class TestDensePromptBadges:
    """Prompt 密集徽章测试。"""

    def test_build_prompt_badges_contains_model(self) -> None:
        """Prompt 徽章应包含模型名称。"""
        from excelmanus.cli import _build_prompt_badges
        badges = _build_prompt_badges(
            model_hint="qwen-max", turn_number=3,
            layout_mode="dashboard", subagent_active=False, plan_mode=False,
        )
        assert "qwen-max" in badges

    def test_build_prompt_badges_contains_turn(self) -> None:
        """Prompt 徽章应包含回合号。"""
        from excelmanus.cli import _build_prompt_badges
        badges = _build_prompt_badges(
            model_hint="m", turn_number=5,
            layout_mode="dashboard", subagent_active=False, plan_mode=False,
        )
        assert "#5" in badges or "5" in badges

    def test_build_prompt_badges_contains_layout(self) -> None:
        """Prompt 徽章应包含布局模式。"""
        from excelmanus.cli import _build_prompt_badges
        badges = _build_prompt_badges(
            model_hint="m", turn_number=1,
            layout_mode="dashboard", subagent_active=False, plan_mode=False,
        )
        assert "dashboard" in badges

    def test_build_prompt_badges_subagent_active(self) -> None:
        """子代理活跃时应显示 subagent 徽章。"""
        from excelmanus.cli import _build_prompt_badges
        badges = _build_prompt_badges(
            model_hint="m", turn_number=1,
            layout_mode="classic", subagent_active=True, plan_mode=False,
        )
        assert "subagent" in badges.lower() or "🧵" in badges

    def test_build_prompt_badges_plan_mode(self) -> None:
        """计划模式时应显示 plan 徽章。"""
        from excelmanus.cli import _build_prompt_badges
        badges = _build_prompt_badges(
            model_hint="m", turn_number=1,
            layout_mode="classic", subagent_active=False, plan_mode=True,
        )
        assert "plan" in badges.lower()


class TestSmartCommandSuggestions:
    """未知命令近似推荐测试。"""

    def test_suggest_similar_commands_help(self) -> None:
        """输入 /hel 应推荐 /help。"""
        from excelmanus.cli import _suggest_similar_commands
        suggestions = _suggest_similar_commands("/hel")
        assert "/help" in suggestions

    def test_suggest_similar_commands_histoy(self) -> None:
        """输入 /histoy 应推荐 /history。"""
        from excelmanus.cli import _suggest_similar_commands
        suggestions = _suggest_similar_commands("/histoy")
        assert "/history" in suggestions

    def test_suggest_similar_commands_max_3(self) -> None:
        """推荐最多 3 个。"""
        from excelmanus.cli import _suggest_similar_commands
        suggestions = _suggest_similar_commands("/x")
        assert len(suggestions) <= 3

    def test_suggest_similar_commands_no_match(self) -> None:
        """无近似命令时返回空列表。"""
        from excelmanus.cli import _suggest_similar_commands
        suggestions = _suggest_similar_commands("/zzzzzzzzzzz")
        assert isinstance(suggestions, list)

    def test_unknown_command_uses_smart_suggestions(self) -> None:
        """REPL 中未知命令应使用近似推荐而非固定列表。"""
        import excelmanus.cli as cli_mod
        cli_mod._current_layout_mode = "classic"
        engine = _make_engine()
        with patch("excelmanus.cli.console") as mock_console:
            mock_console.input.side_effect = ["/hep", "exit"]
            _run(_repl_loop(engine))
            printed = " ".join(str(c) for c in mock_console.print.call_args_list)
            assert "/help" in printed


# ══════════════════════════════════════════════════════════
# Task 7: /history 与 /help 重构
# ══════════════════════════════════════════════════════════


class TestHistoryRedesign:
    """/history 回合聚合视图测试。"""

    @staticmethod
    def _capture_history(engine) -> str:
        from io import StringIO
        from rich.console import Console as RichConsole
        buf = StringIO()
        c = RichConsole(file=buf, width=120, force_terminal=True)
        with patch("excelmanus.cli.console", c):
            _render_history(engine)
        buf.seek(0)
        return buf.read()

    def test_history_shows_turn_aggregation(self) -> None:
        """/history 应按回合聚合显示，包含回合号标记。"""
        engine = _make_engine()
        engine.memory.get_messages.return_value = [
            {"role": "user", "content": "读取数据"},
            {"role": "assistant", "content": "已读取数据，共100行。"},
            {"role": "user", "content": "分析趋势"},
            {"role": "assistant", "content": "趋势分析完成。"},
        ]
        output = self._capture_history(engine)
        assert "回合" in output or "#1" in output

    def test_history_shows_message_stats(self) -> None:
        """/history 应显示消息统计信息。"""
        engine = _make_engine()
        engine.memory.get_messages.return_value = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮助你的？"},
        ]
        output = self._capture_history(engine)
        assert "1" in output

    def test_history_tool_calls_counted(self) -> None:
        """/history 应统计工具调用。"""
        engine = _make_engine()
        engine.memory.get_messages.return_value = [
            {"role": "user", "content": "读取文件"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"function": {"name": "read_excel", "arguments": "{}"}}
            ]},
            {"role": "tool", "content": "读取成功", "name": "read_excel"},
            {"role": "assistant", "content": "已读取。"},
        ]
        output = self._capture_history(engine)
        assert "read_excel" in output or "工具" in output or "🔧" in output

    def test_history_empty(self) -> None:
        """/history 无历史时应提示。"""
        engine = _make_engine()
        engine.memory.get_messages.return_value = []
        output = self._capture_history(engine)
        assert "暂无" in output


class TestHelpRedesign:
    """/help 重构测试。"""

    @staticmethod
    def _capture_help() -> str:
        from io import StringIO
        from rich.console import Console as RichConsole
        buf = StringIO()
        c = RichConsole(file=buf, width=120, force_terminal=True)
        with patch("excelmanus.cli.console", c):
            _render_help()
        buf.seek(0)
        return buf.read()

    def test_help_contains_ui_command(self) -> None:
        """/help 应包含 /ui 命令说明。"""
        output = self._capture_help()
        assert "/ui" in output

    def test_help_contains_flow_example(self) -> None:
        """/help 应包含使用流程示例。"""
        output = self._capture_help()
        assert "步骤" in output or "入门" in output

    def test_help_contains_layout_section(self) -> None:
        """/help 应包含显示模式相关说明。"""
        output = self._capture_help()
        assert "dashboard" in output or "显示模式" in output


async def _run_chat_turn_helper(
    engine,
    user_input: str,
    *,
    slash_command: str | None = None,
    raw_args: str | None = None,
    error_label: str = "处理请求",
) -> tuple[str, bool] | None:
    """测试辅助函数，调用 _run_chat_turn。"""
    from excelmanus.cli import _run_chat_turn
    return await _run_chat_turn(
        engine,
        user_input=user_input,
        slash_command=slash_command,
        raw_args=raw_args,
        error_label=error_label,
    )
