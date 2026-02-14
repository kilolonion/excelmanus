"""单元测试：任务清单数据模型。

测试 TaskStatus 枚举、TaskItem 状态转换、TaskStore 错误处理。
后续任务（2.2, 3.3, 6.3, 7.2）会追加更多测试到本文件。

_Requirements: 1.3, 1.5_
"""

from __future__ import annotations

import pytest

from excelmanus.task_list import TaskItem, TaskStatus, TaskStore, VALID_TRANSITIONS


class TestTaskStatusEnum:
    """测试 TaskStatus 枚举。"""

    def test_exactly_four_statuses(self) -> None:
        """TaskStatus 应恰好包含四种状态。"""
        assert len(TaskStatus) == 4

    def test_status_values(self) -> None:
        """验证四种状态的值。"""
        expected = {"pending", "in_progress", "completed", "failed"}
        actual = {s.value for s in TaskStatus}
        assert actual == expected


class TestTaskItemTransition:
    """测试 TaskItem 非法状态转换抛出 ValueError。"""

    def test_pending_to_completed_raises(self) -> None:
        """pending → completed 是非法转换。"""
        item = TaskItem(title="测试")
        with pytest.raises(ValueError, match="非法状态转换"):
            item.transition(TaskStatus.COMPLETED)

    def test_pending_to_failed_raises(self) -> None:
        """pending → failed 是非法转换。"""
        item = TaskItem(title="测试")
        with pytest.raises(ValueError, match="非法状态转换"):
            item.transition(TaskStatus.FAILED)

    def test_pending_to_pending_raises(self) -> None:
        """pending → pending 是非法转换（自身转换不合法）。"""
        item = TaskItem(title="测试")
        with pytest.raises(ValueError, match="非法状态转换"):
            item.transition(TaskStatus.PENDING)

    def test_completed_to_any_raises(self) -> None:
        """completed 是终态，不能转换到任何状态。"""
        item = TaskItem(title="测试", status=TaskStatus.COMPLETED)
        for target in TaskStatus:
            with pytest.raises(ValueError, match="非法状态转换"):
                item.transition(target)

    def test_failed_to_any_raises(self) -> None:
        """failed 是终态，不能转换到任何状态。"""
        item = TaskItem(title="测试", status=TaskStatus.FAILED)
        for target in TaskStatus:
            with pytest.raises(ValueError, match="非法状态转换"):
                item.transition(target)


class TestTaskStoreUpdateItemErrors:
    """测试 TaskStore 无活跃 TaskList 时 update_item 报错。"""

    def test_update_item_without_active_task_list(self) -> None:
        """无活跃 TaskList 时调用 update_item 应抛出 ValueError。"""
        store = TaskStore()
        with pytest.raises(ValueError, match="当前没有活跃的任务清单"):
            store.update_item(0, TaskStatus.IN_PROGRESS)

    def test_update_item_after_clear(self) -> None:
        """清除 TaskList 后调用 update_item 应抛出 ValueError。"""
        store = TaskStore()
        store.create("测试清单", ["子任务1"])
        store.clear()
        with pytest.raises(ValueError, match="当前没有活跃的任务清单"):
            store.update_item(0, TaskStatus.IN_PROGRESS)


# ---------------------------------------------------------------------------
# 任务 2.2: 工具部分单元测试
# _Requirements: 2.4, 2.5, 2.6_
# ---------------------------------------------------------------------------

from excelmanus.tools.registry import ToolDef
from excelmanus.tools import task_tools
from excelmanus.task_list import TaskStore


