"""ToolCallEvent 数据模型属性测试。

使用 hypothesis 验证 ToolCallEvent 的 round-trip 序列化正确性。
"""

from datetime import datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from excelmanus.events import EventType, ToolCallEvent

# ---------------------------------------------------------------------------
# 自定义 hypothesis strategies
# ---------------------------------------------------------------------------

# 事件类型策略：从枚举成员中随机选取
event_type_st = st.sampled_from(list(EventType))

# JSON 可序列化的简单值策略（asdict 需要可序列化）
json_primitive_st = st.one_of(
    st.text(max_size=50),
    st.integers(min_value=-(10**9), max_value=10**9),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.none(),
)

# 参数字典策略：键为非空字符串，值为简单 JSON 类型
arguments_st = st.dictionaries(
    keys=st.text(min_size=1, max_size=20),
    values=json_primitive_st,
    max_size=5,
)

# 时间戳策略：限制在合理范围内，避免极端日期导致 isoformat 问题
timestamp_st = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2099, 12, 31),
)

# 完整的 ToolCallEvent 策略
tool_call_event_st = st.builds(
    ToolCallEvent,
    event_type=event_type_st,
    tool_call_id=st.text(max_size=80),
    tool_name=st.text(max_size=50),
    arguments=arguments_st,
    result=st.text(max_size=200),
    success=st.booleans(),
    error=st.one_of(st.none(), st.text(max_size=100)),
    thinking=st.text(max_size=200),
    iteration=st.integers(min_value=0, max_value=10000),
    timestamp=timestamp_st,
)


# ---------------------------------------------------------------------------
# Property 1: ToolCallEvent round-trip 序列化
# ---------------------------------------------------------------------------


class TestToolCallEventRoundTrip:
    """**Feature: cli-beautify, Property 1: ToolCallEvent round-trip 序列化**

    **Validates: Requirements 5.3**
    """

    @given(event=tool_call_event_st)
    def test_round_trip_preserves_equality(self, event: ToolCallEvent) -> None:
        """对于任意合法 ToolCallEvent，from_dict(to_dict(event)) == event。"""
        serialized = event.to_dict()
        restored = ToolCallEvent.from_dict(serialized)

        assert restored.event_type == event.event_type
        assert restored.tool_call_id == event.tool_call_id
        assert restored.tool_name == event.tool_name
        assert restored.arguments == event.arguments
        assert restored.result == event.result
        assert restored.success == event.success
        assert restored.error == event.error
        assert restored.thinking == event.thinking
        assert restored.iteration == event.iteration
        assert restored.timestamp == event.timestamp


# ---------------------------------------------------------------------------
# 任务 1.3: ToolCallEvent 单元测试
# ---------------------------------------------------------------------------


class TestEventTypeEnum:
    """EventType 枚举完整性验证。

    _需求: 5.1_
    """

    def test_has_all_expected_members(self) -> None:
        """枚举应包含所有设计文档中定义的事件类型。"""
        expected = {
            "TOOL_CALL_START",
            "TOOL_CALL_END",
            "THINKING",
            "THINKING_DELTA",
            "TEXT_DELTA",
            "ITERATION_START",
            "ROUTE_START",
            "ROUTE_END",
            "CHAT_SUMMARY",
            "SUBAGENT_START",
            "SUBAGENT_END",
            "SUBAGENT_ITERATION",
            "SUBAGENT_SUMMARY",
            "TASK_LIST_CREATED",
            "TASK_ITEM_UPDATED",
            "USER_QUESTION",
            "PENDING_APPROVAL",
            "APPROVAL_RESOLVED",
            "MODE_CHANGED",
            "EXCEL_PREVIEW",
            "EXCEL_DIFF",
            "FILES_CHANGED",
            "MEMORY_EXTRACTED",
            "PIPELINE_PROGRESS",
        }
        actual = {member.name for member in EventType}
        assert actual == expected

    def test_enum_values(self) -> None:
        """枚举值应与设计文档中的字符串一致。"""
        assert EventType.TOOL_CALL_START.value == "tool_call_start"
        assert EventType.TOOL_CALL_END.value == "tool_call_end"
        assert EventType.THINKING.value == "thinking"
        assert EventType.ITERATION_START.value == "iteration_start"
        assert EventType.ROUTE_START.value == "route_start"
        assert EventType.ROUTE_END.value == "route_end"
        assert EventType.CHAT_SUMMARY.value == "chat_summary"

    def test_enum_snapshot_stable(self) -> None:
        """EventType 对外协议快照，避免无意破坏命名或顺序。"""
        assert [(member.name, member.value) for member in EventType] == [
            ("TOOL_CALL_START", "tool_call_start"),
            ("TOOL_CALL_END", "tool_call_end"),
            ("THINKING", "thinking"),
            ("ITERATION_START", "iteration_start"),
            ("ROUTE_START", "route_start"),
            ("ROUTE_END", "route_end"),
            ("SUBAGENT_START", "subagent_start"),
            ("SUBAGENT_END", "subagent_end"),
            ("SUBAGENT_ITERATION", "subagent_iteration"),
            ("SUBAGENT_SUMMARY", "subagent_summary"),
            ("CHAT_SUMMARY", "chat_summary"),
            ("TASK_LIST_CREATED", "task_list_created"),
            ("TASK_ITEM_UPDATED", "task_item_updated"),
            ("USER_QUESTION", "user_question"),
            ("PENDING_APPROVAL", "pending_approval"),
            ("APPROVAL_RESOLVED", "approval_resolved"),
            ("THINKING_DELTA", "thinking_delta"),
            ("TEXT_DELTA", "text_delta"),
            ("MODE_CHANGED", "mode_changed"),
            ("EXCEL_PREVIEW", "excel_preview"),
            ("EXCEL_DIFF", "excel_diff"),
            ("FILES_CHANGED", "files_changed"),
            ("PIPELINE_PROGRESS", "pipeline_progress"),
            ("MEMORY_EXTRACTED", "memory_extracted"),
        ]


