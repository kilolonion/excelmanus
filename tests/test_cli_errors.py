"""CLI 异常分级与恢复建议面板单元测试。

覆盖：
- 不同异常类型映射到不同 category（config / network / engine / unknown）
- 每个 category 有对应的恢复建议命令列表
- 最终输出为结构化 Rich Panel 而非裸字符串
- 异常渲染本身不崩溃（二次异常降级为纯文本）
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from excelmanus.cli_errors import (
    CliErrorCategory,
    classify_error,
    render_error_panel,
    recovery_hints,
)


# ══════════════════════════════════════════════════════════
# classify_error 分类测试
# ══════════════════════════════════════════════════════════


class TestClassifyError:
    def test_config_error(self) -> None:
        from excelmanus.config import ConfigError
        cat = classify_error(ConfigError("缺少 API Key"))
        assert cat == CliErrorCategory.CONFIG

    def test_connection_error(self) -> None:
        cat = classify_error(ConnectionError("无法连接到 API"))
        assert cat == CliErrorCategory.NETWORK

    def test_timeout_error(self) -> None:
        cat = classify_error(TimeoutError("请求超时"))
        assert cat == CliErrorCategory.NETWORK

    def test_os_error_is_network(self) -> None:
        cat = classify_error(OSError("Connection refused"))
        assert cat == CliErrorCategory.NETWORK

    def test_value_error_is_engine(self) -> None:
        cat = classify_error(ValueError("参数无效"))
        assert cat == CliErrorCategory.ENGINE

    def test_runtime_error_is_engine(self) -> None:
        cat = classify_error(RuntimeError("引擎内部错误"))
        assert cat == CliErrorCategory.ENGINE

    def test_generic_exception_is_unknown(self) -> None:
        cat = classify_error(Exception("未知错误"))
        assert cat == CliErrorCategory.UNKNOWN

    def test_keyboard_interrupt_is_unknown(self) -> None:
        """KeyboardInterrupt 不应被分类（由上层处理），但 classify 应返回 UNKNOWN。"""
        cat = classify_error(KeyboardInterrupt())
        assert cat == CliErrorCategory.UNKNOWN


# ══════════════════════════════════════════════════════════
# recovery_hints 恢复建议测试
# ══════════════════════════════════════════════════════════


class TestRecoveryHints:
    def test_config_hints_contain_config_command(self) -> None:
        hints = recovery_hints(CliErrorCategory.CONFIG)
        assert any("/config" in h for h in hints)

    def test_network_hints_contain_retry(self) -> None:
        hints = recovery_hints(CliErrorCategory.NETWORK)
        assert any("重试" in h or "retry" in h.lower() for h in hints)

    def test_engine_hints_not_empty(self) -> None:
        hints = recovery_hints(CliErrorCategory.ENGINE)
        assert len(hints) >= 1

    def test_unknown_hints_contain_help(self) -> None:
        hints = recovery_hints(CliErrorCategory.UNKNOWN)
        assert any("/help" in h for h in hints)


# ══════════════════════════════════════════════════════════
# render_error_panel 渲染测试
# ══════════════════════════════════════════════════════════


class TestRenderErrorPanel:
    def _make_console(self, width: int = 120) -> Console:
        return Console(file=StringIO(), width=width, force_terminal=True)

    def _get_output(self, console: Console) -> str:
        console.file.seek(0)
        return console.file.read()

    def test_renders_structured_panel(self) -> None:
        """输出应包含错误信息和恢复建议，使用 Panel 包裹。"""
        c = self._make_console()
        render_error_panel(
            c,
            error=RuntimeError("引擎崩溃"),
            error_label="处理请求",
        )
        output = self._get_output(c)
        assert "引擎崩溃" in output
        assert "处理请求" in output or "错误" in output

    def test_contains_recovery_hints(self) -> None:
        """输出应包含恢复建议。"""
        c = self._make_console()
        render_error_panel(
            c,
            error=ConnectionError("连接超时"),
            error_label="网络请求",
        )
        output = self._get_output(c)
        # 应包含至少一条恢复建议
        assert "建议" in output or "重试" in output or "/help" in output

    def test_narrow_terminal_no_crash(self) -> None:
        """窄终端下不崩溃。"""
        c = self._make_console(width=40)
        render_error_panel(
            c,
            error=Exception("窄终端错误"),
            error_label="测试",
        )
        output = self._get_output(c)
        assert "窄终端错误" in output

    def test_render_exception_does_not_crash(self) -> None:
        """渲染异常时降级为纯文本，不崩溃。"""
        c = self._make_console()
        # 即使 error 对象很奇怪也不应崩溃
        render_error_panel(
            c,
            error=Exception(""),
            error_label="测试降级",
        )
        output = self._get_output(c)
        assert "测试降级" in output or "错误" in output
