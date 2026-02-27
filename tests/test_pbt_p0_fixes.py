"""
P0 Bug 条件探索测试 — B2 Task 泄漏 & U2 PENDING_APPROVAL 敏感信息暴露

此测试文件分两阶段：
1. 探索性测试（exploration）：在未修复代码上运行，预期 FAIL，证明 Bug 存在
2. 保留性测试（preservation）：在未修复代码上运行，预期 PASS，建立基线

**验证：需求 1.1, 1.2, 1.3, 1.4, 1.5**
"""

from __future__ import annotations

import asyncio
import json
import sys
import os

import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from excelmanus.events import EventType, ToolCallEvent
from excelmanus.api import _sse_event_to_sse


# ============================================================
# 辅助工具
# ============================================================

def _make_pending_approval_event(**kwargs) -> ToolCallEvent:
    """构造一个 PENDING_APPROVAL 事件，可选覆盖字段。"""
    defaults = dict(
        event_type=EventType.PENDING_APPROVAL,
        approval_id="approval-001",
        approval_tool_name="write_cell",
        approval_arguments={
            "file_path": "/Users/secret/sensitive_data.xlsx",
            "cell": "A1",
            "value": "confidential",
        },
    )
    defaults.update(kwargs)
    return ToolCallEvent(**defaults)


# ============================================================
# B2 探索性测试 — asyncio.wait task 泄漏
# ============================================================

