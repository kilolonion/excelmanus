"""ToolCallEvent 数据模型属性测试。

使用 hypothesis 验证 ToolCallEvent 的 round-trip 序列化正确性。
"""

from datetime import datetime

import pytest
from hypothesis import given, settings
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
    @settings(max_examples=200)
    def test_round_trip_preserves_equality(self, event: ToolCallEvent) -> None:
        """对于任意合法 ToolCallEvent，from_dict(to_dict(event)) == event。"""
        serialized = event.to_dict()
        restored = ToolCallEvent.from_dict(serialized)

        assert restored.event_type == event.event_type
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
            "ITERATION_START",
        }
        actual = {member.name for member in EventType}
        assert actual == expected

    def test_enum_values(self) -> None:
        """枚举值应与设计文档中的字符串一致。"""
        assert EventType.TOOL_CALL_START.value == "tool_call_start"
        assert EventType.TOOL_CALL_END.value == "tool_call_end"
        assert EventType.THINKING.value == "thinking"
        assert EventType.ITERATION_START.value == "iteration_start"


class TestToolCallEventFields:
    """ToolCallEvent 字段完整性与默认值验证。

    _需求: 5.1_
    """

    def test_minimal_construction(self) -> None:
        """仅提供 event_type 即可构造实例，其余字段使用默认值。"""
        event = ToolCallEvent(event_type=EventType.TOOL_CALL_START)

        assert event.event_type == EventType.TOOL_CALL_START
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
            "tool_name",
            "arguments",
            "result",
            "success",
            "error",
            "thinking",
            "iteration",
            "timestamp",
        }
        assert set(annotations.keys()) == expected_fields
