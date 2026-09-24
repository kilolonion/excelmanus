"""ChatResult → SubagentResult：变更回填与 stop_reason 映射。"""

from __future__ import annotations

from excelmanus.engine_types import ChatResult, ToolCallResult
from excelmanus.subagent.models import SubagentConfig
from excelmanus.subagent.result import chat_to_result, format_parent_reply


def _cfg(**kwargs) -> SubagentConfig:
    kwargs.setdefault("permission_mode", "readOnly")
    return SubagentConfig(name="explorer", description="x", **kwargs)


def test_truncated_maps_max_tokens() -> None:
    chat = ChatResult(reply="写到一半", truncated=True)
    result = chat_to_result(chat, config=_cfg(), conversation_id="r1")
    assert result.stop_reason == "max-tokens"
    assert result.success is False


def test_structured_changes_and_observed_from_tool_batch() -> None:
    chat = ChatResult(
        reply="已写入",
        tool_calls=[
            ToolCallResult(
                tool_name="observe_spreadsheet",
                arguments={"file_path": "sales.xlsx"},
                result="ok",
                success=True,
            ),
            ToolCallResult(
                tool_name="apply_spreadsheet_changes",
                    arguments={"file_path": "outputs/out.xlsx", "sheet": "Sheet1"},
                result="ok",
                success=True,
            ),
            ToolCallResult(
                tool_name="apply_spreadsheet_changes",
                arguments={"file_path": "outputs/fail.xlsx"},
                result="denied",
                success=False,
            ),
        ],
    )
    result = chat_to_result(chat, config=_cfg(permission_mode="acceptEdits"), conversation_id="r2")
    assert result.stop_reason == "completed"
    assert [change.path for change in result.structured_changes] == ["outputs/out.xlsx"]
    assert result.structured_changes[0].sheets_affected == ("Sheet1",)
    assert result.observed_files == ["sales.xlsx", "outputs/out.xlsx", "outputs/fail.xlsx"]


def test_pre_execute_denied_maps_refusal() -> None:
    chat = ChatResult(
        reply="",
        tool_calls=[
            ToolCallResult(
                tool_name="copy_file",
                arguments={"source": "a.xlsx", "destination": "b.xlsx"},
                result="只读子代理拒绝写入：copy_file",
                success=False,
                error="PRE_EXECUTE_DENIED",
            )
        ],
    )
    result = chat_to_result(chat, config=_cfg(), conversation_id="r3")
    assert result.stop_reason == "refusal"
    assert result.diagnostic is not None
    assert "拒绝写入" in result.diagnostic


def test_format_parent_reply_keeps_partial_hint() -> None:
    chat = ChatResult(
        reply="中途失败",
        tool_calls=[
            ToolCallResult(
                tool_name="observe_spreadsheet",
                arguments={"file_path": "data.xlsx"},
                result="ok",
                success=True,
            )
        ],
    )
    result = chat_to_result(
        chat,
        config=_cfg(),
        conversation_id="r4",
        stop_reason="error",
        diagnostic="中途失败",
    )
    reply = format_parent_reply(result)
    assert "已保留部分产出" in reply
    assert "中途失败" in reply
