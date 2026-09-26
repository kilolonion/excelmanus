"""属性测试：任务清单数据模型与工具。

# Feature: agent-task-list, Property 1-6

使用 hypothesis 验证 TaskList 序列化往返、初始状态、状态转换合法性、
进度摘要不变量、task_create 有效性、越界索引错误。

**验证：需求 1.1, 1.2, 1.4, 1.5, 1.6, 2.3, 2.4, 6.1, 6.2, 6.3, 6.4**
"""

from __future__ import annotations

import pytest
from hypothesis import given, assume
from hypothesis import strategies as st

from excelmanus.task_list import (
    TaskItem,
    TaskList,
    TaskStatus,
    TaskStore,
    VALID_TRANSITIONS,
)
from excelmanus.tools import task_tools


# ── 辅助策略 ──────────────────────────────────────────────

# 合法的任务标题：非空可打印字符串
_title_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S", "Z"),
        blacklist_categories=("Cs",),
    ),
    min_size=1,
    max_size=50,
).filter(lambda s: s.strip())

# 可选的 result 字段
_result_strategy = st.one_of(st.none(), _title_strategy)

# TaskStatus 策略
_status_strategy = st.sampled_from(list(TaskStatus))

# 生成合法的 TaskItem（可指定状态）
_task_item_strategy = st.builds(
    TaskItem,
    title=_title_strategy,
    status=_status_strategy,
    result=_result_strategy,
)

# 生成合法的 TaskList
_task_list_strategy = st.builds(
    TaskList,
    title=_title_strategy,
    items=st.lists(_task_item_strategy, min_size=0, max_size=20),
)


# ---------------------------------------------------------------------------
# Property 1: TaskList 序列化往返一致性
# Feature: agent-task-list, Property 1: TaskList 序列化往返一致性
# **验证：需求 6.3, 6.1, 6.2, 6.4, 1.1, 1.2**
# ---------------------------------------------------------------------------


@given(task_list=_task_list_strategy)
def test_pbt_property_1_task_list_round_trip(task_list: TaskList) -> None:
    """Property 1：对于任意合法的 TaskList 实例，
    TaskList.from_dict(task_list.to_dict()) 应产生等价的 TaskList。

    **验证：需求 6.3, 6.1, 6.2, 6.4, 1.1, 1.2**
    """
    serialized = task_list.to_dict()
    restored = TaskList.from_dict(serialized)

    # 验证标题一致
    assert restored.title == task_list.title, (
        f"标题不一致: {restored.title!r} != {task_list.title!r}"
    )
    # 验证 items 数量一致
    assert len(restored.items) == len(task_list.items), (
        f"items 数量不一致: {len(restored.items)} != {len(task_list.items)}"
    )
    # 验证每个 TaskItem 的字段一致
    for i, (orig, rest) in enumerate(zip(task_list.items, restored.items)):
        assert rest.title == orig.title, f"items[{i}].title 不一致"
        assert rest.status == orig.status, f"items[{i}].status 不一致"
        assert rest.result == orig.result, f"items[{i}].result 不一致"
    # 验证时间戳一致（通过 isoformat 往返）
    assert restored.created_at == task_list.created_at, "created_at 不一致"


# ---------------------------------------------------------------------------
# Property 2: 新建 TaskList 所有项初始为 pending
# Feature: agent-task-list, Property 2: 新建 TaskList 所有项初始为 pending
# **验证：需求 1.4**
# ---------------------------------------------------------------------------

# 非空子任务标题列表
_subtask_titles_strategy = st.lists(_title_strategy, min_size=1, max_size=20)


@given(title=_title_strategy, subtask_titles=_subtask_titles_strategy)
def test_pbt_property_2_new_task_list_all_pending(
    title: str, subtask_titles: list[str]
) -> None:
    """Property 2：对于任意非空的子任务标题列表，
    通过 TaskStore.create() 创建的 TaskList 中，所有 TaskItem 的 status 均为 PENDING。

    **验证：需求 1.4**
    """
    store = TaskStore()
    task_list = store.create(title, subtask_titles)

    assert len(task_list.items) == len(subtask_titles), "items 数量与输入不一致"
    for i, item in enumerate(task_list.items):
        assert item.status == TaskStatus.PENDING, (
            f"items[{i}] 状态应为 PENDING，实际为 {item.status.value}"
        )


# ---------------------------------------------------------------------------
# Property 3: 状态转换合法性
# Feature: agent-task-list, Property 3: 状态转换合法性
# **验证：需求 1.5**
# ---------------------------------------------------------------------------

# 合法转换集合
_LEGAL_TRANSITIONS = {
    (TaskStatus.PENDING, TaskStatus.IN_PROGRESS),
    (TaskStatus.PENDING, TaskStatus.COMPLETED),
    (TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED),
    (TaskStatus.IN_PROGRESS, TaskStatus.FAILED),
}


