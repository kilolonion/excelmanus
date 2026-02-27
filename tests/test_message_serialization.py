from __future__ import annotations

import json

import pytest

from excelmanus.message_serialization import assistant_message_to_dict
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
