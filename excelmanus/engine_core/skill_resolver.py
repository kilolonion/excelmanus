"""技能解析与 Hook 管理 — 从 AgentEngine 提取的 Skill 命令解析和钩子执行逻辑。

包括：
- 斜杠命令解析（/skill_name args）
- 技能名称归一化与匹配
- 技能加载状态查询
- Hook 事件处理（SESSION_START, PRE_TOOL_USE, STOP 等）
- Hook agent action 执行（含递归保护）
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from excelmanus.engine_utils import _SKILL_AGENT_ALIASES
from excelmanus.hooks import (
    HookAgentAction,
    HookCallContext,
    HookDecision,
    HookEvent,
    HookResult,
)
from excelmanus.logger import get_logger

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine
    from excelmanus.events import EventCallback
    from excelmanus.skillpacks import SkillMatchResult, Skillpack

logger = get_logger("skill_resolver")


class SkillResolver:
    """技能解析与 Hook 管理组件。

    通过 ``self._engine`` 引用访问 AgentEngine 的技能路由器和钩子系统。
    """

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine

    # ── 命令解析 ──────────────────────────────────────────

    @staticmethod
    def normalize_skill_command_name(name: str) -> str:
        """命令名归一化：小写并移除连字符/下划线。"""
        return name.strip().lower().replace("-", "").replace("_", "")

    @staticmethod
    def iter_slash_command_lines(user_message: str) -> list[str]:
        """提取消息中所有可能的斜杠命令片段（支持命令出现在句中）。"""
        text = user_message.strip()
        if not text:
            return []
        command_lines: list[str] = []
        for idx, char in enumerate(text):
            if char != "/":
                continue
            if idx > 0 and not text[idx - 1].isspace():
                continue
            command_line = text[idx + 1 :].strip()
            if command_line:
                command_lines.append(command_line)
        return command_lines

    def resolve_skill_from_command_line(
        self,
        command_line: str,
        *,
        skill_names: Sequence[str],
    ) -> tuple[str, str] | None:
        """解析单个命令片段，返回 (skill_name, raw_args)。"""
        lower_to_name = {name.lower(): name for name in skill_names}
        command_line_lower = command_line.lower()

        # 1) 精确匹配（含命名空间）
        exact = lower_to_name.get(command_line_lower)
        if exact is not None:
            return exact, ""

        # 2) 前缀匹配（/skill_name 后跟参数）
        for candidate in sorted(skill_names, key=len, reverse=True):
            lower_candidate = candidate.lower()
            if command_line_lower == lower_candidate:
                return candidate, ""
            if command_line_lower.startswith(lower_candidate + " "):
                raw_args = command_line[len(candidate) :].strip()
                return candidate, raw_args

        command_token, _, raw_tail = command_line.partition(" ")

        # 先尝试已注册技能匹配，之后再按路径输入兜底排除，避免误伤命名空间技能。
        if "/" in command_token and "." in command_token:
            return None

        # 3) 无分隔符归一兜底匹配（兼容旧命令）
        normalized_cmd = self.normalize_skill_command_name(command_token)
        normalized_matches = [
            name
            for name in skill_names
            if self.normalize_skill_command_name(name) == normalized_cmd
        ]
        if len(normalized_matches) == 1:
            return normalized_matches[0], raw_tail.strip()
        return None

    def resolve_skill_command_with_args(self, user_message: str) -> tuple[str, str] | None:
        """解析消息中的手动 Skill 命令并返回 (skill_name, raw_args)。"""
        skill_names = self.list_manual_invocable_skill_names()
        if not skill_names:
            return None
        for command_line in self.iter_slash_command_lines(user_message):
            resolved = self.resolve_skill_from_command_line(
                command_line,
                skill_names=skill_names,
            )
            if resolved is not None:
                return resolved
        return None

    def resolve_skill_command(self, user_message: str) -> str | None:
        """将消息中的 `/skill_name ...` 解析为 Skill 名称（用于手动调用）。"""
        resolved = self.resolve_skill_command_with_args(user_message)
        if resolved is None:
            return None
        return resolved[0]

    # ── 技能查询 ──────────────────────────────────────────

    def list_loaded_skill_names(self) -> list[str]:
        """获取当前可匹配的 Skill 名称；为空时尝试主动加载。"""
        e = self._engine
        if e._skill_router is None:
            return []
        skillpacks = e._skill_router._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = e._skill_router._loader.load_all()
        return list(skillpacks.keys())

    def get_loaded_skillpacks(self) -> dict | None:
        """获取运行时已加载的 Skillpack 对象字典，供预路由 catalog 构建使用。"""
        e = self._engine
        if e._skill_router is None:
            return None
        skillpacks = e._skill_router._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = e._skill_router._loader.load_all()
        return skillpacks or None

    def list_manual_invocable_skill_names(self) -> list[str]:
        """获取可手动调用的技能名（user_invocable=true）。"""
        e = self._engine
        if e._skillpack_manager is not None:
            rows = e._skillpack_manager.list_skillpacks()
            return [
                str(item["name"])
                for item in rows
                if bool(item.get("user_invocable", True))
            ]
        if e._skill_router is None:
            return []
        skillpacks = e._skill_router._loader.get_skillpacks()
        if not skillpacks:
            skillpacks = e._skill_router._loader.load_all()
        names: list[str] = []
        for name, skill in skillpacks.items():
            if not isinstance(name, str) or not name.strip():
                continue
            if bool(getattr(skill, "user_invocable", True)):
                names.append(name)
        return names

    @staticmethod
    def normalize_skill_name(name: str) -> str:
        """归一化技能名：小写、去除连字符和下划线，与 router 保持一致。"""
        return name.strip().lower().replace("-", "").replace("_", "")

    def blocked_skillpacks(self) -> set[str] | None:
        """返回当前会话被限制的技能包集合。"""
        e = self._engine
        if e._full_access_enabled:
            return None
        return set(e._restricted_code_skillpacks)

    def get_loaded_skill(self, name: str) -> "Skillpack | None":
        e = self._engine
        if e._skill_router is None:
            return None
        loader = e._skill_router._loader
        skill = loader.get_skillpack(name)
        if skill is not None:
            return skill
        skillpacks = loader.get_skillpacks()
        if not skillpacks:
            skillpacks = loader.load_all()
        return skillpacks.get(name)

    def pick_route_skill(self, route_result: "SkillMatchResult | None") -> "Skillpack | None":
        e = self._engine
        if e._active_skills:
            return e._active_skills[-1]
        if route_result is None or not route_result.skills_used:
            return None
        return self.get_loaded_skill(route_result.skills_used[0])

    @property
    def primary_skill(self) -> "Skillpack | None":
        """当前主 skill（列表末尾），无激活时返回 None。"""
        e = self._engine
        return e._active_skills[-1] if e._active_skills else None

    @staticmethod
    def normalize_skill_agent_name(agent_name: str | None) -> str | None:
        if not agent_name:
            return None
        normalized = agent_name.strip()
        if not normalized:
            return None
        lowered = normalized.lower()
        return _SKILL_AGENT_ALIASES.get(lowered, normalized)

    # ── Hook 管理 ──────────────────────────────────────────

    def push_hook_context(self, text: str) -> None:
        normalized = text.strip()
        if not normalized:
            return
        self._engine._transient_hook_contexts.append(normalized)

    @staticmethod
    def merge_hook_reasons(current: str, extra: str) -> str:
        parts = [part.strip() for part in (current, extra) if str(part).strip()]
        return " | ".join(parts)

    def normalize_hook_decision_scope(
        self,
        *,
        event: HookEvent,
        hook_result: HookResult,
    ) -> HookResult:
        if hook_result.decision != HookDecision.ASK or event == HookEvent.PRE_TOOL_USE:
            return hook_result
        reason = self.merge_hook_reasons(
            hook_result.reason,
            f"事件 {event.value} 不支持 ASK，已降级为 CONTINUE",
        )
        logger.warning("Hook ASK 降级：event=%s reason=%s", event.value, reason)
        return HookResult(
            decision=HookDecision.CONTINUE,
            reason=reason,
            updated_input=hook_result.updated_input,
            additional_context=hook_result.additional_context,
            agent_action=hook_result.agent_action,
            raw_output=dict(hook_result.raw_output),
        )

    def apply_hook_agent_failure(
        self,
        *,
        hook_result: HookResult,
        action: HookAgentAction,
        message: str,
    ) -> HookResult:
        decision = hook_result.decision
        if action.on_failure == "deny":
            decision = HookDecision.DENY
        reason = self.merge_hook_reasons(hook_result.reason, message)
        return HookResult(
            decision=decision,
            reason=reason,
            updated_input=hook_result.updated_input,
            additional_context=hook_result.additional_context,
            agent_action=hook_result.agent_action,
            raw_output=dict(hook_result.raw_output),
        )

    async def apply_hook_agent_action(
        self,
        *,
        event: HookEvent,
        hook_result: HookResult,
        on_event: "EventCallback | None",
    ) -> HookResult:
        e = self._engine
        action = hook_result.agent_action
        if action is None:
            return hook_result

        task_text = action.task.strip()
        if not task_text:
            return hook_result

        if e._hook_agent_action_depth > 0:
            message = "agent hook 递归触发已被跳过"
            logger.warning("Hook agent action 递归保护触发：event=%s", event.value)
            return self.apply_hook_agent_failure(
                hook_result=hook_result,
                action=action,
                message=message,
            )

        picked_agent = self.normalize_skill_agent_name(action.agent_name)
        if not picked_agent:
            picked_agent = await e._auto_select_subagent(
                task=task_text,
                file_paths=[],
            )
        picked_agent = self.normalize_skill_agent_name(picked_agent) or "subagent"

        logger.info(
            "执行 hook agent action：event=%s agent=%s",
            event.value,
            picked_agent,
        )
        e._hook_agent_action_depth += 1
        try:
            sub_result = await e.run_subagent(
                agent_name=picked_agent,
                prompt=task_text,
                on_event=on_event,
            )
        except Exception as exc:  # noqa: BLE001
            message = f"agent hook 执行异常（{picked_agent}）：{exc}"
            logger.warning(message)
            return self.apply_hook_agent_failure(
                hook_result=hook_result,
                action=action,
                message=message,
            )
        finally:
            e._hook_agent_action_depth -= 1

        if not sub_result.success:
            message = f"agent hook 执行失败（{picked_agent}）：{sub_result.summary}"
            logger.warning(message)
            return self.apply_hook_agent_failure(
                hook_result=hook_result,
                action=action,
                message=message,
            )

        summary = (sub_result.summary or "").strip()
        additional_context = hook_result.additional_context
        if action.inject_summary_as_context and summary:
            injected = f"[Hook Agent:{picked_agent}] {summary}"
            additional_context = (
                f"{additional_context}\n{injected}"
                if additional_context
                else injected
            )
        return HookResult(
            decision=hook_result.decision,
            reason=hook_result.reason,
            updated_input=hook_result.updated_input,
            additional_context=additional_context,
            agent_action=hook_result.agent_action,
            raw_output=dict(hook_result.raw_output),
        )

    async def resolve_hook_result(
        self,
        *,
        event: HookEvent,
        hook_result: HookResult | None,
        on_event: "EventCallback | None",
    ) -> HookResult | None:
        if hook_result is None:
            return None
        normalized = self.normalize_hook_decision_scope(
            event=event,
            hook_result=hook_result,
        )
        resolved = await self.apply_hook_agent_action(
            event=event,
            hook_result=normalized,
            on_event=on_event,
        )

        before = (normalized.additional_context or "").strip()
        after = (resolved.additional_context or "").strip()
        if after:
            if before and after.startswith(before):
                delta = after[len(before) :].strip()
                if delta:
                    self.push_hook_context(delta)
            elif after != before:
                self.push_hook_context(after)
        return resolved

    def run_skill_hook(
        self,
        *,
        skill: "Skillpack | None",
        event: HookEvent,
        payload: dict[str, Any],
        tool_name: str = "",
    ):
        e = self._engine
        if skill is None:
            return None

        def _invoke(target_event: HookEvent, target_payload: dict[str, Any]):
            context = HookCallContext(
                event=target_event,
                skill_name=skill.name,
                payload=target_payload,
                tool_name=tool_name,
                full_access_enabled=e._full_access_enabled,
            )
            hook_result = e._hook_runner.run(skill=skill, context=context)
            if hook_result.additional_context:
                self.push_hook_context(hook_result.additional_context)
            return self.normalize_hook_decision_scope(
                event=target_event,
                hook_result=hook_result,
            )

        if event == HookEvent.SESSION_START:
            e._hook_started_skills.add(skill.name)
            return _invoke(event, payload)

        if skill.name not in e._hook_started_skills:
            start_result = _invoke(
                HookEvent.SESSION_START,
                {"trigger_event": event.value, **payload},
            )
            e._hook_started_skills.add(skill.name)
            if start_result is not None and start_result.decision == HookDecision.DENY:
                return start_result

        result = _invoke(event, payload)
        if event in {HookEvent.STOP, HookEvent.SESSION_END}:
            e._hook_started_skills.discard(skill.name)
        return result
