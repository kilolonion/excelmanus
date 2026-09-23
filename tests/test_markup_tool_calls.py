"""MiMo 风格 <tool_call> 正文标签的解析、剥离与 tool_calls 修复。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.markup_tool_calls import (
    parse_markup_tool_calls,
    recover_tool_calls_from_markup,
    strip_markup_tool_calls,
)
from excelmanus.tools import ToolRegistry
from excelmanus.tools.registry import ToolDef


def _msg(content) -> SimpleNamespace:
    return SimpleNamespace(content=content)


def _call(name: str, arguments, tc_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(
        id=tc_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


MIMO_MARKUP = (
    "现在把宽度恢复回去，高度保留。"
    "<tool_call><function=format_spreadsheet>"
    "<parameter=expected_version>sha256:d586608f</parameter>"
    "<parameter=file_path>outputs/收款收据_布局还原版.xlsx</parameter>"
    '<parameter=operations>[{"kind": "size", "columns": {"A": 7}}, '
    '{"kind": "print_layout", "print_layout": {"fit_to_width": 1}}]</parameter>'
    "</function></tool_call>"
)


class TestParseMarkupToolCalls:
    def test_mimo_parameter_format(self) -> None:
        calls = parse_markup_tool_calls(MIMO_MARKUP)
        assert len(calls) == 1
        call = calls[0]
        assert call["name"] == "format_spreadsheet"
        assert call["arguments"]["expected_version"] == "sha256:d586608f"
        assert call["arguments"]["file_path"] == "outputs/收款收据_布局还原版.xlsx"
        assert call["arguments"]["operations"] == [
            {"kind": "size", "columns": {"A": 7}},
            {"kind": "print_layout", "print_layout": {"fit_to_width": 1}},
        ]

    def test_json_body_format(self) -> None:
        text = '<tool_call>{"name": "foo", "arguments": {"a": 1}}</tool_call>'
        assert parse_markup_tool_calls(text) == [
            {"name": "foo", "arguments": {"a": 1}}
        ]

    def test_multiple_blocks(self) -> None:
        text = (
            "<tool_call><function=a><parameter=x>1</parameter></function></tool_call>"
            "中间文本"
            "<tool_call><function=b><parameter=y>[1,2]</parameter></function></tool_call>"
        )
        calls = parse_markup_tool_calls(text)
        assert [c["name"] for c in calls] == ["a", "b"]
        assert calls[1]["arguments"] == {"y": [1, 2]}

    def test_fenced_markup_ignored(self) -> None:
        text = "示例：```\n<tool_call><function=foo></function></tool_call>\n```"
        assert parse_markup_tool_calls(text) == []
        assert strip_markup_tool_calls(text) == text

    def test_plain_text(self) -> None:
        assert parse_markup_tool_calls("普通回复，没有标签") == []
        assert strip_markup_tool_calls("普通回复，没有标签") == "普通回复，没有标签"


class TestStripMarkupToolCalls:
    def test_strips_block_and_keeps_text(self) -> None:
        cleaned = strip_markup_tool_calls(MIMO_MARKUP)
        assert "<tool_call>" not in cleaned
        assert "现在把宽度恢复回去，高度保留。" in cleaned

    def test_unclosed_tail_block_stripped(self) -> None:
        text = "前文。<tool_call><function=foo><parameter=a>1</parameter>"
        assert strip_markup_tool_calls(text) == "前文。"


class TestRecoverToolCallsFromMarkup:
    def test_repairs_truncated_arguments(self) -> None:
        """复刻导出日志：网关把数组参数截断在 \"operations\": 处。"""
        message = _msg(MIMO_MARKUP)
        tc = _call(
            "format_spreadsheet",
            '{"expected_version": "sha256:d586608f", '
            '"file_path": "outputs/收款收据_布局还原版.xlsx", "operations": ',
        )
        calls, stripped = recover_tool_calls_from_markup(message, [tc])

        assert len(calls) == 1
        args = json.loads(calls[0].function.arguments)
        assert args["operations"][0] == {"kind": "size", "columns": {"A": 7}}
        assert args["file_path"] == "outputs/收款收据_布局还原版.xlsx"
        assert calls[0].id == "call_1"  # 保留原 id，tool_result 才能关联
        assert "<tool_call>" not in message.content
        assert stripped is not None and "现在把宽度恢复回去" in stripped

    def test_creates_call_when_structured_missing(self) -> None:
        message = _msg(
            "读取文件。<tool_call><function=read_text_file>"
            "<parameter=file_path>a.txt</parameter></function></tool_call>"
        )
        calls, stripped = recover_tool_calls_from_markup(message, [])
        assert len(calls) == 1
        assert calls[0].function.name == "read_text_file"
        assert json.loads(calls[0].function.arguments) == {"file_path": "a.txt"}
        assert calls[0].id  # 合成 id，供 tool_result 关联
        assert stripped is not None and "<tool_call>" not in stripped

    def test_valid_arguments_untouched_no_phantom_append(self) -> None:
        """结构化参数合法时只剥离标签，不追加同批标签调用。"""
        message = _msg(
            'ok。<tool_call><function=foo><parameter=a>1</parameter></function></tool_call>'
        )
        tc = _call("bar", '{"x": 1}')
        calls, _ = recover_tool_calls_from_markup(message, [tc])
        assert len(calls) == 1
        assert calls[0].function.arguments == '{"x": 1}'
        assert "<tool_call>" not in message.content

    def test_duplicate_name_markup_not_appended(self) -> None:
        """同名标签调用视为重复，不追加避免重复执行。"""
        content = (
            "<tool_call><function=a><parameter=x>1</parameter></function></tool_call>"
            "<tool_call><function=b><parameter=y>2</parameter></function></tool_call>"
        )
        message = _msg(content)
        tc_a = _call("a", '{"x": ', tc_id="call_a")   # 截断
        tc_b = _call("b", '{"y": 2}', tc_id="call_b")  # 合法
        calls, _ = recover_tool_calls_from_markup(message, [tc_a, tc_b])
        assert len(calls) == 2
        assert json.loads(calls[0].function.arguments) == {"x": 1}
        assert calls[1].function.arguments == '{"y": 2}'

    def test_no_markup_returns_unchanged(self) -> None:
        message = _msg("普通回复")
        tc = _call("t", "{}")
        calls, stripped = recover_tool_calls_from_markup(message, [tc])
        assert calls == [tc]
        assert stripped is None
        assert message.content == "普通回复"

    def test_empty_args_filled_only_by_same_name(self) -> None:
        """无参调用不被无关标签塞参数。"""
        message = _msg(
            "<tool_call><function=other><parameter=a>1</parameter></function></tool_call>"
        )
        tc = _call("no_args_tool", "")
        calls, _ = recover_tool_calls_from_markup(message, [tc])
        assert calls[0].function.arguments == ""


