"""模型空闲时长细分 — 按停止原因独占记账。

两次模型请求之间的墙钟空闲（``_model_idle_seconds``）按等待原因细分：
审批、用户问题、同步委派、工具执行、取消排空各用 ``idle_segment``
包住阻塞点；段可嵌套，子段时长从父段扣除，保证各 reason 合计
不超过真实墙钟。每个响应-请求间隔在记录 ``_last_model_response_at``
处由 ``reset_idle_tracker`` 清零，只统计本间隔内的段。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


def _tracker_of(engine: Any) -> dict[str, Any]:
    """取 engine 上的空闲记账表，缺失或形态异常时懒创建（tracker 永远可用）。"""
    tracker = getattr(engine, "_idle_tracker", None) if engine is not None else None
    if (
        isinstance(tracker, dict)
        and isinstance(tracker.get("totals"), dict)
        and isinstance(tracker.get("stack"), list)
    ):
        return tracker
    tracker = {"totals": {}, "stack": []}
    if engine is not None:
        try:
            engine._idle_tracker = tracker
        except Exception:
            pass  # 无法挂载时退回一次性 tracker，段语义仍然成立。
    return tracker


@contextmanager
def idle_segment(engine: Any, reason: str) -> Iterator[None]:
    """记录一段按 reason 归属的独占等待时长。

    进入时压栈 ``[reason, t0, child_elapsed]``；退出时把
    ``elapsed - child_elapsed``（下限 0）计入 ``totals[reason]``，
    并把整段 elapsed 加到父段的 child_elapsed。并行任务交错进出栈时
    按退出瞬间栈中相邻位置认定父子，让先退出的兄弟段抵扣到仍在
    运行的兄弟上，保证 totals 合计不超墙钟。异常（含
    ``asyncio.CancelledError``）原样传播，finally 只做记账。
    """
    tracker = _tracker_of(engine)
    stack = tracker["stack"]
    entry = [reason, time.monotonic(), 0.0]
    stack.append(entry)
    try:
        yield
    finally:
        elapsed = max(0.0, time.monotonic() - entry[1])
        index = next((i for i, item in enumerate(stack) if item is entry), None)
        parent = None
        if index is not None:
            parent = stack[index - 1] if index > 0 else None
            del stack[index]
        own = max(0.0, elapsed - entry[2])
        totals = tracker["totals"]
        totals[reason] = totals.get(reason, 0.0) + own
        if parent is not None:
            parent[2] += elapsed


def reset_idle_tracker(engine: Any) -> None:
    """清空记账表：每个响应-请求间隔只统计本间隔内的段。"""
    tracker = _tracker_of(engine)
    tracker["totals"].clear()
    tracker["stack"].clear()
