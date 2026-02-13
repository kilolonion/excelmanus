"""CLI 模块单元测试：命令解析与退出流程。

覆盖需求：
- 4.5: exit/quit/Ctrl+C 优雅退出并显示告别信息
- 4.6: /history 显示对话历史摘要
- 4.7: /clear 清除对话历史并确认
- 4.8: /help 显示所有可用命令和使用说明
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.config import ConfigError
from excelmanus.cli import (
    _async_main,
    _compute_inline_suggestion,
    _render_farewell,
    _render_help,
    _render_history,
    _render_skills,
    _render_welcome,
    _repl_loop,
    main,
)


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
        return config

    def test_welcome_renders_without_error(self) -> None:
        """欢迎信息应正常渲染，不抛出异常。"""
        with patch("excelmanus.cli.console") as mock_console:
            _render_welcome(self._make_config(), 3)
            mock_console.print.assert_called_once()

    def test_welcome_contains_version(self) -> None:
        """欢迎面板应包含版本号。"""
        from io import StringIO
        from rich.console import Console as RealConsole

        buf = StringIO()
        real_console = RealConsole(file=buf, width=120)
        with patch("excelmanus.cli.console", real_console):
            _render_welcome(self._make_config(), 3)
        text_str = buf.getvalue()
        assert "v3.0.0" in text_str


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
        assert "/fullAccess" in text_str
        assert "exit" in text_str
        assert "quit" in text_str
        assert "Ctrl+C" in text_str


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


class TestInlineSuggestion:
    """测试斜杠命令的内联补全计算。"""

    def test_command_prefix_suggestion(self) -> None:
        """输入 /ful 应补全到 /fullAccess。"""
        assert _compute_inline_suggestion("/ful") == "lAccess"

    def test_fullaccess_argument_suggestion(self) -> None:
        """输入 /fullAccess s 应补全 status。"""
        assert _compute_inline_suggestion("/fullAccess s") == "tatus"

    def test_fullaccess_empty_argument_suggestion(self) -> None:
        """输入 /fullAccess 空格后应默认建议 status。"""
        assert _compute_inline_suggestion("/fullAccess ") == "status"

    def test_non_slash_input_returns_none(self) -> None:
        """普通自然语言输入不应触发斜杠补全。"""
        assert _compute_inline_suggestion("分析销售数据") is None

    def test_unknown_command_returns_none(self) -> None:
        """未知斜杠命令不应给出错误补全。"""
        assert _compute_inline_suggestion("/unknown") is None


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
        """Skill 斜杠命令应走 engine.chat + StreamRenderer。"""
        engine = _make_engine()
        engine.resolve_skill_command.return_value = "data_basic"
        with patch("excelmanus.cli.console") as mock_console, \
             patch("excelmanus.cli.StreamRenderer") as mock_renderer_cls:
            mock_console.input.side_effect = ["/data_basic 分析 sales.xlsx", "exit"]
            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer
            _run(_repl_loop(engine))

            mock_renderer_cls.assert_called_once_with(mock_console)
            engine.chat.assert_called_once_with(
                "/data_basic 分析 sales.xlsx",
                on_event=mock_renderer.handle_event,
            )


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
        """自然语言输入应调用 engine.chat 并显示结果（使用 StreamRenderer 回调）。"""
        engine = _make_engine()
        engine.chat.return_value = "处理完成"
        with patch("excelmanus.cli.console") as mock_console, \
             patch("excelmanus.cli.StreamRenderer") as mock_renderer_cls:
            mock_console.input.side_effect = ["读取文件", "exit"]
            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer
            _run(_repl_loop(engine))
            # 验证 StreamRenderer 使用 console 创建
            mock_renderer_cls.assert_called_once_with(mock_console)
            # 验证 engine.chat 传入了 on_event 回调
            engine.chat.assert_called_once_with(
                "读取文件", on_event=mock_renderer.handle_event
            )


class TestCliStreamRendererIntegration:
    """CLI 集成测试：StreamRenderer 替代 spinner（需求 4.1, 4.2, 4.3, 4.4）。"""

    def test_natural_language_uses_stream_renderer_not_spinner(self) -> None:
        """自然语言输入应创建 StreamRenderer 并传递给 engine.chat，不使用 spinner。"""
        engine = _make_engine()
        engine.chat.return_value = "分析完成"
        with patch("excelmanus.cli.console") as mock_console, \
             patch("excelmanus.cli.StreamRenderer") as mock_renderer_cls:
            mock_console.input.side_effect = ["分析销售数据", "exit"]
            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer
            _run(_repl_loop(engine))
            # 验证创建了 StreamRenderer
            mock_renderer_cls.assert_called_once_with(mock_console)
            # 验证 engine.chat 使用 on_event 回调而非 spinner
            engine.chat.assert_called_once_with(
                "分析销售数据", on_event=mock_renderer.handle_event
            )
            # 验证未使用 console.status（即 spinner）
            mock_console.status.assert_not_called()

    def test_exception_during_chat_repl_continues(self) -> None:
        """engine.chat 抛出异常后，REPL 应继续运行，用户可继续输入。"""
        engine = _make_engine()
        # 第一次调用抛出异常，第二次正常返回
        engine.chat.side_effect = [RuntimeError("网络超时"), "第二次回复"]
        with patch("excelmanus.cli.console") as mock_console, \
             patch("excelmanus.cli.StreamRenderer"):
            mock_console.input.side_effect = ["第一次输入", "第二次输入", "exit"]
            _run(_repl_loop(engine))
            # 验证 engine.chat 被调用了两次（异常后继续）
            assert engine.chat.call_count == 2
            # 验证错误信息被输出
            error_printed = any(
                "网络超时" in str(call) for call in mock_console.print.call_args_list
            )
            assert error_printed
            # 验证最终正常退出（显示告别信息）
            farewell_printed = any(
                "再见" in str(call) for call in mock_console.print.call_args_list
            )
            assert farewell_printed

    def test_final_reply_rendered_as_markdown_after_cards(self) -> None:
        """最终回复应在工具调用卡片之后以 Panel(Markdown) 形式渲染。"""
        engine = _make_engine()
        engine.chat.return_value = "**分析结果**：销售额增长 20%"
        with patch("excelmanus.cli.console") as mock_console, \
             patch("excelmanus.cli.StreamRenderer") as mock_renderer_cls:
            mock_console.input.side_effect = ["分析数据", "exit"]
            mock_renderer = MagicMock()
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
        """每次自然语言输入应创建新的 StreamRenderer 实例。"""
        engine = _make_engine()
        engine.chat.side_effect = ["回复1", "回复2"]
        with patch("excelmanus.cli.console") as mock_console, \
             patch("excelmanus.cli.StreamRenderer") as mock_renderer_cls:
            mock_console.input.side_effect = ["输入1", "输入2", "exit"]
            mock_renderer_cls.return_value = MagicMock()
            _run(_repl_loop(engine))
            # 每次自然语言输入都应创建新的 StreamRenderer
            assert mock_renderer_cls.call_count == 2


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