@given(
    current_status=_status_strategy,
    target_status=_status_strategy,
)
def test_pbt_property_3_state_transition_validity(
    current_status: TaskStatus, target_status: TaskStatus
) -> None:
    """Property 3：对于任意 TaskItem 和任意目标状态，
    当且仅当 (当前状态, 目标状态) 属于合法转换集合时，transition() 调用成功；
    否则抛出 ValueError。

    **验证：需求 1.5**
    """
    item = TaskItem(title="测试任务", status=current_status)
    is_legal = (current_status, target_status) in _LEGAL_TRANSITIONS

    if is_legal:
        item.transition(target_status)
        assert item.status == target_status, (
            f"转换后状态应为 {target_status.value}，实际为 {item.status.value}"
        )
    else:
        with pytest.raises(ValueError):
            item.transition(target_status)


# ---------------------------------------------------------------------------
# Property 4: 进度摘要不变量
# Feature: agent-task-list, Property 4: 进度摘要不变量
# **验证：需求 1.6**
# ---------------------------------------------------------------------------


@given(task_list=_task_list_strategy)
def test_pbt_property_4_progress_summary_invariant(task_list: TaskList) -> None:
    """Property 4：对于任意 TaskList，progress_summary() 返回的各状态计数之和
    等于 len(items)，且每个状态的计数等于 items 中处于该状态的实际数量。

    **验证：需求 1.6**
    """
    summary = task_list.progress_summary()

    # 计数之和等于 items 总数
    total = sum(summary.values())
    assert total == len(task_list.items), (
        f"计数之和 {total} != items 数量 {len(task_list.items)}"
    )

    # 每个状态的计数等于实际数量
    for status in TaskStatus:
        expected = sum(1 for item in task_list.items if item.status == status)
        assert summary[status.value] == expected, (
            f"状态 {status.value} 计数 {summary[status.value]} != 实际 {expected}"
        )


# ---------------------------------------------------------------------------
# Property 5: task_create 工具产生有效 TaskList
# Feature: agent-task-list, Property 5: task_create 工具产生有效 TaskList
# **验证：需求 2.3**
# ---------------------------------------------------------------------------


@given(title=_title_strategy, subtask_titles=_subtask_titles_strategy)
def test_pbt_property_5_task_create_produces_valid_task_list(
    title: str, subtask_titles: list[str]
) -> None:
    """Property 5：对于任意标题字符串和非空子任务标题列表，
    调用 task_create() 后，TaskStore.current 不为 None，
    且其 title 与传入标题一致，items 数量与子任务列表长度一致。

    **验证：需求 2.3**
    """
    # 每次测试使用独立的 TaskStore
    store = TaskStore()
    task_tools.init_store(store)

    result = task_tools.task_create(title, subtask_titles)

    # TaskStore.current 不为 None
    assert store.current is not None, "task_create 后 TaskStore.current 不应为 None"
    # title 一致
    assert store.current.title == title, (
        f"title 不一致: {store.current.title!r} != {title!r}"
    )
    # items 数量一致
    assert len(store.current.items) == len(subtask_titles), (
        f"items 数量 {len(store.current.items)} != 子任务数量 {len(subtask_titles)}"
    )


# ---------------------------------------------------------------------------
# Property 6: 越界索引抛异常
# Feature: agent-task-list, Property 6: 越界索引抛异常
# **验证：需求 2.4**
# ---------------------------------------------------------------------------

# 生成越界索引：负数或 >= N
_oob_negative = st.integers(min_value=-1000, max_value=-1)
_oob_positive_offset = st.integers(min_value=0, max_value=1000)


@given(
    title=_title_strategy,
    subtask_titles=_subtask_titles_strategy,
    negative_index=_oob_negative,
    positive_offset=_oob_positive_offset,
)
def test_pbt_property_6_out_of_bounds_index_raises(
    title: str,
    subtask_titles: list[str],
    negative_index: int,
    positive_offset: int,
) -> None:
    """Property 6：对于任意包含 N 个子任务的 TaskList（N ≥ 1），
    调用 task_update() 时传入 index < 0 或 index ≥ N，
    应抛出 IndexError。

    **验证：需求 2.4**
    """
    store = TaskStore()
    task_tools.init_store(store)
    task_tools.task_create(title, subtask_titles)

    n = len(subtask_titles)
    oob_high = n + positive_offset  # >= N

    # 测试负数索引
    with pytest.raises(IndexError):
        task_tools.task_update(negative_index, "in_progress")

    # 测试越界正数索引
    with pytest.raises(IndexError):
        task_tools.task_update(oob_high, "in_progress")


# ---------------------------------------------------------------------------
# Property 8: ToolCallEvent 任务字段序列化完整性
# Feature: agent-task-list, Property 8: ToolCallEvent 任务字段序列化完整性
# **验证：需求 5.3, 3.4**
# ---------------------------------------------------------------------------

from excelmanus.events import ToolCallEvent, EventType