class TestGetToolsSchema:
    """测试 get_tools() 返回的 ToolDef schema 格式合规。"""

    def test_returns_list_of_tooldef(self) -> None:
        """get_tools() 应返回 ToolDef 实例列表。"""
        tools = task_tools.get_tools()
        assert isinstance(tools, list)
        assert len(tools) == 2
        for tool in tools:
            assert isinstance(tool, ToolDef)

    def test_tooldef_has_required_fields(self) -> None:
        """每个 ToolDef 应包含 name、description、input_schema、func。"""
        for tool in task_tools.get_tools():
            assert isinstance(tool.name, str) and tool.name
            assert isinstance(tool.description, str) and tool.description
            assert isinstance(tool.input_schema, dict)
            assert callable(tool.func)

    def test_input_schema_has_required_key(self) -> None:
        """input_schema 应包含 'required' 字段。"""
        for tool in task_tools.get_tools():
            schema = tool.input_schema
            assert "type" in schema
            assert schema["type"] == "object"
            assert "properties" in schema
            assert "required" in schema
            assert isinstance(schema["required"], list)


class TestTaskUpdateInvalidStatus:
    """测试 task_update 传入无效状态字符串抛错。"""

    def setup_method(self) -> None:
        """每个测试前注入新的 TaskStore 并创建一个任务清单。"""
        self.store = TaskStore()
        task_tools.init_store(self.store)
        task_tools.task_create("测试清单", ["子任务1"])

    def test_invalid_status_raises(self) -> None:
        """传入无效状态字符串应抛出 ValueError。"""
        with pytest.raises(ValueError, match="无效状态"):
            task_tools.task_update(0, "invalid_status")

    def test_invalid_status_error_lists_valid_values(self) -> None:
        """错误描述应列出合法状态值。"""
        with pytest.raises(ValueError) as exc_info:
            task_tools.task_update(0, "bad")
        message = str(exc_info.value)
        for status in TaskStatus:
            assert status.value in message


class TestTaskCreateEmptySubtasks:
    """测试 task_create 空子任务列表正常工作。"""

    def setup_method(self) -> None:
        """每个测试前注入新的 TaskStore。"""
        self.store = TaskStore()
        task_tools.init_store(self.store)

    def test_empty_subtasks_returns_success(self) -> None:
        """空子任务列表应返回成功描述字符串。"""
        result = task_tools.task_create("空清单", [])
        assert "已创建任务清单" in result
        assert "空清单" in result

    def test_empty_subtasks_store_has_zero_items(self) -> None:
        """空子任务列表创建后，TaskStore.current 应有 0 个 items。"""
        task_tools.task_create("空清单", [])
        assert self.store.current is not None
        assert len(self.store.current.items) == 0


# ---------------------------------------------------------------------------
# 任务 3.3: 事件部分单元测试
# _Requirements: 3.3, 3.4_
# ---------------------------------------------------------------------------

from excelmanus.events import EventType, ToolCallEvent


class TestEventTypeTaskMembers:
    """测试 EventType 枚举包含任务清单相关成员。"""

    def test_has_task_list_created(self) -> None:
        """EventType 应包含 TASK_LIST_CREATED 成员。"""
        assert hasattr(EventType, "TASK_LIST_CREATED")
        assert EventType.TASK_LIST_CREATED.value == "task_list_created"

    def test_has_task_item_updated(self) -> None:
        """EventType 应包含 TASK_ITEM_UPDATED 成员。"""
        assert hasattr(EventType, "TASK_ITEM_UPDATED")
        assert EventType.TASK_ITEM_UPDATED.value == "task_item_updated"