class TestToolCallEventFields:
    """ToolCallEvent 字段完整性与默认值验证。

    _需求: 5.1_
    """

    def test_minimal_construction(self) -> None:
        """仅提供 event_type 即可构造实例，其余字段使用默认值。"""
        event = ToolCallEvent(event_type=EventType.TOOL_CALL_START)

        assert event.event_type == EventType.TOOL_CALL_START
        assert event.tool_call_id == ""
        assert event.tool_name == ""
        assert event.arguments == {}
        assert event.result == ""
        assert event.success is True
        assert event.error is None
        assert event.thinking == ""
        assert event.iteration == 0
        assert isinstance(event.timestamp, datetime)

    def test_all_fields_assignable(self) -> None:
        """所有字段均可在构造时显式赋值。"""
        ts = datetime(2025, 1, 15, 10, 30, 0)
        event = ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_name="read_excel",
            arguments={"file_path": "data.xlsx", "sheet": 0},
            result="读取了 100 行数据",
            success=True,
            error=None,
            thinking="需要先读取文件",
            iteration=3,
            timestamp=ts,
        )

        assert event.event_type == EventType.TOOL_CALL_END
        assert event.tool_name == "read_excel"
        assert event.arguments == {"file_path": "data.xlsx", "sheet": 0}
        assert event.result == "读取了 100 行数据"
        assert event.success is True
        assert event.error is None
        assert event.thinking == "需要先读取文件"
        assert event.iteration == 3
        assert event.timestamp == ts

    def test_default_arguments_not_shared(self) -> None:
        """不同实例的 arguments 默认字典应互相独立（非共享引用）。"""
        event_a = ToolCallEvent(event_type=EventType.THINKING)
        event_b = ToolCallEvent(event_type=EventType.THINKING)

        event_a.arguments["key"] = "value"
        assert "key" not in event_b.arguments

    def test_failure_event_fields(self) -> None:
        """失败事件应正确携带 success=False 和 error 信息。"""
        event = ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_name="write_excel",
            success=False,
            error="文件被占用",
        )

        assert event.success is False
        assert event.error == "文件被占用"

    def test_field_type_annotations(self) -> None:
        """ToolCallEvent 的类型注解应包含所有预期字段。"""
        annotations = ToolCallEvent.__dataclass_fields__
        expected_fields = {
            "event_type",
            "tool_call_id",
            "tool_name",
            "arguments",
            "result",
            "success",
            "error",
            "thinking",
            "iteration",
            "timestamp",
            "route_mode",
            "skills_used",
            "tool_scope",
            "total_iterations",
            "total_tool_calls",
            "success_count",
            "failure_count",
            "elapsed_seconds",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "subagent_reason",
            "subagent_tools",
            "subagent_summary",
            "subagent_success",
            "subagent_name",
            "subagent_permission_mode",
            "subagent_conversation_id",
            "subagent_iterations",
            "subagent_tool_calls",
            "task_list_data",
            "task_index",
            "task_status",
            "task_result",
            "question_id",
            "question_header",
            "question_text",
            "question_options",
            "question_multi_select",
            "question_queue_size",
            "approval_id",
            "approval_tool_name",
            "approval_arguments",
            "approval_undoable",
            "approval_risk_level",
            "approval_args_summary",
            "text_delta",
            "thinking_delta",
            "mode_name",
            "mode_enabled",
            "excel_file_path",
            "excel_sheet",
            "excel_columns",
            "excel_rows",
            "excel_total_rows",
            "excel_truncated",
            "excel_affected_range",
            "excel_changes",
            "changed_files",
            "memory_entries",
            "memory_trigger",
            "pipeline_message",
            "pipeline_stage",
        }
        assert set(annotations.keys()) == expected_fields