# ToolCallEvent 任务字段策略
_task_list_data_strategy = st.fixed_dictionaries({
    "title": _title_strategy,
    "items": st.lists(
        st.fixed_dictionaries({
            "title": _title_strategy,
            "status": st.sampled_from(["pending", "in_progress", "completed", "failed"]),
        }),
        min_size=0,
        max_size=10,
    ),
    "created_at": st.just("2025-01-15T10:30:00"),
    "progress": st.fixed_dictionaries({
        "pending": st.integers(min_value=0, max_value=10),
        "in_progress": st.integers(min_value=0, max_value=10),
        "completed": st.integers(min_value=0, max_value=10),
        "failed": st.integers(min_value=0, max_value=10),
    }),
})

_task_index_strategy = st.one_of(st.none(), st.integers(min_value=0, max_value=20))
_task_status_strategy = st.sampled_from(["", "pending", "in_progress", "completed", "failed"])
_task_result_strategy = st.one_of(st.none(), _title_strategy)


@given(
    task_list_data=_task_list_data_strategy,
    task_index=_task_index_strategy,
    task_status=_task_status_strategy,
    task_result=_task_result_strategy,
)
def test_pbt_property_8_tool_call_event_task_fields_serialization(
    task_list_data: dict,
    task_index: int | None,
    task_status: str,
    task_result: str | None,
) -> None:
    """Property 8：对于任意设置了 task_list_data 的 ToolCallEvent 实例，
    to_dict() 的返回字典应包含 task_list_data、task_index、task_status、task_result 字段。

    **验证：需求 5.3, 3.4**
    """
    event = ToolCallEvent(
        event_type=EventType.TASK_LIST_CREATED,
        task_list_data=task_list_data,
        task_index=task_index,
        task_status=task_status,
        task_result=task_result,
    )

    d = event.to_dict()

    # 验证四个任务字段均存在于序列化结果中
    assert "task_list_data" in d, "to_dict() 缺少 task_list_data 字段"
    assert "task_index" in d, "to_dict() 缺少 task_index 字段"
    assert "task_status" in d, "to_dict() 缺少 task_status 字段"
    assert "task_result" in d, "to_dict() 缺少 task_result 字段"

    # 验证字段值与原始输入一致
    assert d["task_list_data"] == task_list_data, "task_list_data 序列化值不一致"
    assert d["task_index"] == task_index, "task_index 序列化值不一致"
    assert d["task_status"] == task_status, "task_status 序列化值不一致"
    assert d["task_result"] == task_result, "task_result 序列化值不一致"


# ---------------------------------------------------------------------------
# Property 7: 渲染输出包含正确状态图标
# Feature: agent-task-list, Property 7: 渲染输出包含正确状态图标
# **验证：需求 4.1, 4.2, 4.3**
# ---------------------------------------------------------------------------

import io
from rich.console import Console
from excelmanus.renderer import StreamRenderer, _STATUS_SYMBOLS as _STATUS_ICONS

# 生成包含各种状态组合的 task_list_data 字典
_task_list_data_for_render = st.fixed_dictionaries({
    "title": _title_strategy,
    "items": st.lists(
        st.fixed_dictionaries({
            "title": _title_strategy,
            "status": st.sampled_from(["pending", "in_progress", "completed", "failed"]),
        }),
        min_size=1,
        max_size=15,
    ),
    "created_at": st.just("2025-01-15T10:30:00"),
    "progress": st.fixed_dictionaries({
        "pending": st.integers(min_value=0, max_value=10),
        "in_progress": st.integers(min_value=0, max_value=10),
        "completed": st.integers(min_value=0, max_value=10),
        "failed": st.integers(min_value=0, max_value=10),
    }),
})


@given(task_list_data=_task_list_data_for_render)
def test_pbt_property_7_render_output_contains_correct_status_icons(
    task_list_data: dict,
) -> None:
    """Property 7：对于任意 TaskList 数据（items 处于各种状态组合），
    StreamRenderer 渲染 TASK_LIST_CREATED 事件时，输出中每个 TaskItem
    对应的行应包含与其状态匹配的图标（pending→⬜, in_progress→🔄, completed→✅, failed→❌）。

    **验证：需求 4.1, 4.2, 4.3**
    """
    # 使用 StringIO 捕获渲染输出（宽终端，避免窄终端紧凑格式干扰）
    buf = io.StringIO()
    console = Console(file=buf, width=120, force_terminal=True, no_color=True)
    renderer = StreamRenderer(console)

    event = ToolCallEvent(
        event_type=EventType.TASK_LIST_CREATED,
        task_list_data=task_list_data,
    )
    renderer.handle_event(event)

    output = buf.getvalue()

    # 验证每个 TaskItem 对应的状态图标出现在输出中
    for i, item in enumerate(task_list_data["items"]):
        expected_icon = _STATUS_ICONS[item["status"]]
        # 渲染格式为 "     {icon} {i}. {title}"（宽终端）
        # 检查输出中包含该图标
        assert expected_icon in output, (
            f"items[{i}] 状态 {item['status']} 对应图标 {expected_icon} "
            f"未出现在渲染输出中。\n输出:\n{output}"
        )