class TestToolCallEventFromDictTaskFields:
    """测试 ToolCallEvent.from_dict 能正确反序列化任务相关字段。"""

    def _make_event_dict(self, **overrides: object) -> dict:
        """构造一个包含任务字段的事件字典。"""
        base = ToolCallEvent(
            event_type=EventType.TASK_LIST_CREATED,
            task_list_data={
                "title": "分析数据",
                "items": [
                    {"title": "读取文件", "status": "pending"},
                    {"title": "清洗数据", "status": "in_progress"},
                ],
                "created_at": "2025-01-15T10:30:00",
                "progress": {"pending": 1, "in_progress": 1, "completed": 0, "failed": 0},
            },
            task_index=1,
            task_status="in_progress",
            task_result="正在处理中",
        ).to_dict()
        base.update(overrides)
        return base

    def test_from_dict_preserves_task_list_data(self) -> None:
        """from_dict 应正确还原 task_list_data 字段。"""
        data = self._make_event_dict()
        event = ToolCallEvent.from_dict(data)
        assert event.task_list_data is not None
        assert event.task_list_data["title"] == "分析数据"
        assert len(event.task_list_data["items"]) == 2

    def test_from_dict_preserves_task_index(self) -> None:
        """from_dict 应正确还原 task_index 字段。"""
        data = self._make_event_dict()
        event = ToolCallEvent.from_dict(data)
        assert event.task_index == 1

    def test_from_dict_preserves_task_status(self) -> None:
        """from_dict 应正确还原 task_status 字段。"""
        data = self._make_event_dict()
        event = ToolCallEvent.from_dict(data)
        assert event.task_status == "in_progress"

    def test_from_dict_preserves_task_result(self) -> None:
        """from_dict 应正确还原 task_result 字段。"""
        data = self._make_event_dict()
        event = ToolCallEvent.from_dict(data)
        assert event.task_result == "正在处理中"

    def test_from_dict_with_none_task_fields(self) -> None:
        """当任务字段为 None/空时，from_dict 应正确还原默认值。"""
        event = ToolCallEvent(event_type=EventType.TASK_ITEM_UPDATED)
        data = event.to_dict()
        restored = ToolCallEvent.from_dict(data)
        assert restored.task_list_data is None
        assert restored.task_index is None
        assert restored.task_status == ""
        assert restored.task_result is None


# ---------------------------------------------------------------------------
# 任务 6.3: 渲染部分单元测试
# _Requirements: 4.4, 4.5_
# ---------------------------------------------------------------------------

import io
import re

from rich.console import Console

from excelmanus.renderer import StreamRenderer


def _strip_ansi(text: str) -> str:
    """移除 ANSI 转义序列，便于断言纯文本内容。"""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestRendererSummaryLine:
    """测试全部完成时显示摘要行。

    当所有任务项均为 completed 或 failed 时，
    渲染器应输出包含 '全部完成' 的摘要行，并显示 ✅ 和 ❌ 计数。
    _Requirements: 4.4_
    """

    def _make_all_done_event(
        self, completed: int = 2, failed: int = 1
    ) -> ToolCallEvent:
        """构造一个全部完成/失败的 TASK_ITEM_UPDATED 事件。"""
        items = [
            {"title": f"子任务{i}", "status": "completed"}
            for i in range(completed)
        ] + [
            {"title": f"失败任务{i}", "status": "failed"}
            for i in range(failed)
        ]
        total = completed + failed
        return ToolCallEvent(
            event_type=EventType.TASK_ITEM_UPDATED,
            task_list_data={
                "title": "测试清单",
                "items": items,
                "created_at": "2025-01-15T10:30:00",
                "progress": {
                    "pending": 0,
                    "in_progress": 0,
                    "completed": completed,
                    "failed": failed,
                },
            },
            task_index=total - 1,
            task_status="completed" if failed == 0 else "failed",
        )

    def test_summary_line_shown_when_all_done(self) -> None:
        """全部完成时应显示包含 '全部完成' 的摘要行。"""
        buf = io.StringIO()
        console = Console(file=buf, width=120, force_terminal=True, no_color=True)
        renderer = StreamRenderer(console)

        event = self._make_all_done_event(completed=3, failed=1)
        renderer.handle_event(event)

        output = buf.getvalue()
        assert "全部完成" in output

    def test_summary_line_contains_completed_count(self) -> None:
        """摘要行应包含 ✅ 完成计数。"""
        buf = io.StringIO()
        console = Console(file=buf, width=120, force_terminal=True, no_color=True)
        renderer = StreamRenderer(console)

        event = self._make_all_done_event(completed=5, failed=0)
        renderer.handle_event(event)

        output = _strip_ansi(buf.getvalue())
        assert "✅5" in output or "✅ 5" in output

    def test_summary_line_contains_failed_count(self) -> None:
        """摘要行应包含 ❌ 失败计数。"""
        buf = io.StringIO()
        console = Console(file=buf, width=120, force_terminal=True, no_color=True)
        renderer = StreamRenderer(console)

        event = self._make_all_done_event(completed=2, failed=3)
        renderer.handle_event(event)

        output = _strip_ansi(buf.getvalue())
        assert "❌3" in output or "❌ 3" in output

    def test_no_summary_when_tasks_still_pending(self) -> None:
        """仍有 pending 任务时不应显示摘要行。"""
        buf = io.StringIO()
        console = Console(file=buf, width=120, force_terminal=True, no_color=True)
        renderer = StreamRenderer(console)

        event = ToolCallEvent(
            event_type=EventType.TASK_ITEM_UPDATED,
            task_list_data={
                "title": "未完成清单",
                "items": [
                    {"title": "已完成", "status": "completed"},
                    {"title": "待执行", "status": "pending"},
                ],
                "created_at": "2025-01-15T10:30:00",
                "progress": {
                    "pending": 1,
                    "in_progress": 0,
                    "completed": 1,
                    "failed": 0,
                },
            },
            task_index=0,
            task_status="completed",
        )
        renderer.handle_event(event)

        output = buf.getvalue()
        assert "全部完成" not in output


