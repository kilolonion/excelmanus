"""SubagentRuntime：唯一发布边界。同步等待是默认；背景/消息为接缝。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from excelmanus.hooks import HookDecision, HookEvent
from excelmanus.logger import get_logger
from excelmanus.subagent.driver import InProcessDriver
from excelmanus.subagent.errors import SubagentError
from excelmanus.subagent.lifecycle import emit_end, emit_start, parent_session_id
from excelmanus.subagent.models import (
    SubagentDescriptor,
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
        return normalize(picked) or "subagent"
    return picked


class SubagentRuntime:
    """校验 → 组合 → 发布 run_id → 跑循环。发布前失败不发 start。"""

    def __init__(self, parent: Any) -> None:
        self._parent = parent
        self._live: dict[str, tuple[SubagentRun, InProcessDriver, Any]] = {}

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

        run_id = str(uuid4())
        descriptor = SubagentDescriptor(
            run_id=run_id,
            agent_name=config.name,
            parent_session_id=parent_session_id(parent),
            delegation_depth=int(getattr(child, "_delegation_depth", 1) or 1),
        )
        run = SubagentRun(run_id)
        run.set_dispose(driver.dispose)
        self._live[run_id] = (run, driver, config)

        prompt = task_text
        if request.file_paths:
            prompt += f"\n\n相关文件：{', '.join(request.file_paths)}"

        emit_start(
            request.on_event,  # type: ignore[arg-type]
            descriptor,
            reason=request.label or task_text,
            permission_mode=config.permission_mode,
        )

        timeout = float(getattr(getattr(parent, "_config", None), "subagent_timeout_seconds", 0) or 0)
        try:
            result = await driver.run(
                parent,
                config,
                prompt=prompt,
                descriptor=descriptor,
                on_event=request.on_event,
                timeout=timeout,
                child=child,
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
            self._live.pop(run_id, None)

        try:
            await self._run_post_hook(
                task_text=task_text,
                picked_agent=config.name,
                result=result,
                on_event=request.on_event,
            )
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

        # interrupt 可能已经先写入 aborted 终态；以已发布结果为准，保证事件与工具结果一致。
        if run.result.done():  # type: ignore[attr-defined]
            result = run.result.result()  # type: ignore[attr-defined]
        else:
            run.set_result(result)
        emit_end(request.on_event, descriptor, result)  # type: ignore[arg-type]
        return run

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
            elif isinstance(item, BaseException):
                all_ok = False
                parts.append(f"❌ 任务 {i + 1}「{label}」：{item}")
            else:
                results.append(item)
                if not item.success:
                    all_ok = False
                mark = "✅" if item.success else "❌"
                parts.append(f"{mark} 任务 {i + 1}「{label}」：{item.output}")
        return ParallelOutcome(reply="\n\n".join(parts), success=all_ok, results=results)

    async def start_background(self, request: SubagentStartRequest) -> str:
        raise SubagentError(
            "UNSUPPORTED_CAPABILITY",
            "背景子代理尚未实现。请使用同步 delegate 等待结果。",
        )

    async def send_message(self, *_args: Any, **_kwargs: Any) -> None:
        raise SubagentError(
            "UNSUPPORTED_CAPABILITY",
            "send_message 尚未实现。",
        )

    async def interrupt(self, run_id: str) -> None:
        live = self._live.get(run_id)
        if live is None:
            return
        run, driver, config = live
        if not run.result.done():  # type: ignore[attr-defined]
            run.set_result(
                failure_result(
                    config=config,
                    conversation_id=run_id,
                    stop_reason="aborted",
                    message=f"子代理 {config.name} 已取消。",
                )
            )
        await driver.dispose()

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