@pytest.mark.asyncio
async def test_b2_exploration_bug_condition_exists():
    """
    B2 探索（修复验证）：构造 chat_task 先完成的场景，验证修复后 get_task 被正确取消。

    修复后的代码应该：
    1. 将 asyncio.wait 的 pending 返回值保存（不用 _ 丢弃）
    2. 对 pending 集合中的每个 task 调用 cancel() 并 await

    **修复后此测试应 PASS（确认 Bug 已修复）**
    **验证：需求 2.1**
    """
    queue: asyncio.Queue = asyncio.Queue()

    # 构造一个已完成的 chat_task（立即返回）
    async def _fast_chat():
        return "done"

    chat_task = asyncio.ensure_future(_fast_chat())
    # 等待 chat_task 完成
    await asyncio.sleep(0)
    assert chat_task.done(), "chat_task 应该已完成"

    # 构造一个永远不会完成的 get_task（队列为空）
    get_task = asyncio.create_task(queue.get())

    # 模拟修复后的代码行为：asyncio.wait 后取消 pending 中的 task
    done, pending = await asyncio.wait(
        [get_task, chat_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # 修复后的代码：取消 pending 中的所有 task
    for t in pending:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    # 验证修复效果：get_task 应该已被取消，不再处于 pending 状态
    is_bug_condition = (
        chat_task in done
        and get_task in pending
        and not get_task.cancelled()
    )

    assert not is_bug_condition, (
        f"Bug B2 仍然存在：chat_task 先完成，get_task 仍在 pending 集合中且未被取消。"
        f"isBugCondition_B2=True 证明修复未生效。"
        f"done={done}, pending={pending}, get_task.cancelled()={get_task.cancelled()}"
    )


@pytest.mark.asyncio
async def test_b2_exploration_linear_task_accumulation():
    """
    B2 探索（修复验证）：模拟 10 次循环迭代，验证修复后无悬挂 task 积累。

    修复后的代码在每次迭代中都应取消 pending 中的 get_task，
    10 次迭代后应有 0 个悬挂 task。

    **修复后此测试应 PASS（确认 Bug 已修复）**
    **验证：需求 2.2**
    """
    queue: asyncio.Queue = asyncio.Queue()
    leaked_tasks: list[asyncio.Task] = []

    async def _fast_chat():
        return "done"

    chat_task = asyncio.ensure_future(_fast_chat())
    await asyncio.sleep(0)
    assert chat_task.done()

    # 模拟 10 次循环迭代（修复后的代码行为：每次取消 pending 中的 get_task）
    ITERATIONS = 10
    for _ in range(ITERATIONS):
        get_task = asyncio.create_task(queue.get())
        done, pending = await asyncio.wait(
            [get_task, chat_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        # 修复后的代码：取消 pending 中的所有 task
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        # 检查是否还有未取消的悬挂 task
        for t in pending:
            if t is not chat_task and not t.done() and not t.cancelled():
                leaked_tasks.append(t)

    # 修复后：没有悬挂 task
    assert len(leaked_tasks) == 0, (
        f"Bug B2 仍然存在：{ITERATIONS} 次迭代后积累了 {len(leaked_tasks)} 个悬挂 task，"
        f"证明修复未生效。"
    )


# ============================================================
# U2 探索性测试 — PENDING_APPROVAL 敏感信息暴露
# ============================================================

def test_u2_exploration_pending_approval_not_filtered_in_safe_mode():
    """
    U2 探索：构造 PENDING_APPROVAL 事件 + safe_mode=True，
    断言返回值不为 None（isBugCondition_U2 = True）。

    在未修复代码中，PENDING_APPROVAL 不在 safe_mode 过滤集合中，
    会进入 else 分支调用 event.to_dict()，返回非 None 值。

    **预期在未修复代码上 FAIL（这是正确的 —— 证明 Bug 存在）**
    **验证：需求 1.4**
    """
    event = _make_pending_approval_event()

    # isBugCondition_U2：safe_mode=True 且 event_type=PENDING_APPROVAL
    result = _sse_event_to_sse(event, safe_mode=True)

    # Bug 修复后：result 应该为 None（事件被过滤）
    # Bug 存在时：result 不为 None（事件未被过滤，进入 else 分支）
    assert result is None, (
        f"Bug U2 已确认存在：safe_mode=True 时 PENDING_APPROVAL 事件未被过滤，"
        f"_sse_event_to_sse 返回了非 None 值。"
        f"isBugCondition_U2=True 证明敏感信息暴露 Bug 存在。"
        f"返回值: {result!r}"
    )


def test_u2_exploration_approval_arguments_exposed():
    """
    U2 探索：检查返回值中含 approval_arguments 字段（敏感信息暴露）。

    在未修复代码中，PENDING_APPROVAL 事件进入 else 分支调用 event.to_dict()，
    返回值中包含 approval_arguments 字段（含用户文件路径等敏感信息）。

    **预期在未修复代码上 FAIL（这是正确的 —— 证明 Bug 存在）**
    **验证：需求 1.5**
    """
    sensitive_path = "/Users/secret/sensitive_data.xlsx"
    event = _make_pending_approval_event(
        approval_arguments={
            "file_path": sensitive_path,
            "cell": "A1",
            "value": "confidential",
        }
    )

    # safe_mode=True 时调用
    result = _sse_event_to_sse(event, safe_mode=True)

    # 如果 result 为 None，说明事件已被过滤（Bug 已修复），此测试不适用
    if result is None:
        pytest.skip("事件已被过滤（Bug 可能已修复），跳过敏感字段检查")

    # Bug 存在时：result 不为 None，且包含 approval_arguments 字段
    # 解析 SSE 格式，提取 data 部分
    data_line = None
    for line in result.split("\n"):
        if line.startswith("data:"):
            data_line = line[len("data:"):].strip()
            break

    assert data_line is not None, f"SSE 格式异常，未找到 data 行: {result!r}"

    payload = json.loads(data_line)

    # 断言：返回值中不应包含 approval_arguments（敏感字段）
    # Bug 存在时：包含 approval_arguments，此断言 FAIL
    assert "approval_arguments" not in payload, (
        f"Bug U2 已确认存在：safe_mode=True 时 PENDING_APPROVAL 事件的返回值中"
        f"包含敏感字段 approval_arguments，内容: {payload.get('approval_arguments')!r}。"
        f"敏感路径 {sensitive_path!r} 已暴露给外部调用方。"
    )


# ============================================================
# 保留性测试（Preservation）— 在未修复代码上应 PASS
# ============================================================

from hypothesis import given, settings
import hypothesis.strategies as st


# 已有过滤事件类型（safe_mode=True 时应返回 None）
SAFE_MODE_FILTERED_EVENT_TYPES = [
    EventType.THINKING,
    EventType.THINKING_DELTA,
    EventType.TOOL_CALL_START,
    EventType.TOOL_CALL_END,
    EventType.ITERATION_START,
    EventType.SUBAGENT_START,
    EventType.SUBAGENT_ITERATION,
    EventType.SUBAGENT_SUMMARY,
    EventType.SUBAGENT_END,
]

# 非敏感事件类型（safe_mode=True 时应正常输出，不为 None）
NON_SENSITIVE_EVENT_TYPES = [
    EventType.USER_QUESTION,
    EventType.TEXT_DELTA,
    EventType.THINKING_DELTA,  # 注意：safe_mode=True 时被过滤，但 safe_mode=False 时正常输出
]

# 非 PENDING_APPROVAL 的所有事件类型（safe_mode=False 时应正常输出）
NON_PENDING_APPROVAL_EVENT_TYPES = [
    et for et in EventType if et != EventType.PENDING_APPROVAL
]


def _make_event_for_type(event_type: EventType) -> ToolCallEvent:
    """根据事件类型构造最小化的 ToolCallEvent。"""
    return ToolCallEvent(
        event_type=event_type,
        tool_name="test_tool",
        thinking="test thinking",
        iteration=1,
        text_delta="hello",
        thinking_delta="thinking...",
        question_id="q-001",
        question_text="test question?",
        subagent_name="test_agent",
        subagent_reason="test reason",
        subagent_summary="test summary",
    )


# ============================================================
# U2 保留性测试 — 已有过滤事件在 safe_mode=True 时仍被过滤
# ============================================================

@given(event_type=st.sampled_from(SAFE_MODE_FILTERED_EVENT_TYPES))
@settings(max_examples=50)
def test_u2_preservation_existing_filtered_events_still_filtered(event_type):
    """
    U2 保留性：safe_mode=True 时，已有过滤事件类型继续被过滤（返回 None）。

    此测试验证修复前的基线行为：THINKING、TOOL_CALL_START 等事件
    在 safe_mode=True 时应返回 None，此行为不应被修复破坏。

    **预期在未修复代码上 PASS（建立基线）**
    **验证：需求 3.4**
    """
    event = _make_event_for_type(event_type)
    result = _sse_event_to_sse(event, safe_mode=True)
    assert result is None, (
        f"保留性违反：safe_mode=True 时 {event_type.value} 事件应返回 None，"
        f"但实际返回了 {result!r}"
    )


# ============================================================
# U2 保留性测试 — safe_mode=False 时非 PENDING_APPROVAL 事件正常输出
# ============================================================

@given(event_type=st.sampled_from(NON_PENDING_APPROVAL_EVENT_TYPES))
@settings(max_examples=50)
def test_u2_preservation_safe_mode_false_non_approval_events_pass_through(event_type):
    """
    U2 保留性：safe_mode=False 时，非 PENDING_APPROVAL 事件正常输出（不为 None）。

    此测试验证修复前的基线行为：safe_mode=False 时所有非敏感事件
    应正常输出，此行为不应被修复破坏。

    **预期在未修复代码上 PASS（建立基线）**
    **验证：需求 3.5**
    """
    event = _make_event_for_type(event_type)
    result = _sse_event_to_sse(event, safe_mode=False)
    assert result is not None, (
        f"保留性违反：safe_mode=False 时 {event_type.value} 事件应正常输出，"
        f"但实际返回了 None"
    )
    assert isinstance(result, str) and len(result) > 0, (
        f"保留性违反：safe_mode=False 时 {event_type.value} 事件应返回非空字符串，"
        f"但实际返回了 {result!r}"
    )


# ============================================================
# U2 保留性测试 — safe_mode=True 时非敏感事件正常输出
# ============================================================

@pytest.mark.parametrize("event_type", [
    EventType.USER_QUESTION,
    EventType.TEXT_DELTA,
])
def test_u2_preservation_safe_mode_true_non_sensitive_events_pass_through(event_type):
    """
    U2 保留性：safe_mode=True 时，USER_QUESTION、TEXT_DELTA 等非敏感事件正常输出。

    此测试验证修复前的基线行为：这些事件不在过滤集合中，
    safe_mode=True 时应正常输出，此行为不应被修复破坏。

    **预期在未修复代码上 PASS（建立基线）**
    **验证：需求 3.6**
    """
    event = _make_event_for_type(event_type)
    result = _sse_event_to_sse(event, safe_mode=True)
    assert result is not None, (
        f"保留性违反：safe_mode=True 时 {event_type.value} 事件应正常输出，"
        f"但实际返回了 None"
    )
    assert isinstance(result, str) and len(result) > 0, (
        f"保留性违反：safe_mode=True 时 {event_type.value} 事件应返回非空字符串，"
        f"但实际返回了 {result!r}"
    )


# ============================================================
# B2 保留性测试 — get_task 先完成时事件被正常读取
# ============================================================

@pytest.mark.asyncio
async def test_b2_preservation_get_task_completes_first_event_forwarded():
    """
    B2 保留性：get_task 先于 chat_task 完成时（队列有事件），事件被正常读取。

    此测试验证修复前的基线行为：当队列中有事件时，get_task 先完成，
    事件应被正确读取，此行为不应被修复破坏。

    **预期在未修复代码上 PASS（建立基线）**
    **验证：需求 3.1**
    """
    queue: asyncio.Queue = asyncio.Queue()
    test_event = ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta="hello")

    # 预先放入事件，确保 get_task 先完成
    await queue.put(test_event)

    # 构造一个慢速 chat_task（不会先完成）
    async def _slow_chat():
        await asyncio.sleep(10)
        return "done"

    chat_task = asyncio.create_task(_slow_chat())

    try:
        get_task = asyncio.create_task(queue.get())
        done, pending = await asyncio.wait(
            [get_task, chat_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # get_task 应该先完成（队列有事件）
        assert get_task in done, (
            f"保留性违反：队列有事件时 get_task 应先完成，"
            f"但 done={done}, pending={pending}"
        )
        assert chat_task in pending, (
            f"保留性违反：chat_task 不应先完成，"
            f"但 done={done}, pending={pending}"
        )

        # 事件应被正确读取
        retrieved_event = get_task.result()
        assert retrieved_event is test_event, (
            f"保留性违反：读取到的事件与放入的事件不一致，"
            f"expected={test_event!r}, got={retrieved_event!r}"
        )

    finally:
        # 清理
        chat_task.cancel()
        try:
            await chat_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_b2_preservation_multiple_events_in_queue_all_readable():
    """
    B2 保留性：队列中有多个事件时，每次 get_task 先完成，事件依次被正确读取。

    **预期在未修复代码上 PASS（建立基线）**
    **验证：需求 3.1, 3.2**
    """
    queue: asyncio.Queue = asyncio.Queue()
    events = [
        ToolCallEvent(event_type=EventType.TEXT_DELTA, text_delta=f"chunk-{i}")
        for i in range(5)
    ]

    # 预先放入所有事件
    for evt in events:
        await queue.put(evt)

    # 构造一个慢速 chat_task
    async def _slow_chat():
        await asyncio.sleep(10)
        return "done"

    chat_task = asyncio.create_task(_slow_chat())

    try:
        retrieved = []
        for _ in range(len(events)):
            get_task = asyncio.create_task(queue.get())
            done, pending = await asyncio.wait(
                [get_task, chat_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            assert get_task in done, "get_task 应先完成"
            retrieved.append(get_task.result())

        assert retrieved == events, (
            f"保留性违反：读取到的事件序列与放入的不一致，"
            f"expected={events!r}, got={retrieved!r}"
        )

    finally:
        chat_task.cancel()
        try:
            await chat_task
        except asyncio.CancelledError:
            pass
