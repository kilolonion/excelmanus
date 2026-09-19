from __future__ import annotations

import json

import pytest

from excelmanus.message_serialization import assistant_message_to_dict
from excelmanus.message_serialization import sanitize_tool_call_arguments
from excelmanus.providers.claude import _Function as ClaudeFunction
from excelmanus.providers.claude import _Message as ClaudeMessage
from excelmanus.providers.claude import _ToolCall as ClaudeToolCall
from excelmanus.providers.gemini import _Function as GeminiFunction
from excelmanus.providers.gemini import _Message as GeminiMessage
from excelmanus.providers.gemini import _ToolCall as GeminiToolCall
from excelmanus.providers.openai_responses import _Function as ResponsesFunction
from excelmanus.providers.openai_responses import _Message as ResponsesMessage
from excelmanus.providers.openai_responses import _ToolCall as ResponsesToolCall


@pytest.mark.parametrize(
    ("message_cls", "tool_call_cls", "function_cls"),
    [
        (GeminiMessage, GeminiToolCall, GeminiFunction),
        (ClaudeMessage, ClaudeToolCall, ClaudeFunction),
        (ResponsesMessage, ResponsesToolCall, ResponsesFunction),
    ],
)
def test_assistant_message_to_dict_preserves_provider_tool_calls(
    message_cls,
    tool_call_cls,
    function_cls,
) -> None:
    message = message_cls(
        content=None,
        tool_calls=[
            tool_call_cls(
                id="call_1",
                function=function_cls(
                    name="read_excel",
                    arguments=json.dumps({"file_path": "data.xlsx"}),
                ),
            )
        ],
    )

    payload = assistant_message_to_dict(message)

    assert payload["role"] == "assistant"
    assert payload["content"] is None
    assert payload["tool_calls"][0]["id"] == "call_1"
    assert payload["tool_calls"][0]["function"]["name"] == "read_excel"


def test_assistant_message_to_dict_fallback_for_text_message() -> None:
    payload = assistant_message_to_dict("hello")

    assert payload == {"role": "assistant", "content": ""}


class TestSanitizeToolCallArguments:
    def test_invalid_json_args_replaced_with_marker(self) -> None:
        calls = [
            {
                "id": "c1",
                "type": "function",
                "function": {
                    "name": "format_spreadsheet",
                    "arguments": '{"file_path": "a.xlsx", "operations": ',
                },
            }
        ]
        result = sanitize_tool_call_arguments(calls)
        raw = result[0]["function"]["arguments"]
        parsed = json.loads(raw)  # 必须是合法 JSON，避免污染后续请求
        assert "_malformed_arguments" in parsed
        assert parsed["_truncated_len"] >= len(parsed["_malformed_arguments"])

    def test_valid_args_unchanged(self) -> None:
        valid = '{"a": 1}'
        calls = [{"function": {"name": "t", "arguments": valid}}]
        result = sanitize_tool_call_arguments(calls)
        assert result[0]["function"]["arguments"] == valid

    def test_none_args_become_empty_object(self) -> None:
        calls = [{"function": {"name": "t", "arguments": None}}]
        result = sanitize_tool_call_arguments(calls)
        assert result[0]["function"]["arguments"] == "{}"

    def test_dict_args_normalized_to_string(self) -> None:
        calls = [{"function": {"name": "t", "arguments": {"a": 1}}}]
        result = sanitize_tool_call_arguments(calls)
        assert json.loads(result[0]["function"]["arguments"]) == {"a": 1}