class TestRendererNarrowTerminal:
    """测试窄终端（宽度 < 60）渲染紧凑格式。

    窄终端下应使用紧凑格式：图标直接跟随索引和标题，无前导缩进空格。
    _Requirements: 4.5_
    """

    def _make_task_list_event(self) -> ToolCallEvent:
        """构造一个 TASK_LIST_CREATED 事件。"""
        return ToolCallEvent(
            event_type=EventType.TASK_LIST_CREATED,
            task_list_data={
                "title": "紧凑测试",
                "items": [
                    {"title": "子任务A", "status": "pending"},
                    {"title": "子任务B", "status": "pending"},
                ],
                "created_at": "2025-01-15T10:30:00",
                "progress": {
                    "pending": 2,
                    "in_progress": 0,
                    "completed": 0,
                    "failed": 0,
                },
            },
        )

    def test_narrow_no_wide_indentation(self) -> None:
        """窄终端输出不应包含宽格式的 5 空格缩进行。"""
        buf = io.StringIO()
        console = Console(file=buf, width=40, force_terminal=True, no_color=True)
        renderer = StreamRenderer(console)

        event = self._make_task_list_event()
        renderer.handle_event(event)

        output = buf.getvalue()
        lines = output.strip().split("\n")
        # 宽格式中任务项行以 "     " (5空格) 开头，窄格式不应有此缩进
        for line in lines:
            if "⬜" in line:
                assert not line.startswith("     "), (
                    f"窄终端不应使用 5 空格缩进: {line!r}"
                )

    def test_narrow_compact_format(self) -> None:
        """窄终端应使用紧凑格式：图标直接跟随索引。"""
        buf = io.StringIO()
        console = Console(file=buf, width=40, force_terminal=True, no_color=True)
        renderer = StreamRenderer(console)

        event = self._make_task_list_event()
        renderer.handle_event(event)

        output = _strip_ansi(buf.getvalue())
        # 紧凑格式: "⬜0.子任务A"（图标紧跟索引，无空格分隔）
        assert "⬜0." in output
        assert "⬜1." in output

    def test_wide_terminal_uses_indentation(self) -> None:
        """宽终端应使用带缩进的格式作为对照。"""
        buf = io.StringIO()
        console = Console(file=buf, width=120, force_terminal=True, no_color=True)
        renderer = StreamRenderer(console)

        event = self._make_task_list_event()
        renderer.handle_event(event)

        output = buf.getvalue()
        # 宽格式: "     ⬜ 0. 子任务A"（5空格缩进 + 图标 + 空格 + 索引）
        assert "     ⬜" in output


