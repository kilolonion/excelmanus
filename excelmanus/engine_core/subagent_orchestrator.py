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
    from excelmanus.engine import AgentEngine, DelegateSubagentOutcome
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
        from excelmanus.engine import DelegateSubagentOutcome

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

        normalized_paths = engine._normalize_subagent_file_paths(file_paths)

        picked_agent = (agent_name or "").strip()
        if not picked_agent:
            picked_agent = await engine._auto_select_subagent(
                task=task_text,
                file_paths=normalized_paths,
            )
        picked_agent = engine._normalize_skill_agent_name(picked_agent) or "subagent"

        # ── Pre-subagent Hook ──
        hook_skill = engine._active_skills[-1] if engine._active_skills else None
        pre_hook_raw = engine._run_skill_hook(
            skill=hook_skill,
            event=HookEvent.SUBAGENT_START,
            payload={
                "task": task_text,
                "agent_name": picked_agent,
                "file_paths": normalized_paths,
            },
        )
        pre_hook = await engine._resolve_hook_result(
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

        # ── 执行子代理 ──
        prompt = task_text
        if normalized_paths:
            prompt += f"\n\n相关文件：{', '.join(normalized_paths)}"

        result = await engine.run_subagent(
            agent_name=picked_agent,
            prompt=prompt,
            on_event=on_event,
        )

        # ── Post-subagent Hook ──
        post_hook_raw = engine._run_skill_hook(
            skill=hook_skill,
            event=HookEvent.SUBAGENT_STOP,
            payload={
                "task": task_text,
                "agent_name": picked_agent,
                "success": result.success,
                "summary": result.summary,
            },
        )
        post_hook = await engine._resolve_hook_result(
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
            engine._window_perception.observe_subagent_context(
                candidate_paths=[*normalized_paths, *result.observed_files],
                subagent_name=picked_agent,
                task=task_text,
            )
            # 子代理有写入时，标记 window 为 stale 并清缓存
            if result.structured_changes:
                engine._window_perception.observe_subagent_writes(
                    structured_changes=result.structured_changes,
                    subagent_name=picked_agent,
                    task=task_text,
                )
            return DelegateSubagentOutcome(
                reply=result.summary,
                success=True,
                picked_agent=picked_agent,
                task_text=task_text,
                normalized_paths=normalized_paths,
                subagent_result=result,
            )

        return DelegateSubagentOutcome(
            reply=f"子代理执行失败（{picked_agent}）：{result.summary}",
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
        from excelmanus.engine import DelegateSubagentOutcome

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

        if len(tasks) > 5:
            return ParallelDelegateOutcome(
                reply="工具参数错误: 最多同时并行 5 个子任务。",
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

        # ── 并发执行 ──
        async def _run_one(t: ParallelDelegateTask) -> DelegateSubagentOutcome:
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

        # 写入传播：将所有成功子代理的 structured_changes 传播到 window perception
        for outcome in outcomes:
            sub = outcome.subagent_result
            if outcome.success and sub is not None:
                all_paths = [*outcome.normalized_paths, *sub.observed_files]
                engine._window_perception.observe_subagent_context(
                    candidate_paths=all_paths,
                    subagent_name=outcome.picked_agent or "subagent",
                    task=outcome.task_text,
                )
                if sub.structured_changes:
                    engine._window_perception.observe_subagent_writes(
                        structured_changes=sub.structured_changes,
                        subagent_name=outcome.picked_agent or "subagent",
                        task=outcome.task_text,
                    )

        summary = "\n\n".join(reply_parts)
        return ParallelDelegateOutcome(
            reply=summary,
            success=all_success,
            outcomes=outcomes,
        )

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
