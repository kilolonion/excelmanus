"""ToolRuntime — 工具执行流水线（阶段 1–9）。

包住现有 ToolDispatcher / workbook_commit，不重写 openpyxl。
直接工具与 ``run_code`` 共用执行策略与权限边界。
"""

from __future__ import annotations

import copy
import json
import asyncio
import os
import signal
import subprocess
import threading
import uuid
import time
from contextvars import ContextVar
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any, Literal

from excelmanus.engine_core.error_payload import PRE_EXECUTE_DENIED, SDK_CONTRACT_VIOLATION
from excelmanus.engine_core.idle_tracker import idle_segment
from excelmanus.engine_core.tool_result import (
    ToolResult,
    annotate_shadow_schema_violations,
    coerce_legacy_result,
    error_result,
    finalize_content,
)
from excelmanus.engine_types import ToolCallResult, _ToolCallBatch
from excelmanus.logger import get_logger
from excelmanus.tools.policy import is_concurrency_safe as policy_is_concurrency_safe

logger = get_logger("tools.runtime")


# 子进程由宿主持有，而不是由不可终止的线程池线程持有。run_code/run_shell
# 在启动 Popen 后注册到这里；取消时按 execution_id 杀掉整棵进程树，线程只
# 负责收集已终止的 stdout/stderr，不会继续向 workspace 发布 pending 写入。
_PROCESS_LOCK = threading.RLock()
_ACTIVE_PROCESSES: dict[str, set[subprocess.Popen[Any]]] = {}
_WINDOWS_JOB_HANDLES: dict[int, int] = {}
# ``Popen.poll()`` reaps the leader.  If the leader has already exited while a
# descendant is still alive, looking up ``getpgid(pid)`` afterwards fails and
# the descendant would escape cancellation.  Keep the process-group identity
# from registration time for the lifetime of the handle.
_PROCESS_GROUPS: dict[int, int] = {}


def _attach_windows_job(process: subprocess.Popen[Any]) -> None:
    """Attach a child to a kill-on-close Job Object when running on Windows."""
    if (
        os.name != "nt"
        or not getattr(process, "pid", None)
        or os.environ.get("EXCELMANUS_WINDOWS_JOB_OBJECT", "0").strip().lower()
        not in {"1", "true", "yes", "on"}
    ):
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            return
        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION starts with a basic limit
        # structure (40 bytes on Windows); the final LimitFlags field is at
        # offset 32.  KILL_ON_JOB_CLOSE = 0x2000.
        class _Info(ctypes.Structure):
            _fields_ = [("padding", ctypes.c_byte * 32), ("limit_flags", wintypes.DWORD), ("rest", ctypes.c_byte * 144)]

        info = _Info()
        info.limit_flags = 0x2000
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel.CloseHandle(job)
            return
        handle = kernel.OpenProcess(0x0200 | 0x0800 | 0x0400, False, int(process.pid))
        if not handle or not kernel.AssignProcessToJobObject(job, handle):
            if handle:
                kernel.CloseHandle(handle)
            kernel.CloseHandle(job)
            return
        kernel.CloseHandle(handle)
        with _PROCESS_LOCK:
            _WINDOWS_JOB_HANDLES[id(process)] = int(job)
    except Exception:
        logger.debug("Windows Job Object attach failed", exc_info=True)


def _close_windows_job(process: subprocess.Popen[Any]) -> None:
    handle = None
    with _PROCESS_LOCK:
        handle = _WINDOWS_JOB_HANDLES.pop(id(process), None)
    if handle and os.name == "nt":
        try:
            import ctypes
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
        except Exception:
            pass


def register_killable_process(execution_id: str | None, process: subprocess.Popen[Any]) -> None:
    pid = int(getattr(process, "pid", 0) or 0)
    pgid: int | None = None
    if os.name != "nt" and pid:
        try:
            candidate = int(os.getpgid(pid))
            # Never record the host's own process group.  A caller that did
            # not request ``start_new_session`` is still safely terminated as
            # an individual process.
            if candidate > 1 and candidate != os.getpgrp():
                pgid = candidate
        except (OSError, ProcessLookupError):
            pgid = None
    with _PROCESS_LOCK:
        if pgid is not None:
            _PROCESS_GROUPS[id(process)] = pgid
        if execution_id:
            _ACTIVE_PROCESSES.setdefault(str(execution_id), set()).add(process)
    _attach_windows_job(process)