def _make_engine() -> AgentEngine:
    config = ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        max_iterations=20,
        max_consecutive_failures=3,
        workspace_root=str(Path(__file__).resolve().parent),
    )
    registry = ToolRegistry()

    def add_numbers(a: int, b: int) -> int:
        return a + b

    registry.register_tools([
        ToolDef(
            name="add_numbers",
            description="两数相加",
            input_schema={
                "type": "object",
                "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"],
            },
            func=add_numbers,
        )
    ])
    return AgentEngine(config, registry)


class TestMarkupToolCallEndToEnd:
    """引擎级回归：结构化参数截断 + 正文 <tool_call> 标签 → 工具正常执行。"""

    @pytest.mark.asyncio
    async def test_truncated_args_recovered_and_executed(self) -> None:
        engine = _make_engine()
        markup = (
            "计算两数之和。"
            "<tool_call><function=add_numbers>"
            "<parameter=a>1</parameter><parameter=b>2</parameter>"
            "</function></tool_call>"
        )
        broken = SimpleNamespace(
            id="call_1", type="function",
            function=SimpleNamespace(
                name="add_numbers", arguments='{"a": 1, "b": ',
            ),
        )
        response_1 = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=markup, tool_calls=[broken])
            )]
        )
        response_2 = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="已处理", tool_calls=None)
            )]
        )
        engine._client.chat.completions.create = AsyncMock(
            side_effect=[response_1, response_2]
        )

        result = await engine.followup("一加二等于几")

        assert result.reply == "已处理"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].success is True
        assert "3" in result.tool_calls[0].result
        tool_msgs = [
            m for m in engine.memory.get_messages() if m.get("role") == "tool"
        ]
        assert len(tool_msgs) == 1
        assert "参数解析错误" not in tool_msgs[0]["content"]
        assistant_msgs = [
            m for m in engine.memory.get_messages()
            if m.get("role") == "assistant" and m.get("tool_calls")
        ]
        assert assistant_msgs
        assert "<tool_call>" not in (assistant_msgs[0].get("content") or "")
