"""子代理发布、后台运行、结果查询与继续执行。同步和后台共用子 Driver。"""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from dataclasses import asdict, replace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from excelmanus.hooks import HookDecision, HookEvent
from excelmanus.logger import get_logger
from excelmanus.subagent.driver import InProcessDriver
from excelmanus.subagent.errors import SubagentError
from excelmanus.subagent.lifecycle import emit_end, emit_start, parent_session_id
from excelmanus.subagent.models import (
    SubagentDescriptor,
    SubagentConfig,
    SubagentResult,
    SubagentRun,
    SubagentStartRequest,
)
from excelmanus.subagent.parallel import (
    ParallelOutcome,
    ParallelTask,
    assert_parallel_allowed,
)
from excelmanus.subagent.result import bound_diagnostic, failure_result

if TYPE_CHECKING:
    from excelmanus.events import EventCallback

logger = get_logger("subagent.runtime")


def normalize_file_paths(file_paths: list[Any] | None) -> list[str]:
    if not file_paths:
        return []
    return [item.strip() for item in file_paths if isinstance(item, str) and item.strip()]


def _resolve_agent_name(parent: Any, raw: str | None) -> str:
    picked = (raw or "").strip() or "subagent"
    resolver = getattr(parent, "_skill_resolver", None)
    normalize = getattr(resolver, "normalize_skill_agent_name", None)
    if callable(normalize):
        return str(normalize(picked) or "subagent")
    return picked