def unregister_killable_process(execution_id: str | None, process: subprocess.Popen[Any]) -> None:
    with _PROCESS_LOCK:
        _PROCESS_GROUPS.pop(id(process), None)
    if not execution_id:
        _close_windows_job(process)
        return
    with _PROCESS_LOCK:
        active = _ACTIVE_PROCESSES.get(str(execution_id))
        if not active:
            return
        active.discard(process)
        _close_windows_job(process)
        if not active:
            _ACTIVE_PROCESSES.pop(str(execution_id), None)


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    """终止单个子进程及其新会话中的后代（best effort）。"""
    pid = int(getattr(process, "pid", 0) or 0)
    with _PROCESS_LOCK:
        target_pgid = _PROCESS_GROUPS.get(id(process))
    try:
        with _PROCESS_LOCK:
            job_handle = _WINDOWS_JOB_HANDLES.get(id(process))
        if job_handle and os.name == "nt":
            import ctypes
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(job_handle)
            with _PROCESS_LOCK:
                _WINDOWS_JOB_HANDLES.pop(id(process), None)
            process.wait(timeout=3)
            return
        if os.name == "nt" and pid:
            # CREATE_NEW_PROCESS_GROUP / shell wrapper 下，/T 才能避免孙进程
            # 留在后台继续写文件。taskkill 失败时回退到直接 kill。
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=3,
            )
        elif pid:
            try:
                if target_pgid is None:
                    candidate = int(os.getpgid(pid))
                    if candidate > 1 and candidate != os.getpgrp():
                        target_pgid = candidate
                if target_pgid is not None:
                    os.killpg(target_pgid, signal.SIGTERM)
                elif process.poll() is None:
                    process.terminate()
            except (ProcessLookupError, PermissionError, OSError):
                if process.poll() is None:
                    process.terminate()
        else:
            if process.poll() is None:
                process.terminate()
    except (OSError, subprocess.SubprocessError):
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
    # Do not return merely because the leader exited: a child may still hold
    # the recorded group alive.  Give SIGTERM a short grace period, then
    # enforce the hard deadline with SIGKILL for the whole group.
    deadline = time.monotonic() + 1.5
    while process.poll() is None and time.monotonic() < deadline:
        try:
            process.wait(timeout=max(0.05, deadline - time.monotonic()))
        except (subprocess.TimeoutExpired, OSError):
            break
    group_alive = False
    if os.name != "nt" and target_pgid is not None:
        try:
            os.killpg(target_pgid, 0)
            group_alive = True
        except (ProcessLookupError, PermissionError, OSError):
            group_alive = False
    if group_alive:
        try:
            os.killpg(target_pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    elif process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=1.5)
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("进程终止未在期限内完成: pid=%s", pid)


def terminate_killable_processes(execution_id: str | None) -> int:
    """终止 execution 下登记的所有子进程，返回尝试终止的数量。"""
    if not execution_id:
        return 0
    with _PROCESS_LOCK:
        processes = list(_ACTIVE_PROCESSES.get(str(execution_id), ()))
    for process in processes:
        _terminate_process_tree(process)
    return len(processes)


def terminate_killable_processes_async(execution_id: str | None) -> None:
    """在后台线程终止进程树，不阻塞取消请求所在的 event loop。"""
    if not execution_id:
        return
    threading.Thread(
        target=terminate_killable_processes,
        args=(execution_id,),
        name=f"excelmanus-kill-{str(execution_id)[:8]}",
        daemon=True,
    ).start()


@dataclass
class _ToolExecution:
    runtime: Any
    execution_id: str
    call_id: str
    name: str
    arguments: dict[str, Any]
    parent_call_id: str
    parent_execution_id: str
    on_event: Any
    iteration: int
    status: str = "queued"
    cancel_requested: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
    task: Any = None
    start_emitted: bool = False
    publishing: bool = False
    execution_started: bool = False
    outcome: ToolResult | None = None
    queued_result: ToolCallResult | None = None
    approval_id: str = ""
    approval_resolved: bool = False
    retained_by_batch: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {"execution_id": self.execution_id, "tool_call_id": self.call_id,
                "tool_name": self.name, "parent_execution_id": self.parent_execution_id,
                "status": self.status, "cancel_requested": self.cancel_requested}


_current_execution: ContextVar[_ToolExecution | None] = ContextVar("tool_execution", default=None)


def current_execution() -> _ToolExecution | None:
    return _current_execution.get()

PreExecuteDecision = Literal["allow", "deny", "ask"]

PreExecuteHook = Callable[["ExecutionToken"], PreExecuteDecision]
GuardHook = Callable[["ExecutionToken", PreExecuteDecision], PreExecuteDecision]
PostExecuteHook = Callable[["ExecutionToken", ToolCallResult], ToolCallResult | None]


@dataclass(frozen=True)
class ExecutionToken:
    """一次调用的冻结身份。Code Mode 子调用带 parent。"""

    call_id: str
    name: str
    arguments: dict[str, Any]
    parent: str | None = None


@dataclass
class ToolExecutionPlan:
    """Per-model-batch data; ordering barriers do not imply success dependencies."""

    batches: list[_ToolCallBatch]
    dependencies: dict[int, list[Any]]
    errors: dict[int, tuple[str, str]]

    def blocked_result(self, tc: Any, results: dict[int, ToolCallResult]) -> ToolCallResult | None:
        failure = self.errors.get(id(tc))
        failed_ids = []
        if failure is None:
            for dependency in self.dependencies.get(id(tc), []):
                result = results.get(id(dependency))
                if (result is None or not result.success or result.pending_approval
                        or result.pending_question or result.defer_tool_result
                        or (result.structured is not None and not result.structured.success)):
                    failed_ids.append(dependency.id)
            if failed_ids:
                failure = ("DEPENDENCY_FAILED", "前置工具未成功完成，本调用未执行。")
        if failure is None:
            return None
        code, message = failure
        value = error_result(message, code=code, fields={"executed": False, "dependencies": failed_ids})
        raw = tc.function.arguments
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            arguments = {}
        return ToolCallResult(tool_name=tc.function.name, arguments=arguments if isinstance(arguments, dict) else {},
                              result=value.model_text, success=False, error=code, structured=value)


