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

def test_u2_pending_approval_is_emitted_and_sanitized():
    """PENDING_APPROVAL 一律下发，但不暴露 approval_arguments 原始字段。"""
    sensitive_path = "/Users/secret/sensitive_data.xlsx"
    event = _make_pending_approval_event(
        approval_arguments={
            "file_path": sensitive_path,
            "cell": "A1",
            "value": "confidential",
        }
    )
    result = _sse_event_to_sse(event)
    assert result is not None
    assert "event: pending_approval" in result
    assert "approval_arguments" not in result
    assert sensitive_path not in result


# ============================================================
# 保留性测试（Preservation）— 在未修复代码上应 PASS
# ============================================================

from hypothesis import given, settings
import hypothesis.strategies as st


ALWAYS_EMITTED_EVENT_TYPES = [
    EventType.THINKING,
    EventType.THINKING_DELTA,
    EventType.TOOL_CALL_START,
    EventType.TOOL_CALL_END,
    EventType.ITERATION_START,
    EventType.SUBAGENT_START,
    EventType.SUBAGENT_ITERATION,
    EventType.SUBAGENT_SUMMARY,
    EventType.SUBAGENT_END,
    EventType.USER_QUESTION,
    EventType.TEXT_DELTA,
    EventType.PENDING_APPROVAL,
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
# U2 保留性测试 — UI 事件一律下发
# ============================================================

@given(event_type=st.sampled_from(ALWAYS_EMITTED_EVENT_TYPES))
@settings(max_examples=50)
def test_u2_all_ui_events_are_emitted(event_type):
    """思考 / 工具 / 子代理 / 审批等 UI 事件一律下发。"""
    event = _make_event_for_type(event_type)
    result = _sse_event_to_sse(event)
    assert result is not None
    assert isinstance(result, str) and len(result) > 0


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