# ---------------------------------------------------------------------------
# 7.2 API SSE 事件格式测试
# _Requirements: 5.1, 5.2_
# ---------------------------------------------------------------------------

import json

from excelmanus.api import _sse_event_to_sse
from excelmanus.events import EventType, ToolCallEvent


class TestSseEventToSseTaskUpdate:
    """测试 _sse_event_to_sse 对任务事件返回正确格式的 SSE 文本。"""

    def _make_task_list_data(self) -> dict:
        """构造一个示例 TaskList 序列化字典。"""
        return {
            "title": "分析销售数据",
            "items": [
                {"title": "读取文件", "status": "completed"},
                {"title": "数据清洗", "status": "in_progress"},
                {"title": "生成图表", "status": "pending"},
            ],
            "created_at": "2025-01-15T10:30:00",
            "progress": {
                "pending": 1,
                "in_progress": 1,
                "completed": 1,
                "failed": 0,
            },
        }

    def test_task_list_created_sse_event_type(self) -> None:
        """TASK_LIST_CREATED 事件的 SSE 类型应为 'task_update'。"""
        event = ToolCallEvent(
            event_type=EventType.TASK_LIST_CREATED,
            task_list_data=self._make_task_list_data(),
            task_index=None,
            task_status="",
        )
        sse_text = _sse_event_to_sse(event, safe_mode=False)
        assert sse_text is not None
        assert sse_text.startswith("event: task_update\n")

    def test_task_list_created_payload_fields(self) -> None:
        """TASK_LIST_CREATED 事件的 payload 应包含 task_list、task_index、task_status 字段。"""
        task_list_data = self._make_task_list_data()
        event = ToolCallEvent(
            event_type=EventType.TASK_LIST_CREATED,
            task_list_data=task_list_data,
            task_index=None,
            task_status="",
        )
        sse_text = _sse_event_to_sse(event, safe_mode=False)
        assert sse_text is not None
        # 解析 data 行
        data_line = sse_text.split("\n")[1]
        assert data_line.startswith("data: ")
        payload = json.loads(data_line[len("data: "):])
        assert payload["task_list"] == task_list_data
        assert payload["task_index"] is None
        assert payload["task_status"] == ""

    def test_task_item_updated_sse_event_type(self) -> None:
        """TASK_ITEM_UPDATED 事件的 SSE 类型应为 'task_update'。"""
        event = ToolCallEvent(
            event_type=EventType.TASK_ITEM_UPDATED,
            task_list_data=self._make_task_list_data(),
            task_index=0,
            task_status="completed",
        )
        sse_text = _sse_event_to_sse(event, safe_mode=False)
        assert sse_text is not None
        assert sse_text.startswith("event: task_update\n")

    def test_task_item_updated_payload_fields(self) -> None:
        """TASK_ITEM_UPDATED 事件的 payload 应包含正确的 task_index 和 task_status。"""
        task_list_data = self._make_task_list_data()
        event = ToolCallEvent(
            event_type=EventType.TASK_ITEM_UPDATED,
            task_list_data=task_list_data,
            task_index=1,
            task_status="in_progress",
        )
        sse_text = _sse_event_to_sse(event, safe_mode=False)
        assert sse_text is not None
        data_line = sse_text.split("\n")[1]
        payload = json.loads(data_line[len("data: "):])
        assert payload["task_list"] == task_list_data
        assert payload["task_index"] == 1
        assert payload["task_status"] == "in_progress"

    def test_sse_text_ends_with_double_newline(self) -> None:
        """SSE 文本应以双换行符结尾（SSE 协议要求）。"""
        event = ToolCallEvent(
            event_type=EventType.TASK_LIST_CREATED,
            task_list_data=self._make_task_list_data(),
        )
        sse_text = _sse_event_to_sse(event, safe_mode=False)
        assert sse_text is not None
        assert sse_text.endswith("\n\n")