class SubagentRuntime:
    """校验 → 组合 → 发布 run_id → 跑循环。发布前失败不发 start。"""

    def __init__(self, parent: Any) -> None:
        self._parent = parent
        self._live: dict[str, tuple[SubagentRun, InProcessDriver, Any]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._records: dict[str, dict[str, Any]] = {}
        self._callbacks: dict[str, Any] = {}
        self._background_slots = asyncio.Semaphore(
            int(getattr(getattr(parent, "_config", None), "parallel_subagent_max", 3) or 3)
        )

    def list_catalog(self) -> str:
        registry = getattr(self._parent, "_subagent_registry", None)
        if registry is None:
            return "当前没有可用子代理。"
        agents = registry.list_all()
        if not agents:
            return "当前没有可用子代理。"
        lines = [f"共 {len(agents)} 个可用子代理：\n"]
        for agent in agents:
            lines.append(f"- {agent.name} ({agent.permission_mode})：{agent.description}")
        return "\n".join(lines)

    async def start(self, request: SubagentStartRequest) -> SubagentRun:
        run = await self._launch(request, background=False)
        try:
            await asyncio.shield(self._tasks[run.id])
        except asyncio.CancelledError:
            await self.interrupt(run.id)
            raise
        return run

    async def _launch(
        self, request: SubagentStartRequest, *, background: bool,
        history: list[dict[str, Any]] | None = None, resumed_from: str | None = None,
    ) -> SubagentRun:
        parent = self._parent
        if not getattr(parent, "_subagent_enabled", True):
            raise SubagentError("DISABLED", "subagent 当前处于关闭状态，请先执行 `/subagent on`。")
        task_text = (request.task or "").strip()
        if not task_text:
            raise SubagentError("EMPTY_TASK", "工具参数错误: task 必须为非空字符串。")

        picked = _resolve_agent_name(parent, request.agent_name)
        registry = getattr(parent, "_subagent_registry", None)
        config = registry.get(picked) if registry is not None else None
        if config is None:
            raise SubagentError("NOT_FOUND", f"未找到子代理: {picked}")

        await self._run_pre_hook(
            task_text=task_text,
            picked_agent=config.name,
            file_paths=request.file_paths,
            on_event=request.on_event,
        )

        driver = InProcessDriver()
        child = driver.compose(parent, config)
        from excelmanus.subagent.child import assert_child_capability_subset

        assert_child_capability_subset(parent, child)
        if background:
            child._background_parent = parent
            dispatcher = getattr(parent, "_tool_dispatcher", None)
            if dispatcher is not None:
                dispatcher._readonly_replay_cache.clear()
        if history:
            child.inject_history(deepcopy(history))
            child.memory.repair_dangling_tool_calls()

        run_id = str(uuid4())
        from excelmanus.trace import scope_key, trace_of

        trace = trace_of(parent)
        if trace is not None:
            child._trace = trace
            child._trace_scope = scope_key(parent, f"subagent:{run_id}")
            child._trace_parent_key = scope_key(parent, f"subagent:{run_id}")
            child._trace_persist = parent.save_session_snapshot
            callback = request.on_event

            def traced_event(event: Any) -> None:
                parent._trace_event(event)
                if callback is not None:
                    callback(event)

            request = replace(request, on_event=traced_event)
        descriptor = SubagentDescriptor(
            run_id=run_id,
            agent_name=config.name,
            parent_session_id=parent_session_id(parent),
            delegation_depth=int(getattr(child, "_delegation_depth", 1) or 1),
            mode="background" if background else "one-shot",
        )
        run = SubagentRun(run_id)
        run.set_dispose(lambda: self.interrupt(run_id))
        self._live[run_id] = (run, driver, config)
        self._callbacks[run_id] = request.on_event
        self._records[run_id] = {
            "run_id": run_id, "agent_name": config.name, "task": task_text,
            "parent_turn_id": getattr(getattr(parent, "_driver", None), "turn_id", ""),
            "file_paths": list(request.file_paths), "background": background,
            "status": "queued", "created_at": time.time(), "started_at": None, "finished_at": None,
            "iteration": 0, "tool_calls": 0, "last_tool": "", "result": None,
            "history": deepcopy(history or []), "resumed_from": resumed_from,
        }

        prompt = task_text
        if request.file_paths:
            prompt += f"\n\n相关文件：{', '.join(request.file_paths)}"

        emit_start(
            request.on_event,  # type: ignore[arg-type]
            descriptor,
            reason=request.label or task_text,
            permission_mode=config.permission_mode,
        )
        self._tasks[run_id] = asyncio.create_task(
            self._run_published(request, config, descriptor, run, driver, child, prompt),
            name=f"subagent:{run_id}",
        )
        def finished(task: asyncio.Task[None]) -> None:
            exc = None if task.cancelled() else task.exception()
            if not run.result.done():
                self._settle(
                    run, descriptor,
                    failure_result(config=config, conversation_id=run_id,
                                   stop_reason="aborted" if task.cancelled() else "error",
                                   message=str(exc) if exc else "子任务执行已中断。"),
                    request.on_event,
                )
        self._tasks[run_id].add_done_callback(finished)
        self._persist()
        return run

    async def _run_published(
        self, request: SubagentStartRequest, config: SubagentConfig,
        descriptor: SubagentDescriptor, run: SubagentRun,
        driver: InProcessDriver, child: Any, prompt: str,
    ) -> None:
        parent = self._parent
        run_id = run.id
        task_text = request.task.strip()
        record = self._records[run_id]

        def progress(event: Any) -> None:
            from excelmanus.events import EventType

            record["iteration"] = max(record["iteration"], event.iteration)
            if event.event_type == EventType.SUBAGENT_TOOL_START:
                record["tool_calls"] += 1
                record["last_tool"] = event.tool_name
            if event.event_type == EventType.SUBAGENT_ITERATION:
                self._capture_history(run_id, child)
                self._persist()
            if request.on_event is not None:
                request.on_event(event)

        async def execute() -> SubagentResult:
            record["status"] = "running"
            record["started_at"] = time.time()
            self._persist()
            return await driver.run(
                parent, config, prompt=prompt, descriptor=descriptor,
                on_event=progress, timeout=timeout, child=child,
            )

        timeout = float(getattr(getattr(parent, "_config", None), "subagent_timeout_seconds", 0) or 0)
        parent_remaining_fn = getattr(getattr(parent, "_driver", None), "remaining_turn_seconds", None)
        parent_remaining = parent_remaining_fn() if callable(parent_remaining_fn) else None
        if parent_remaining is not None:
            timeout = min(timeout or parent_remaining, parent_remaining)
        try:
            if record["background"]:
                async with self._background_slots:
                    result = await execute()
            else:
                result = await execute()
        except asyncio.CancelledError:
            result = failure_result(
                config=config, conversation_id=run_id, stop_reason="aborted",
                message=f"子代理 {config.name} 已停止。",
            )
        except Exception as exc:
            logger.warning("子代理 %s 基础设施失败: %s", config.name, exc, exc_info=True)
            result = failure_result(
                config=config,
                conversation_id=run_id,
                stop_reason="error",
                message=str(exc),
            )
        finally:
            await driver.dispose()
            self._capture_history(run_id, child)

        try:
            await self._run_post_hook(
                task_text=task_text,
                picked_agent=config.name,
                result=result,
                on_event=request.on_event,
            )
        except asyncio.CancelledError:
            result = replace(result, stop_reason="aborted", diagnostic="子代理已停止。")
        except SubagentError as exc:
            from excelmanus.engine_core.error_payload import dumps_error_payload, payload_from_subagent_error

            logger.warning("子代理 post-hook 拦截: %s", exc.message)
            text = dumps_error_payload(
                payload_from_subagent_error(exc, published=True, conversation_id=run_id)
            )
            result = SubagentResult(
                stop_reason="refusal",
                output=text,
                diagnostic=text,
                subagent_name=config.name,
                permission_mode=config.permission_mode,
                conversation_id=run_id,
                iterations=result.iterations,
                tool_calls_count=result.tool_calls_count,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                structured_changes=result.structured_changes,
                observed_files=result.observed_files,
            )
        except Exception as exc:
            logger.warning("子代理 post-hook 异常: %s", exc, exc_info=True)
            result = SubagentResult(
                stop_reason="error",
                output=str(exc),
                diagnostic=str(exc),
                subagent_name=config.name,
                permission_mode=config.permission_mode,
                conversation_id=run_id,
                iterations=result.iterations,
                tool_calls_count=result.tool_calls_count,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                structured_changes=result.structured_changes,
                observed_files=result.observed_files,
            )

        self._settle(run, descriptor, result, request.on_event)

    def _capture_history(self, run_id: str, child: Any) -> None:
        messages = getattr(getattr(child, "memory", None), "messages", None)
        if isinstance(messages, list):
            self._records[run_id]["history"] = deepcopy(messages)
        inbox = getattr(getattr(child, "_driver", None), "inbox", None)
        if inbox is not None:
            self._records[run_id]["pending_messages"] = [item.content for item in inbox.next_step]
        state = getattr(child, "_state", None)
        files = getattr(state, "affected_files", None)
        if isinstance(files, list):
            self._records[run_id]["changed_files"] = list(files)

    def _settle(
        self, run: SubagentRun, descriptor: SubagentDescriptor,
        result: SubagentResult, on_event: Any,
    ) -> None:
        if run.result.done():
            return
        record = self._records[run.id]
        stop = record.pop("stop_requested", None)
        if stop:
            result = replace(result, stop_reason="aborted", diagnostic=f"子代理已{stop}。")
        record.update(
            status="paused" if stop == "暂停" else result.stop_reason,
            result=asdict(result), finished_at=time.time(),
            iteration=max(record["iteration"], result.iterations),
            tool_calls=max(record["tool_calls"], result.tool_calls_count),
        )
        self._live.pop(run.id, None)
        self._tasks.pop(run.id, None)
        self._callbacks.pop(run.id, None)
        dispatcher = getattr(self._parent, "_tool_dispatcher", None)
        if dispatcher is not None and record["background"]:
            dispatcher._readonly_replay_cache.clear()
        run.set_result(result)
        emit_end(on_event, descriptor, result)
        if record["background"]:
            inject = getattr(getattr(self._parent, "_driver", None), "inject", None)
            if callable(inject):
                inject(
                    f"子任务 {run.id}（{descriptor.agent_name}）状态：{record['status']}。"
                    f'可用 delegate(action="status", run_id="{run.id}") 获取结果。',
                    extra={"prompt_kind": "subagent_result"},
                )
        self._persist()

    async def start_parallel(
        self,
        tasks: list[ParallelTask],
        *,
        on_event: EventCallback | None = None,
    ) -> ParallelOutcome:
        parent = self._parent
        if not getattr(parent, "_subagent_enabled", True):
            raise SubagentError("DISABLED", "subagent 当前处于关闭状态，请先执行 `/subagent on`。")
        if not tasks:
            raise SubagentError("EMPTY_TASK", "工具参数错误: tasks 不能为空。")
        max_parallel = int(getattr(getattr(parent, "_config", None), "parallel_subagent_max", 3) or 3)
        if len(tasks) > max_parallel:
            raise SubagentError(
                "PARALLEL_LIMIT",
                f"工具参数错误: 最多同时并行 {max_parallel} 个子任务。",
            )

        registry = getattr(parent, "_subagent_registry", None)
        resolved: list[tuple[ParallelTask, Any]] = []
        for task in tasks:
            picked = _resolve_agent_name(parent, task.agent_name)
            config = registry.get(picked) if registry is not None else None
            if config is None:
                raise SubagentError("NOT_FOUND", f"未找到子代理: {picked}")
            resolved.append((task, config))
        from excelmanus.tools.context import capability_from_engine

        parent_cap = getattr(parent, "_fixed_capability", None) or capability_from_engine(parent)
        assert_parallel_allowed(resolved, parent_capability=parent_cap)

        sem = asyncio.Semaphore(max_parallel)

        async def _one(task: ParallelTask) -> SubagentResult:
            async with sem:
                run = await self.start(
                    SubagentStartRequest(
                        task=task.task,
                        agent_name=task.agent_name,
                        file_paths=task.file_paths,
                        on_event=on_event,
                    )
                )
                return await run.result  # type: ignore[misc]

        raw = await asyncio.gather(
            *[_one(task) for task, _cfg in resolved],
            return_exceptions=True,
        )
        results: list[SubagentResult] = []
        parts: list[str] = []
        all_ok = True
        for i, item in enumerate(raw):
            label = tasks[i].task[:60]
            if isinstance(item, SubagentError):
                all_ok = False
                from excelmanus.engine_core.error_payload import (
                    dumps_error_payload,
                    payload_from_subagent_error,
                )

                text = dumps_error_payload(payload_from_subagent_error(item))
                parts.append(f"❌ 任务 {i + 1}「{label}」：{text}")
                results.append(
                    failure_result(
                        config=resolved[i][1],
                        conversation_id=f"parallel-{i}",
                        stop_reason="error",
                        message=text,
                    )
                )
            elif isinstance(item, BaseException):
                all_ok = False
                parts.append(f"❌ 任务 {i + 1}「{label}」：{item}")
                results.append(
                    failure_result(
                        config=resolved[i][1],
                        conversation_id=f"parallel-{i}",
                        stop_reason="error",
                        message=str(item),
                    )
                )
            else:
                results.append(item)
                if not item.success:
                    all_ok = False
                mark = "✅" if item.success else "❌"
                parts.append(f"{mark} 任务 {i + 1}「{label}」：{item.output}")
        return ParallelOutcome(reply="\n\n".join(parts), success=all_ok, results=results)

    async def start_background(self, request: SubagentStartRequest) -> str:
        run = await self._launch(request, background=True)
        return run.id

    def get_run(self, run_id: str) -> dict[str, Any]:
        record = self._records.get(run_id)
        if record is None:
            raise SubagentError("NOT_FOUND", f"未找到本会话的子任务: {run_id}")
        public = deepcopy({key: value for key, value in record.items()
                           if key not in {"history", "stop_requested"}})
        live = self._live.get(run_id)
        child = live[1]._child if live is not None else None
        questions = getattr(child, "_question_flow", None)
        if questions is not None and questions.current() is not None:
            public["status"] = "waiting_input"
            public["pending_question"] = asdict(questions.current())
        return public

    def list_runs(self) -> list[dict[str, Any]]:
        return [self.get_run(run_id) for run_id in self._records]

    async def wait(self, run_id: str, timeout: float = 30) -> dict[str, Any]:
        self.get_run(run_id)
        live = self._live.get(run_id)
        if live is not None and timeout > 0:
            try:
                await asyncio.wait_for(asyncio.shield(live[0].result), timeout=timeout)
            except asyncio.TimeoutError:
                pass  # 查询超时不取消正在执行的子任务。
        return self.get_run(run_id)

    async def send_message(self, run_id: str, message: str) -> dict[str, Any]:
        self.get_run(run_id)
        if not message.strip():
            raise SubagentError("EMPTY_TASK", "追加指令不能为空。")
        live = self._live.get(run_id)
        if live is None:
            raise SubagentError("NOT_RUNNING", "子任务已结束；使用 resume 加上 message 继续。")
        child = live[1]._child
        questions = getattr(child, "_question_flow", None)
        if questions is not None and questions.current() is not None:
            pending = questions.current()
            payload = questions.parse_answer(message, question=pending).to_tool_result()
            if child._interaction_registry.resolve(pending.question_id, payload):
                return self.get_run(run_id)
        if child is None or (
            child._driver.status != "running" and not child._driver.inbox.next_turn
            and self._records[run_id]["status"] != "queued"
        ):
            raise SubagentError("NOT_RUNNING", "子任务正在收尾；等待终态后用 resume 追加任务。")
        child._driver.steer(message.strip())
        self._capture_history(run_id, child)
        self._persist()
        return self.get_run(run_id)

    async def resume(self, run_id: str, message: str = "", *, on_event: Any = None) -> str:
        self.get_run(run_id)
        if run_id in self._live:
            raise SubagentError("ALREADY_RUNNING", "子任务仍在执行；使用 send 追加指令。")
        record = self._records[run_id]
        pending = "\n".join(record.get("pending_messages") or [])
        continuation = message.strip() or (
            f"继续原任务：{record['task']}。结合已有对话继续；"
            "上次执行可能中断，先查看当前文件状态再决定下一步。"
        )
        if pending:
            continuation += f"\n尚未处理的追加指令：\n{pending}"
        request = SubagentStartRequest(
            task=continuation,
            agent_name=record["agent_name"], file_paths=list(record.get("file_paths") or []),
            on_event=on_event,
        )
        run = await self._launch(request, background=True,
                                 history=record.get("history"), resumed_from=run_id)
        return run.id

    async def interrupt_turn(self, turn_id: str) -> None:
        children = [(run_id, driver._child) for run_id, (_, driver, _) in list(self._live.items())
                    if self._records.get(run_id, {}).get("parent_turn_id") == turn_id]
        await asyncio.gather(*(self.interrupt(run_id) for run_id, _ in children))
        for _, child in children:
            if child is not None:
                await child._subagent_runtime.interrupt_turn(child._driver.turn_id)
                await child._driver._settle_interrupted_work()

    async def interrupt(self, run_id: str, *, pause: bool = False) -> None:
        self.get_run(run_id)
        live = self._live.get(run_id)
        if live is None:
            return
        run, driver, config = live
        record = self._records[run_id]
        already_stopping = bool(record.get("stop_requested"))
        record["stop_requested"] = "暂停" if pause else "取消"
        child = driver._child
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            if not already_stopping:
                task.cancel()
            await asyncio.shield(asyncio.gather(task, return_exceptions=True))
        await driver.dispose()
        self._capture_history(run_id, child)
        # Task 可能在协程尚未开始时就被取消，仍需结算 run 和生命周期事件。
        if not run.result.done():
            self._settle(
                run, SubagentDescriptor(run_id=run_id, agent_name=config.name),
                failure_result(config=config, conversation_id=run_id, stop_reason="aborted",
                               message=f"子代理 {config.name} 已停止。"),
                self._callbacks.get(run_id),
            )

    @property
    def has_active_runs(self) -> bool:
        return bool(self._live)

    async def close(self) -> None:
        for run_id in list(self._live):
            await self.interrupt(run_id)

    def snapshot(self) -> list[dict[str, Any]]:
        """保存任务结果与最近一次对话边界；不序列化 Task/Future/客户端。"""
        return deepcopy([row for row in self._records.values() if row["background"]])

    def restore(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            record = deepcopy(row)
            if record["status"] in {"queued", "running"}:
                record["status"] = "interrupted"
            record.pop("stop_requested", None)
            self._records[record["run_id"]] = record

    def _persist(self) -> None:
        saver = getattr(self._parent, "save_session_snapshot", None)
        if callable(saver):
            saver()

    async def _run_pre_hook(
        self,
        *,
        task_text: str,
        picked_agent: str,
        file_paths: list[str],
        on_event: Any,
    ) -> None:
        parent = self._parent
        skills = getattr(parent, "_active_skills", None) or []
        if not skills:
            return
        hook_skill = skills[-1]
        resolver = getattr(parent, "_skill_resolver", None)
        if resolver is None:
            return
        pre_raw = resolver.run_skill_hook(
            skill=hook_skill,
            event=HookEvent.SUBAGENT_START,
            payload={
                "task": task_text,
                "agent_name": picked_agent,
                "file_paths": file_paths,
            },
        )
        pre = await resolver.resolve_hook_result(
            event=HookEvent.SUBAGENT_START,
            hook_result=pre_raw,
            on_event=on_event,
        )
        if pre is not None and pre.decision == HookDecision.DENY:
            raise SubagentError(
                "HOOK_DENIED",
                f"子代理执行已被 Hook 拦截：{pre.reason or 'Hook 拒绝了子代理执行。'}",
            )

    async def _run_post_hook(
        self,
        *,
        task_text: str,
        picked_agent: str,
        result: SubagentResult,
        on_event: Any,
    ) -> None:
        parent = self._parent
        skills = getattr(parent, "_active_skills", None) or []
        if not skills:
            return
        hook_skill = skills[-1]
        resolver = getattr(parent, "_skill_resolver", None)
        if resolver is None:
            return
        post_raw = resolver.run_skill_hook(
            skill=hook_skill,
            event=HookEvent.SUBAGENT_STOP,
            payload={
                "task": task_text,
                "agent_name": picked_agent,
                "success": result.success,
                "summary": result.output,
            },
        )
        post = await resolver.resolve_hook_result(
            event=HookEvent.SUBAGENT_STOP,
            hook_result=post_raw,
            on_event=on_event,
        )
        if post is not None and post.decision == HookDecision.DENY:
            raise SubagentError(
                "HOOK_DENIED",
                bound_diagnostic(f"子代理执行结果已被 Hook 拦截：{post.reason or 'Hook 拒绝了子代理结果。'}"),
            )
