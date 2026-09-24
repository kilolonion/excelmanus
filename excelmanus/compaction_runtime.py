"""Observable compaction jobs, executed only at a session's safe step boundary."""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from typing import Any
from uuid import uuid4

from excelmanus.events import EventType, ToolCallEvent
from excelmanus.logger import get_logger

logger = get_logger("compaction.runtime")
ACTIVE = {"queued", "running"}


def operations(engine: Any) -> list[dict[str, Any]]:
    owner = getattr(engine, "_state", None) or engine._compaction_manager
    if not isinstance(getattr(owner, "compaction_operations", None), list):
        owner.compaction_operations = []
    return owner.compaction_operations


def publish(engine: Any, operation: dict[str, Any], *, on_event: Any = None, **patch: Any) -> None:
    operation.update(patch, updated_at=time.time())
    records = operations(engine)
    if not any(row["operation_id"] == operation["operation_id"] for row in records):
        records.append(operation)
        del records[:-20]
    emit = getattr(engine, "_emit", None)
    if callable(emit):
        callback = on_event or getattr(getattr(engine, "_driver", None), "_on_event", None)
        emit(callback, ToolCallEvent(event_type=EventType.COMPACTION, compaction=deepcopy(operation)))
    save = getattr(engine, "save_session_snapshot", None)
    if callable(save):
        save()


def new_operation(source: str, operation_id: str | None = None) -> dict[str, Any]:
    return {"operation_id": operation_id or uuid4().hex, "source": source,
            "status": "queued", "created_at": time.time(), "updated_at": time.time()}


def finish(engine: Any, operation: dict[str, Any], result: Any, *, on_event: Any = None) -> None:
    success = bool(result.success)
    error = str(result.error or "")
    skipped = not success and any(s in error for s in ("无需", "没有可压缩", "无早期", "轮次不足", "未小于"))
    handoff = result.handoff or {}
    publish(engine, operation, on_event=on_event, status="completed" if success else "skipped" if skipped else "failed",
            message="已压缩历史对话" if success else error or "压缩未执行",
            tokens_before=result.tokens_before, tokens_after=result.tokens_after,
            messages_before=result.messages_before, messages_after=result.messages_after,
            preserved_quotes=len(handoff.get("verbatim", [])),
            handoff_id=handoff.get("handoff_id", ""),
            can_resume=bool(getattr(getattr(engine, "_driver", None), "current_turn", lambda: {})().get("can_resume")))


def request_manual(engine: Any, *, instruction: str | None = None,
                   operation_id: str | None = None, on_event: Any = None) -> dict[str, Any]:
    """Idempotent acknowledgment. The UI need not hold a long HTTP request open."""
    manager = engine._compaction_manager
    for row in reversed(operations(engine)):
        if row["operation_id"] == operation_id or row["status"] in ACTIVE:
            return deepcopy(row)
    operation = new_operation("manual", operation_id)
    manager._pending_manual = {"operation": operation, "instruction": instruction, "on_event": on_event}
    running = bool(getattr(getattr(engine, "_driver", None), "running", False))
    publish(engine, operation, on_event=on_event,
            message="已排队，将在当前步骤结束后压缩并继续任务。" if running else "准备压缩历史对话。")

    async def drain_when_idle() -> None:
        # pre_step normally consumes it. This waiter also handles final answers,
        # stopped turns and tasks waiting for user input with no next model step.
        runner = getattr(getattr(engine, "_driver", None), "_runner_task", None)
        if runner is not None and not runner.done():
            try:
                await asyncio.shield(runner)
            except (Exception, asyncio.CancelledError):
                pass
        try:
            await run_pending(engine)
        except Exception:
            logger.exception("manual compaction worker failed")

    manager._manual_task = asyncio.create_task(drain_when_idle())
    return deepcopy(operation)


async def run_pending(engine: Any) -> bool:
    manager = engine._compaction_manager
    pending = manager._pending_manual
    if pending is None:
        # An idle manual compaction may already own memory when a new user turn
        # arrives. Wait before that turn appends inputs or sends an LLM request.
        if manager._lock.locked():
            async with manager._lock:
                pass
            return True
        return False
    manager._pending_manual = None
    operation = pending["operation"]
    callback = pending.get("on_event")
    from excelmanus.compaction import capture_progress, sync_compaction_boundary
    from excelmanus.prompt.envelope import compaction_wire_context

    system, tools = compaction_wire_context(engine)
    system = system or engine.memory.build_system_messages()
    publish(engine, operation, on_event=callback, status="running", message="正在压缩历史对话")
    try:
        result = await manager.manual_compact(
            engine.memory, system, client=engine._client,
            summary_model=engine.active_model, tools=tools,
            custom_instruction=pending.get("instruction"),
            vision_capable=bool(getattr(engine, "_is_vision_capable", True)),
            progress_provider=lambda: capture_progress(engine),
        )
        if result.success:
            engine.record_compaction_handoff(result.handoff)
            sync_compaction_boundary(engine)
        finish(engine, operation, result, on_event=callback)
    except asyncio.CancelledError:
        publish(engine, operation, on_event=callback, status="failed", message="压缩已中断，可重试。")
        raise
    except Exception:
        logger.exception("manual compaction failed")
        publish(engine, operation, on_event=callback, status="failed", message="压缩失败，可重试。")
    return True
