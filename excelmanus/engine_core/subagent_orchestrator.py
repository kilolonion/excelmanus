"""SubagentOrchestrator — 从 AgentEngine 解耦的子代理委派组件。

负责管理：
- delegate_to_subagent 元工具的完整执行流程
- parallel_delegate 元工具的并行子代理委派
- 子代理选择、Hook 拦截、结果同步
- 返回结构化 DelegateSubagentOutcome
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from excelmanus.hooks import HookDecision, HookEvent
from excelmanus.logger import get_logger

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine
    from excelmanus.engine_types import DelegateSubagentOutcome
    from excelmanus.events import EventCallback

logger = get_logger("subagent_orchestrator")


@dataclass
class ParallelDelegateTask:
    """parallel_delegate 中单个子任务的输入描述。"""

    task: str
    agent_name: str | None = None
    file_paths: list[str] = field(default_factory=list)


@dataclass
class ParallelDelegateOutcome:
    """parallel_delegate 的聚合返回。"""

    reply: str
    success: bool
    outcomes: list["DelegateSubagentOutcome"] = field(default_factory=list)
    conflict_error: str | None = None


class SubagentOrchestrator:
    """子代理委派编排器，封装 _delegate_to_subagent 的完整逻辑。

    通过持有 engine 引用来访问必要的基础设施（hook runner、
    subagent executor、window perception 等），但将委派流程
    的控制逻辑集中在此类中。
    """

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine

    async def delegate(
        self,
        *,
        task: str,
        agent_name: str | None = None,
        file_paths: list[Any] | None = None,
        on_event: "EventCallback | None" = None,
    ) -> "DelegateSubagentOutcome":
        """执行 delegate_to_subagent 并返回结构化结果。"""
        from excelmanus.engine_types import DelegateSubagentOutcome

        engine = self._engine

        if not engine._subagent_enabled:
            return DelegateSubagentOutcome(
                reply="subagent 当前处于关闭状态，请先执行 `/subagent on`。",
                success=False,
            )

        task_text = task.strip()
        if not task_text:
            return DelegateSubagentOutcome(
                reply="工具参数错误: task 必须为非空字符串。",
                success=False,
            )

        normalized_paths = self.normalize_file_paths(file_paths)

        picked_agent = (agent_name or "").strip() or "subagent"
        picked_agent = engine._skill_resolver.normalize_skill_agent_name(picked_agent) or "subagent"

        # ── Pre-subagent Hook（A1: 无激活技能时跳过，避免冗余 async 开销） ──
        hook_skill = engine._active_skills[-1] if engine._active_skills else None
        if hook_skill is not None:
            pre_hook_raw = engine._skill_resolver.run_skill_hook(
                skill=hook_skill,
                event=HookEvent.SUBAGENT_START,
                payload={
                    "task": task_text,
                    "agent_name": picked_agent,
                    "file_paths": normalized_paths,
                },
            )
            pre_hook = await engine._skill_resolver.resolve_hook_result(
                event=HookEvent.SUBAGENT_START,
                hook_result=pre_hook_raw,
                on_event=on_event,
            )
            if pre_hook is not None and pre_hook.decision == HookDecision.DENY:
                reason = pre_hook.reason or "Hook 拒绝了子代理执行。"
                return DelegateSubagentOutcome(
                    reply=f"子代理执行已被 Hook 拦截：{reason}",
                    success=False,
                    picked_agent=picked_agent,
                    task_text=task_text,
                    normalized_paths=normalized_paths,
                )

        # ── 执行子代理（带超时保护） ──
        prompt = task_text
        if normalized_paths:
            prompt += f"\n\n相关文件：{', '.join(normalized_paths)}"

        timeout = engine._config.subagent_timeout_seconds
        try:
            result = await asyncio.wait_for(
                engine.run_subagent(
                    agent_name=picked_agent,
                    prompt=prompt,
                    on_event=on_event,
                ),
                timeout=timeout if timeout > 0 else None,
            )
        except asyncio.TimeoutError:
            logger.warning("子代理 %s 执行超时 (%ds)", picked_agent, timeout)
            return DelegateSubagentOutcome(
                reply=f"子代理 {picked_agent} 执行超时（{timeout}s），已终止。",
                success=False,
                picked_agent=picked_agent,
                task_text=task_text,
                normalized_paths=normalized_paths,
            )

        # ── Post-subagent Hook（A1: 无激活技能时跳过） ──
        if hook_skill is not None:
            post_hook_raw = engine._skill_resolver.run_skill_hook(
                skill=hook_skill,
                event=HookEvent.SUBAGENT_STOP,
                payload={
                    "task": task_text,
                    "agent_name": picked_agent,
                    "success": result.success,
                    "summary": result.summary,
                },
            )
            post_hook = await engine._skill_resolver.resolve_hook_result(
                event=HookEvent.SUBAGENT_STOP,
                hook_result=post_hook_raw,
                on_event=on_event,
            )
            if post_hook is not None and post_hook.decision == HookDecision.DENY:
                reason = post_hook.reason or "Hook 拒绝了子代理结果。"
                return DelegateSubagentOutcome(
                    reply=f"子代理执行结果已被 Hook 拦截：{reason}",
                    success=False,
                    picked_agent=picked_agent,
                    task_text=task_text,
                    normalized_paths=normalized_paths,
                    subagent_result=result,
                )

        if result.success:
            return DelegateSubagentOutcome(
                reply=result.summary,
                success=True,
                picked_agent=picked_agent,
                task_text=task_text,
                normalized_paths=normalized_paths,
                subagent_result=result,
            )

        partial_observed = bool(result.observed_files)
        partial_changes = bool(result.structured_changes)

        partial_hint = ""
        if (partial_observed or partial_changes) and "已完成的工作" not in result.summary:
            partial_hint = (
                "（已保留部分产出"
                f"：发现文件 {len(result.observed_files)} 个"
                f"，结构化变更 {len(result.structured_changes)} 条）"
            )

        return DelegateSubagentOutcome(
            reply=f"子代理执行失败（{picked_agent}）：{result.summary}{partial_hint}",
            success=False,
            picked_agent=picked_agent,
            task_text=task_text,
            normalized_paths=normalized_paths,
            subagent_result=result,
        )

    # ── 并行委派 ──────────────────────────────────────────

    async def delegate_parallel(
        self,
        *,
        tasks: list[ParallelDelegateTask],
        on_event: "EventCallback | None" = None,
    ) -> ParallelDelegateOutcome:
        """并行执行多个子代理任务，返回聚合结果。

        前置校验：
        1. subagent 开关
        2. tasks 非空且 <= 5
        3. 文件路径冲突检测（写入子代理不可操作同一文件）
        """
        from excelmanus.engine_types import DelegateSubagentOutcome

        engine = self._engine

        if not engine._subagent_enabled:
            return ParallelDelegateOutcome(
                reply="subagent 当前处于关闭状态，请先执行 `/subagent on`。",
                success=False,
            )

        if not tasks:
            return ParallelDelegateOutcome(
                reply="工具参数错误: tasks 不能为空。",
                success=False,
            )

        max_parallel = engine._config.parallel_subagent_max
        if len(tasks) > max_parallel:
            return ParallelDelegateOutcome(
                reply=f"工具参数错误: 最多同时并行 {max_parallel} 个子任务。",
                success=False,
            )

        # ── 文件冲突检测 ──
        conflict = self._detect_file_conflicts(tasks)
        if conflict is not None:
            return ParallelDelegateOutcome(
                reply=f"文件冲突：{conflict}",
                success=False,
                conflict_error=conflict,
            )

        # ── 并发执行（Semaphore 限流） ──
        sem = asyncio.Semaphore(max_parallel)

        async def _run_one(t: ParallelDelegateTask) -> DelegateSubagentOutcome:
            async with sem:
                return await self.delegate(
                    task=t.task,
                    agent_name=t.agent_name,
                    file_paths=t.file_paths,
                    on_event=on_event,
                )

        raw_results = await asyncio.gather(
            *[_run_one(t) for t in tasks],
            return_exceptions=True,
        )

        # ── 聚合结果 ──
        outcomes: list[DelegateSubagentOutcome] = []
        all_success = True
        reply_parts: list[str] = []

        for i, r in enumerate(raw_results):
            task_label = tasks[i].task[:60]
            if isinstance(r, BaseException):
                outcome = DelegateSubagentOutcome(
                    reply=f"并行子代理异常: {r}",
                    success=False,
                )
                all_success = False
            else:
                outcome = r
                if not outcome.success:
                    all_success = False
            outcomes.append(outcome)

            status = "✅" if outcome.success else "❌"
            reply_parts.append(
                f"{status} 任务 {i + 1}「{task_label}」：{outcome.reply}"
            )

        summary = "\n\n".join(reply_parts)
        return ParallelDelegateOutcome(
            reply=summary,
            success=all_success,
            outcomes=outcomes,
        )

    @staticmethod
    def normalize_file_paths(file_paths: list[Any] | None) -> list[str]:
        """规范化 subagent 输入文件路径。"""
        if not file_paths:
            return []
        normalized: list[str] = []
        for item in file_paths:
            if not isinstance(item, str):
                continue
            path = item.strip()
            if path:
                normalized.append(path)
        return normalized

    @staticmethod
    def _detect_file_conflicts(
        tasks: list[ParallelDelegateTask],
    ) -> str | None:
        """检测并行任务间的文件路径冲突。

        规则：同一文件不能出现在两个不同任务的 file_paths 中。
        返回冲突描述字符串，无冲突返回 None。
        """
        seen: dict[str, int] = {}  # normalized_path -> task index
        for i, t in enumerate(tasks):
            for raw_path in t.file_paths:
                normalized = raw_path.strip().replace("\\", "/")
                while normalized.startswith("./"):
                    normalized = normalized[2:]
                normalized_lower = normalized.lower()
                if normalized_lower in seen:
                    other = seen[normalized_lower]
                    return (
                        f"任务 {other + 1} 和任务 {i + 1} 都涉及文件 "
                        f"'{normalized}'，不能并行执行。"
                        "请将涉及同一文件的操作合并到一个子代理中。"
                    )
                seen[normalized_lower] = i
        return None
