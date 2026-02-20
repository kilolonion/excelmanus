"""SubagentOrchestrator — 从 AgentEngine 解耦的子代理委派组件。

负责管理：
- delegate_to_subagent 元工具的完整执行流程
- 子代理选择、Hook 拦截、结果同步
- 返回结构化 DelegateSubagentOutcome
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from excelmanus.hooks import HookDecision, HookEvent
from excelmanus.logger import get_logger

if TYPE_CHECKING:
    from excelmanus.engine import DelegateSubagentOutcome
    from excelmanus.events import EventCallback

logger = get_logger("subagent_orchestrator")


class SubagentOrchestrator:
    """子代理委派编排器，封装 _delegate_to_subagent 的完整逻辑。

    通过持有 engine 引用来访问必要的基础设施（hook runner、
    subagent executor、window perception 等），但将委派流程
    的控制逻辑集中在此类中。
    """

    def __init__(self, engine: Any) -> None:
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
        picked_agent = engine._normalize_skill_agent_name(picked_agent) or "explorer"

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
