"""CLI utils 模块测试。"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from excelmanus.cli.utils import (
    RESULT_MAX_LEN,
    THINKING_SUMMARY_LEN,
    THINKING_THRESHOLD,
    format_arguments,
    format_elapsed,
    format_subagent_tools,
    is_narrow_terminal,
    separator_line,
    truncate,
)


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("hello", 10) == "hello"

    def test_exact_length_unchanged(self):
        assert truncate("hello", 5) == "hello"

    def test_long_text_truncated(self):
        result = truncate("hello world", 5)
        assert result == "hello…"
        assert len(result) == 6

    def test_empty_string(self):
        assert truncate("", 10) == ""


class TestFormatArguments:
    def test_empty_dict(self):
        assert format_arguments({}) == ""

    def test_string_value(self):
        result = format_arguments({"path": "test.xlsx"})
        assert result == 'path="test.xlsx"'

    def test_non_string_value(self):
        result = format_arguments({"count": 42})
        assert result == "count=42"

    def test_mixed_values(self):
        result = format_arguments({"path": "test.xlsx", "rows": 10})
        assert 'path="test.xlsx"' in result
        assert "rows=10" in result

    def test_long_string_truncated(self):
        long_val = "x" * 100
        result = format_arguments({"data": long_val})
        assert "…" in result


class TestFormatElapsed:
    def test_milliseconds(self):
        assert format_elapsed(0.023) == "23ms"

    def test_seconds(self):
        assert format_elapsed(3.5) == "3.5s"

    def test_minutes(self):
        assert format_elapsed(125.0) == "2m5s"

    def test_zero(self):
        assert format_elapsed(0.0) == "0ms"


class TestSeparatorLine:
    def test_default_width(self):
        line = separator_line()
        assert len(line) == 50
        assert all(c == "─" for c in line)

    def test_custom_width(self):
        line = separator_line(20)
        assert len(line) == 20


class TestIsNarrowTerminal:
    def test_wide_terminal(self):
        console = Console(file=StringIO(), width=120)
        assert is_narrow_terminal(console) is False

    def test_narrow_terminal(self):
        console = Console(file=StringIO(), width=40)
        assert is_narrow_terminal(console) is True

    def test_boundary(self):
        console = Console(file=StringIO(), width=60)
        assert is_narrow_terminal(console) is False


class TestFormatSubagentTools:
    def test_empty_list(self):
        assert format_subagent_tools([]) == "(无)"

    def test_short_list(self):
        result = format_subagent_tools(["read_excel", "filter_data"])
        assert "read_excel" in result
        assert "filter_data" in result

    def test_long_list_truncated(self):
        tools = [f"tool_{i}" for i in range(20)]
        result = format_subagent_tools(tools)
        assert "(+" in result


class TestConstants:
    def test_constants_are_positive(self):
        assert RESULT_MAX_LEN > 0
        assert THINKING_THRESHOLD > 0
        assert THINKING_SUMMARY_LEN > 0