def schema_tool_name(schema: dict[str, Any]) -> str:
    func = schema.get("function")
    if isinstance(func, dict) and func.get("name"):
        return str(func["name"])
    return str(schema.get("name") or "")


class ToolRuntime:
    """固定阶段的执行入口。阶段 6 仍委托现有 dispatcher。"""

    def __init__(self, dispatcher: Any, engine: Any | None = None) -> None:
        self.dispatcher = dispatcher
        self.engine = engine if engine is not None else getattr(dispatcher, "_engine", None)
        self._pre_hooks: list[PreExecuteHook] = []
        self._guards: list[GuardHook] = []
        self._post_hooks: list[PostExecuteHook] = []
        self._token_seq = 0
        self._calls: dict[str, _ToolExecution] = {}
        # A synchronous legacy tool can own a Python thread that cannot be
        # force-killed.  Cancellation still has a hard user-visible deadline;
        # the call is marked abandoned after this budget and its late result is
        # discarded by the runtime/dispatcher.
        self.cancel_drain_timeout = float(
            getattr(getattr(engine, "config", None), "cancel_drain_timeout_seconds", 5.0) or 5.0
        )

    def prepare_call(self, tc: Any, on_event: Any, iteration: int, *, retain: bool = False) -> _ToolExecution:
        existing = self._calls.get(getattr(tc, "_execution_id", ""))
        if existing is not None:
            existing.retained_by_batch = existing.retained_by_batch or retain
            return existing
        name, args = self._name_and_args(tc, getattr(self.dispatcher, "parse_arguments", None))
        parent_id = getattr(tc, "parent_call_id", None) or ""
        parent = next((row for row in reversed(list(self._calls.values()))
                       if row.call_id == parent_id and row.status in {"running", "cancelling"}), None) if parent_id else None
        row = _ToolExecution(self, uuid.uuid4().hex, str(getattr(tc, "id", "")), name, args,
                             parent_id, parent.execution_id if parent else "", on_event, iteration)
        setattr(tc, "_execution_id", row.execution_id)
        row.retained_by_batch = retain
        self._calls[row.execution_id] = row
        self._trim_calls()
        self._state_event(row)
        if parent is not None and parent.cancel_requested:
            self.cancel_call(row.execution_id)
        return row

    def _trim_calls(self) -> None:
        for key in list(self._calls):
            if len(self._calls) <= 256:
                break
            if self._calls[key].status in {"completed", "failed", "cancelled"} and not self._calls[key].retained_by_batch:
                del self._calls[key]

    def call_states(self) -> list[dict[str, Any]]:
        return [row.snapshot() for row in self._calls.values()]

    def cancel_call(self, execution_id: str) -> dict[str, Any]:
        row = self._calls.get(execution_id)
        if row is None:
            raise KeyError(execution_id)
        if row.status in {"completed", "failed", "cancelled"} or row.cancel_requested:
            return row.snapshot()
        row.cancel_requested = True
        row.cancel_event.set()
        was_queued = row.status == "queued"
        row.status = "cancelling"
        for child in list(self._calls.values()):
            if child.parent_execution_id == row.execution_id:
                self.cancel_call(child.execution_id)
        if row.name == "run_code":
            session = getattr(self.engine, "_active_code_mode_session", None)
            if session is not None and getattr(session, "root_call_id", None) == row.call_id:
                session.stop()
        if row.name in {"run_code", "run_shell"}:
            terminate_killable_processes_async(row.execution_id)
            logger.info(
                "取消工具时异步终止子进程: tool=%s execution_id=%s",
                row.name,
                row.execution_id,
            )
        if was_queued:
            row.queued_result = self._cancelled_result(row)
            self._finish_call(row, row.queued_result)
        else:
            self._state_event(row)
            if row.task is not None and not row.task.done():
                row.task.cancel()
        return row.snapshot()

    def _state_event(self, row: _ToolExecution) -> None:
        from excelmanus.events import EventType, ToolCallEvent

        emit = getattr(self.engine, "_emit", None)
        if callable(emit):
            emit(row.on_event, ToolCallEvent(event_type=EventType.TOOL_CALL_STATE,
                tool_call_id=row.call_id, tool_name=row.name, arguments=row.arguments,
                execution_id=row.execution_id, execution_state=row.status,
                parent_call_id=row.parent_call_id, iteration=row.iteration))

    def filter_event(self, event: Any) -> bool:
        """Defer terminal events until the owned call (including approval) settles."""
        from excelmanus.events import EventType

        row = self._calls.get(getattr(event, "execution_id", "")) or current_execution()
        if row is None or row.runtime is not self or event.tool_call_id != row.call_id:
            return True
        event.execution_id = row.execution_id
        event.execution_state = row.status
        if event.event_type == EventType.PENDING_APPROVAL:
            row.approval_id = event.approval_id
        elif event.event_type == EventType.APPROVAL_RESOLVED:
            row.approval_resolved = True
        if row.publishing:
            return True
        if event.event_type == EventType.TOOL_CALL_END:
            return False
        if event.event_type == EventType.TOOL_CALL_START:
            if row.start_emitted:
                return False
            row.start_emitted = True
        return True

    def _cancelled_result(self, row: _ToolExecution) -> ToolCallResult:
        value = error_result(
            "本次工具调用已取消；已经完成的修改会保留，请核对实际结果，不要自动重放。",
            code="CANCELLED", fields={"execution_started": row.execution_started,
                "execution_completed": row.outcome is not None,
                "executed": False if not row.execution_started else None},
        )
        if row.outcome is not None:
            value.ui_meta = row.outcome.ui_meta
            value.value["completed_success"] = row.outcome.success
            if isinstance(row.outcome.value, dict):
                for key in ("operation_id", "content_version", "published", "sdk_calls"):
                    if key in row.outcome.value:
                        value.value[key] = row.outcome.value[key]
            value.model_text = json.dumps(value.value, ensure_ascii=False, default=str)
        return ToolCallResult(tool_name=row.name, arguments=row.arguments, result=value.model_text,
                              success=False, error="CANCELLED", structured=value)

    def _finish_call(self, row: _ToolExecution, result: ToolCallResult) -> None:
        from excelmanus.events import EventType, ToolCallEvent

        row.status = "cancelled" if result.error == "CANCELLED" else "completed" if result.success else "failed"
        row.publishing = True
        emit = getattr(self.engine, "_emit", None)
        if callable(emit):
            if row.status == "cancelled" and row.approval_id and not row.approval_resolved:
                emit(row.on_event, ToolCallEvent(event_type=EventType.APPROVAL_RESOLVED,
                    tool_call_id=row.call_id, tool_name=row.name, approval_tool_name=row.name,
                    approval_id=row.approval_id, success=False, result=result.result,
                    execution_id=row.execution_id, execution_state=row.status, iteration=row.iteration))
            if not row.start_emitted:
                emit(row.on_event, ToolCallEvent(event_type=EventType.TOOL_CALL_START,
                    tool_call_id=row.call_id, tool_name=row.name, arguments=row.arguments,
                    execution_id=row.execution_id, parent_call_id=row.parent_call_id, iteration=row.iteration))
                row.start_emitted = True
            emit(row.on_event, ToolCallEvent(event_type=EventType.TOOL_CALL_END,
                tool_call_id=row.call_id, tool_name=row.name, arguments=result.arguments,
                execution_id=row.execution_id, execution_state=row.status,
                result=result.result, error=result.error, success=result.success,
                ui=result.structured.ui_meta.to_sse_ui() if result.structured else None,
                parent_call_id=row.parent_call_id, iteration=row.iteration))
        row.publishing = False
        row.task = None
        row.outcome = None

    def finish_queued(self, tc: Any, result: ToolCallResult) -> ToolCallResult:
        row = self._calls.get(getattr(tc, "_execution_id", ""))
        if row is not None:
            if row.queued_result is not None:
                return row.queued_result
            self._finish_call(row, result)
        return result

    def end_batch(self, calls: Sequence[Any]) -> None:
        for tc in calls:
            row = self._calls.get(getattr(tc, "_execution_id", ""))
            if row is not None:
                row.retained_by_batch = False
            if row is not None and row.status in {"queued", "cancelling"} and row.task is None:
                self._finish_call(row, self._cancelled_result(row))
        self._trim_calls()

    async def run_managed(self, tc: Any, operation: Any, on_event: Any, iteration: int) -> ToolCallResult:
        row = self.prepare_call(tc, on_event, iteration)
        if row.queued_result is not None:
            return row.queued_result
        if current_execution() is row:
            return await operation()
        row.status = "running"

        async def run() -> Any:
            token = _current_execution.set(row)
            try:
                row.execution_started = True
                return await operation()
            finally:
                _current_execution.reset(token)

        row.task = asyncio.create_task(run())
        try:
            raw = await asyncio.shield(row.task)
            if isinstance(raw, ToolCallResult):
                result = raw
            else:
                structured = coerce_legacy_result(raw)
                result = ToolCallResult(tool_name=row.name, arguments=row.arguments, result=structured.model_text,
                    success=structured.success, error=structured.error.code if structured.error else None, structured=structured)
            if row.cancel_requested:
                result = self._cancelled_result(row)
        except asyncio.CancelledError:
            owner_task = asyncio.current_task()
            parent_stopping = lambda: bool(
                getattr(owner_task, "cancelling", lambda: 0)()
                or getattr(self.dispatcher, "is_cancelled", lambda: False)()
            )
            parent_cancelled = parent_stopping()
            row.cancel_event.set()
            if not row.cancel_requested and not row.task.done():
                row.task.cancel()
            # 取消排空等待按 cancel_drain 记入空闲细分；CancelledError 传播不变。
            with idle_segment(self.engine, "cancel_drain"):
                deadline = time.monotonic() + max(0.1, self.cancel_drain_timeout)
                # Repeated stop requests cannot detach a still-running sync
                # thread.  Bound the drain so cancellation cannot hang the
                # actor forever; killable subprocesses are terminated above.
                while not row.task.done() and time.monotonic() < deadline:
                    try:
                        await asyncio.wait_for(asyncio.shield(row.task), timeout=max(0.05, deadline - time.monotonic()))
                    except asyncio.CancelledError:
                        parent_cancelled = parent_cancelled or parent_stopping()
                        continue
                    except asyncio.TimeoutError:
                        break
                    except Exception:
                        break
                # A stopped SDK bridge can return before a non-cooperative child
                # finishes. Keep the enclosing tool's execution slot until it drains.
                child_tasks = [child.task for child in self._calls.values()
                               if child.parent_execution_id == row.execution_id and child.task is not None]
                for task in child_tasks:
                    while not task.done() and time.monotonic() < deadline:
                        try:
                            await asyncio.wait_for(asyncio.shield(task), timeout=max(0.05, deadline - time.monotonic()))
                        except asyncio.CancelledError:
                            parent_cancelled = parent_cancelled or parent_stopping()
                        except asyncio.TimeoutError:
                            break
                        except Exception:
                            break
                for task in [row.task, *child_tasks]:
                    if task.done() and not task.cancelled():
                        task.exception()  # Retrieve failures even if cancellation won the race.
            result = self._cancelled_result(row)
            self._finish_call(row, result)
            if parent_cancelled or not row.cancel_requested:
                raise
            interaction = getattr(self.engine, "_interaction_handler", None)
            if interaction is not None:
                interaction.observe_tool_result(row.call_id, result.result, False)
            return result
        except Exception:
            self._finish_call(row, self._denied_result(tc, self.allocate_token(row.name, row.arguments),
                                                      "TOOL_EXECUTION_ERROR", "工具执行异常"))
            raise
        self._finish_call(row, result)
        return result

    def add_pre_execute(self, hook: PreExecuteHook) -> None:
        self._pre_hooks.append(hook)

    def add_guard(self, hook: GuardHook) -> None:
        self._guards.append(hook)

    def add_post_execute(self, hook: PostExecuteHook) -> None:
        self._post_hooks.append(hook)

    def allocate_token(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str = "",
        parent: str | None = None,
    ) -> ExecutionToken:
        self._token_seq += 1
        frozen = copy.deepcopy(arguments) if arguments else {}
        cid = call_id or f"exec_{self._token_seq}"
        return ExecutionToken(call_id=cid, name=name, arguments=frozen, parent=parent)

    def is_concurrency_safe(self, tool_name: str, args: dict[str, Any] | None = None) -> bool:
        """仅 PARALLELIZABLE_READONLY_TOOLS。autoApprove / MCP 默认放行不授予并行。"""
        from excelmanus.tools.policy import write_effect_for_call

        try:
            registry = getattr(self.engine, "registry", None) or getattr(self.engine, "_registry", None)
            get_tool = getattr(registry, "get_tool", None)
            tool = get_tool(tool_name) if callable(get_tool) else None
            if tool is not None and write_effect_for_call(
                tool_name, args, declared=getattr(tool, "write_effect", "unknown"),
                actions=getattr(tool, "actions", None),
            ) != "none":
                return False
            if str(tool_name).startswith("mcp_"):
                getter = getattr(self.engine, "get_tool_write_effect", None)
                effect = getter(tool_name) if callable(getter) else "unknown"
                if effect != "none":
                    return False
                # write_effect=none 仍须落在只读并行名单；MCP 名默认不在其中。
            return policy_is_concurrency_safe(tool_name, args) is True
        except Exception:
            return False

    def split_batches(self, tool_calls: Sequence[Any]) -> list[_ToolCallBatch]:
        """按 ``is_concurrency_safe(args)`` 拆相邻并行批。缺省/抛错/False → 独占。"""
        batches: list[_ToolCallBatch] = []
        current_parallel: list[Any] = []
        parse = getattr(self.dispatcher, "parse_arguments", None)
        for tc in tool_calls:
            name, args = self._name_and_args(tc, parse)
            if self.is_concurrency_safe(name, args):
                current_parallel.append(tc)
            else:
                if current_parallel:
                    batches.append(
                        _ToolCallBatch(current_parallel, len(current_parallel) > 1)
                    )
                    current_parallel = []
                batches.append(_ToolCallBatch([tc], False))
        if current_parallel:
            batches.append(_ToolCallBatch(current_parallel, len(current_parallel) > 1))
        return batches

    def build_execution_batches(self, tool_calls: Sequence[Any]) -> list[_ToolCallBatch]:
        return self.plan_execution(tool_calls).batches

    def plan_execution(self, tool_calls: Sequence[Any], *, parallel: bool = True) -> ToolExecutionPlan:
        """Validate dependencies, remove scheduling metadata, then form conservative waves.

        Metadata lives on the call envelope. Legacy inline fields are accepted
        only when the business schema does not declare a field of that name.
        All writes/unknown effects keep their existing exclusive order.
        """
        calls: list[Any] = []
        requested: list[set[str]] = []
        errors: dict[int, tuple[str, str]] = {}
        safe: list[bool] = []
        parse = getattr(self.dispatcher, "parse_arguments", None)
        registry = getattr(self.engine, "registry", None) or getattr(self.engine, "_registry", None)
        for index, original in enumerate(tool_calls):
            name, args = self._name_and_args(original, parse)
            get_tool = getattr(registry, "get_tool", None)
            tool = get_tool(name) if callable(get_tool) else None
            schema = getattr(tool, "input_schema", None)
            properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
            if not isinstance(properties, dict):
                properties = {}
            args = dict(args)
            dependencies: set[str] = set()
            stripped = False
            for field in ("depends_on", "depends_on_call_ids"):
                values = []
                raw = getattr(original, field, None)
                if raw is not None:
                    values.append(raw)
                if field in args and field not in properties:
                    values.append(args.pop(field))
                    stripped = True
                for raw in values:
                    items = [raw] if isinstance(raw, str) else raw
                    if (not isinstance(items, list)
                            or any(not isinstance(item, str) or not item.strip() for item in items)):
                        errors[index] = ("INVALID_DEPENDENCY", "工具依赖必须是非空调用 ID 或 ID 列表。")
                    else:
                        dependencies.update(items)
            tc = SimpleNamespace(
                id=str(getattr(original, "id", "") or ""),
                type=getattr(original, "type", "function"),
                parent_call_id=getattr(original, "parent_call_id", None),
                function=SimpleNamespace(name=name, arguments=(json.dumps(args, ensure_ascii=False)
                    if stripped else getattr(getattr(original, "function", None), "arguments", None))),
            )
            calls.append(tc)
            requested.append(dependencies)
            safe.append(self.is_concurrency_safe(name, args))

        counts = Counter(tc.id for tc in calls if tc.id)
        ids = {tc.id: i for i, tc in enumerate(calls) if tc.id and counts[tc.id] == 1}
        required: list[set[int]] = [set() for _ in calls]
        for i, dependencies in enumerate(requested):
            if calls[i].id and counts[calls[i].id] > 1:
                errors[i] = ("INVALID_DEPENDENCY", "同批工具调用 ID 重复，无法确定依赖身份。")
            for call_id in dependencies:
                if call_id not in ids:
                    errors[i] = ("INVALID_DEPENDENCY", f"依赖 {call_id} 不存在或 ID 不唯一。")
                elif ids[call_id] == i:
                    errors[i] = ("DEPENDENCY_CYCLE", "工具不能依赖自身。")
                else:
                    required[i].add(ids[call_id])
        after = [set(deps) for deps in required]
        for right in range(len(calls)):
            for left in range(right):
                if not safe[left] or not safe[right]:
                    after[right].add(left)

        remaining = set(range(len(calls)))
        completed: set[int] = set()
        batches: list[_ToolCallBatch] = []
        while remaining:
            ready = [i for i in sorted(remaining) if i in errors or after[i] <= completed]
            if not ready:
                # Identify actual cycles (including conflicts with write-order
                # barriers); settling these as errors releases unrelated nodes.
                for origin in sorted(remaining):
                    visited: set[int] = set()
                    pending = list(after[origin] & remaining)
                    while pending:
                        node = pending.pop()
                        if node == origin:
                            errors[origin] = ("DEPENDENCY_CYCLE", "工具依赖成环或与副作用执行顺序冲突，本调用未执行。")
                            break
                        if node not in visited:
                            visited.add(node)
                            pending.extend(after[node] & remaining)
                continue
            # Error nodes are settled individually; only safe ready calls run together.
            if not parallel or ready[0] in errors or not safe[ready[0]]:
                wave = ready[:1]
            else:
                wave = [i for i in ready if i not in errors and safe[i]]
            batches.append(_ToolCallBatch([calls[i] for i in wave], len(wave) > 1))
            completed.update(wave)
            remaining.difference_update(wave)
        return ToolExecutionPlan(batches,
            {id(calls[i]): [calls[j] for j in sorted(deps)] for i, deps in enumerate(required)},
            {id(calls[i]): error for i, error in errors.items()})

    def reclassify_batch(self, tool_calls: Sequence[Any]) -> list[_ToolCallBatch]:
        """启动前重新分类。"""
        return self.split_batches(tool_calls)

    def render_sdk_section(self) -> str:
        from excelmanus.tools.context import execution_catalog_tools
        from excelmanus.code_mode import render_sdk_section

        # 保留显式 SDK 导出入口；正常 system prompt 不再调用此全量渲染。
        # 目录推导失败仍显式上抛，不能伪造空 SDK。
        tool_defs = execution_catalog_tools(self.engine)
        return render_sdk_section(tool_defs)

    async def execute(
        self, tc: Any, tool_scope: Sequence[str] | None, on_event: Any, iteration: int,
        route_result: Any | None = None, skip_start_event: bool = False,
    ) -> ToolCallResult:
        row = current_execution()
        operation = lambda: self._execute_inner(tc, tool_scope, on_event, iteration, route_result, skip_start_event)
        if row is not None and row.runtime is self and row.call_id == str(getattr(tc, "id", "")):
            return await operation()
        return await self.run_managed(tc, operation, on_event, iteration)

    async def _execute_inner(
        self,
        tc: Any,
        tool_scope: Sequence[str] | None,
        on_event: Any,
        iteration: int,
        route_result: Any | None = None,
        skip_start_event: bool = False,
    ) -> ToolCallResult:
        parse = getattr(self.dispatcher, "parse_arguments", None)
        name, args = self._name_and_args(tc, parse)
        parent = getattr(tc, "parent_call_id", None) or None
        if isinstance(parent, str) and not parent.strip():
            parent = None
        token = self.allocate_token(
            name,
            args,
            call_id=str(getattr(tc, "id", "") or ""),
            parent=parent,
        )

        # 3. pre_execute：只返回 allow | deny | ask，禁止改参数
        snapshot = copy.deepcopy(token.arguments)
        decision: PreExecuteDecision = "allow"
        for hook in self._pre_hooks:
            raw = hook(token)
            if raw not in {"allow", "deny", "ask"}:
                raw = "deny"
            decision = raw
            if token.arguments != snapshot:
                token = ExecutionToken(
                    call_id=token.call_id,
                    name=token.name,
                    arguments=snapshot,
                    parent=token.parent,
                )
            if decision == "deny":
                break

        # 4. ask → 本次一次性审批；通道缺失 = 拒绝该调用
        if decision == "ask":
            from excelmanus.security.policy import resolve_approval_policy

            if resolve_approval_policy(self.engine) == "never":
                decision = "allow"
            elif not await self._one_shot_ask(token, on_event=on_event):
                decision = "deny"
            else:
                decision = "allow"

        # 5. 单调 guard：只能维持或升级为 deny
        for guard in self._guards:
            nxt = guard(token, decision)
            if nxt not in {"allow", "deny", "ask"}:
                nxt = "deny"
            if decision == "deny" and nxt == "allow":
                nxt = "deny"
            if nxt == "deny":
                decision = "deny"

        if decision == "deny":
            message = getattr(self.engine, "_last_guard_deny", None) or "工具调用被拒绝。"
            engine = self.engine
            if engine is not None and getattr(engine, "_last_guard_deny", None):
                engine._last_guard_deny = None
            return self._apply_finalize(
                self._denied_result(tc, token, PRE_EXECUTE_DENIED, str(message)),
                token,
            )

        # 6. 现有 dispatcher（超时/重试/workbook_commit 仍在里面）
        tcr = await self.dispatcher.execute(
            tc,
            tool_scope,
            on_event,
            iteration,
            route_result=route_result,
            skip_start_event=skip_start_event,
        )
        if not isinstance(tcr, ToolCallResult):
            structured = coerce_legacy_result(tcr)
            tcr = ToolCallResult(
                tool_name=name,
                arguments=dict(token.arguments),
                result=structured.model_text,
                success=structured.success,
                error=structured.error.code if structured.error else None,
                structured=structured,
            )

        # 7. post_execute
        for hook in self._post_hooks:
            updated = hook(token, tcr)
            if updated is not None:
                tcr = updated

        # Shaping runs inside the dispatcher, before spilling. Finalize once.
        structured = tcr.structured
        if structured is None:
            structured = coerce_legacy_result(tcr.result)
        tcr.structured = structured
        return self._apply_finalize(tcr, token)

    def _apply_finalize(self, tcr: ToolCallResult, token: ExecutionToken) -> ToolCallResult:
        structured = tcr.structured
        if structured is None:
            structured = coerce_legacy_result(tcr.result)
        cap = 0
        engine = self.engine
        config = getattr(engine, "config", None) or getattr(engine, "_config", None)
        if config is not None:
            cap = int(getattr(config, "tool_result_hard_cap_chars", 0) or 0)
        registry = getattr(engine, "_registry", None) or getattr(engine, "registry", None)
        if tcr.success and not (tcr.pending_approval or tcr.pending_question or tcr.defer_tool_result):
            from excelmanus.tools.output_contracts import enforce_output_contract

            getter = getattr(registry, "get_tool", None)
            tool_def = getter(token.name) if callable(getter) else None
            structured = enforce_output_contract(
                structured, token.name, tcr.arguments or dict(token.arguments), tool_def=tool_def,
            )
        if registry is not None:
            structured = annotate_shadow_schema_violations(
                structured,
                registry=registry,
                tool_name=token.name,
                arguments=dict(token.arguments),
            )
        structured = finalize_content(structured, max_chars=cap)
        tcr = replace(tcr, success=tcr.success and structured.success)
        error = tcr.error
        if not tcr.success and structured.error is not None and not error:
            error = structured.error.code
        if structured.error is not None and structured.error.code == SDK_CONTRACT_VIOLATION:
            error = SDK_CONTRACT_VIOLATION
        finalized = replace(
            tcr,
            result=structured.model_text,
            structured=structured,
            error=error,
            arguments=dict(token.arguments) if not tcr.arguments else tcr.arguments,
        )
        interaction = getattr(self.engine, "_interaction_handler", None)
        if interaction is not None and not finalized.pending_approval:
            interaction.observe_tool_result(token.call_id, finalized.result, finalized.success)
        return finalized

    def _denied_result(
        self,
        tc: Any,
        token: ExecutionToken,
        code: str,
        message: str,
    ) -> ToolCallResult:
        structured = error_result(message, code=code)
        return ToolCallResult(
            tool_name=token.name,
            arguments=dict(token.arguments),
            result=structured.model_text,
            success=False,
            error=code,
            structured=structured,
        )

    async def _one_shot_ask(self, token: ExecutionToken, on_event: Any = None) -> bool:
        """Hook ASK：发审批卡，等同一决策通道（resolver 或 InteractionRegistry）。

        与回合级审批同语义：accept 放行本次调用，reject/超时/父取消收敛为
        deny。``_approval_resolver`` 由 driver 在 run_tool_loop 期间挂到
        engine；Web 路径走 ``submit_approval`` → registry resolve。
        """
        from excelmanus.interaction import DEFAULT_INTERACTION_TIMEOUT

        engine = self.engine
        approval = getattr(engine, "approval", None) or getattr(engine, "_approval", None)
        if approval is None:
            return False
        create = getattr(approval, "create_pending", None)
        if not callable(create):
            return False
        try:
            pending = create(
                tool_name=token.name,
                arguments=dict(token.arguments),
                parent_call_id=token.parent,
            )
        except Exception:
            return False

        approval_id = getattr(pending, "approval_id", "") or ""
        emit_card = getattr(engine, "emit_pending_approval_event", None)
        if callable(emit_card):
            try:
                emit_card(
                    pending=pending,
                    on_event=on_event,
                    iteration=0,
                    tool_call_id=token.call_id,
                )
            except Exception:
                logger.debug("Hook ASK 审批事件发射失败", exc_info=True)

        inflight = getattr(engine, "_inflight_approval_ids", None)
        if inflight is None and engine is not None:
            try:
                inflight = engine._inflight_approval_ids = set()
            except Exception:
                inflight = None
        if inflight is not None:
            inflight.add(approval_id)

        async def _reject_pending(*, timeout: bool = False) -> None:
            reject = getattr(approval, "reject_pending", None)
            if callable(reject) and approval_id:
                try:
                    reject(approval_id, timeout=timeout)
                except Exception:
                    pass

        try:
            resolver = getattr(engine, "_approval_resolver", None)
            session = getattr(engine, "_active_code_mode_session", None)
            cancel_event = getattr(session, "_subcall_cancel", None)
            registry = getattr(engine, "_interaction_registry", None)

            if callable(resolver):
                async def _wait() -> Any:
                    try:
                        return await resolver(pending)
                    except Exception:  # noqa: BLE001
                        return "reject"
            elif registry is not None:
                async def _wait() -> Any:
                    interaction = getattr(engine, "_interaction_handler", None)
                    if interaction is not None:
                        return await interaction.wait_approval_decision(approval_id)
                    return await registry.create(approval_id)
            else:
                await _reject_pending()
                return False

            waiter = getattr(self.dispatcher, "_wait_approval_decision", None)
            if callable(waiter):
                wait_timeout = DEFAULT_INTERACTION_TIMEOUT
                timeout_for = getattr(session, "timeout_for", None) if session is not None else None
                if callable(timeout_for):
                    wait_timeout = timeout_for(token.name)
                decision = await waiter(
                    _wait(), cancel_event, wait_timeout,
                )
                from excelmanus.engine_core import tool_dispatcher as _td

                if decision is _td._WAIT_PARENT_CANCELLED:
                    await _reject_pending()
                    return False
                if decision is _td._WAIT_TIMEOUT:
                    await _reject_pending(timeout=True)
                    return False
            else:
                decision = await _wait()
        finally:
            if inflight is not None:
                inflight.discard(approval_id)

        if isinstance(decision, dict):
            decision = decision.get("decision")
        normalized = str(decision or "").lower()
        interaction = getattr(engine, "_interaction_handler", None)
        if interaction is not None:
            interaction.record_approval_decision(approval_id, normalized)
        if normalized == "fullaccess":
            try:
                engine._full_access_enabled = True
            except Exception:
                pass
        emit_resolved = getattr(engine, "_emit", None) or getattr(engine, "emit", None)
        if normalized in {"accept", "approved", "allow", "yes", "fullaccess"}:
            if interaction is not None:
                interaction.approval_execution_started(approval_id)
            # 一次性门禁：决策已落地，清理 pending 让正常分发继续
            # （不清理会占住单槽位，下一个 create_pending 必抛）。
            clear = getattr(approval, "clear_pending", None)
            if callable(clear):
                try:
                    clear()
                except Exception:
                    pass
            if callable(emit_resolved):
                try:
                    from excelmanus.events import EventType, ToolCallEvent

                    emit_resolved(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.APPROVAL_RESOLVED,
                            tool_call_id=token.call_id,
                            approval_id=approval_id,
                            approval_tool_name=token.name,
                            result="已批准",
                            success=True,
                        ),
                    )
                except Exception:
                    logger.debug("Hook ASK 审批解决事件发射失败", exc_info=True)
            return True
        await _reject_pending()
        return False

    @staticmethod
    def _name_and_args(
        tc: Any,
        parse: Callable[[Any], tuple[dict[str, Any], str | None]] | None,
    ) -> tuple[str, dict[str, Any]]:
        function = getattr(tc, "function", None)
        name = str(getattr(function, "name", "") or "")
        raw = getattr(function, "arguments", None)
        if callable(parse):
            args, _err = parse(raw)
            return name, args if isinstance(args, dict) else {}
        if isinstance(raw, dict):
            return name, raw
        return name, {}
