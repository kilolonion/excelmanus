"""Skill Hook 执行器。"""

from __future__ import annotations

from typing import Any

from excelmanus.config import ExcelManusConfig
from excelmanus.hooks.handlers import (
    run_agent_handler,
    run_command_handler,
    run_prompt_handler,
)
from excelmanus.hooks.matcher import match_tool
from excelmanus.hooks.models import HookCallContext, HookDecision, HookEvent, HookResult
from excelmanus.logger import get_logger
from excelmanus.skillpacks.models import Skillpack

logger = get_logger("hooks.runner")


_DECISION_PRIORITY = {
    HookDecision.DENY: 4,
    HookDecision.ASK: 3,
    HookDecision.ALLOW: 2,
    HookDecision.CONTINUE: 1,
}


class SkillHookRunner:
    """按技能定义执行 hooks。"""

    def __init__(self, config: ExcelManusConfig) -> None:
        self._config = config

    def run(self, *, skill: Skillpack, context: HookCallContext) -> HookResult:
        hooks_root = skill.hooks
        if not hooks_root:
            return HookResult()

        event_rules: Any | None = None
        matched_event_key: str | None = None
        for event_key in self._event_lookup_keys(context.event):
            if event_key in hooks_root:
                event_rules = hooks_root.get(event_key)
                matched_event_key = event_key
                break
        if event_rules is None:
            return HookResult()
        logger.debug(
            "命中 hook 事件：skill=%s event=%s key=%s",
            skill.name,
            context.event.value,
            matched_event_key,
        )

        rules = self._normalize_rules(event_rules)
        final = HookResult()

        for rule in rules:
            matcher = rule.get("matcher")
            if context.tool_name and isinstance(matcher, str):
                if not match_tool(matcher, context.tool_name):
                    logger.debug(
                        "hook matcher 未命中：skill=%s event=%s matcher=%s tool=%s",
                        skill.name,
                        context.event.value,
                        matcher,
                        context.tool_name,
                    )
                    continue

            for handler in self._extract_handlers(rule):
                result = self._run_single_handler(
                    handler=handler,
                    payload=context.payload,
                    full_access_enabled=context.full_access_enabled,
                )
                logger.debug(
                    "hook handler 已执行：skill=%s event=%s type=%s decision=%s",
                    skill.name,
                    context.event.value,
                    str(handler.get("type", "")),
                    result.decision.value,
                )
                final = self._merge_result(final, result)

        return final

    @staticmethod
    def _event_lookup_keys(event: HookEvent) -> list[str]:
        """支持 PascalCase / lowerCamelCase / snake_case 三种事件键。"""
        pascal = event.value
        lower_camel = pascal[0].lower() + pascal[1:] if pascal else pascal
        snake = SkillHookRunner._to_snake_case(pascal)
        ordered = [pascal, lower_camel, snake]
        deduped: list[str] = []
        for item in ordered:
            if item and item not in deduped:
                deduped.append(item)
        return deduped

    @staticmethod
    def _to_snake_case(name: str) -> str:
        if not name:
            return name
        chars: list[str] = []
        for idx, ch in enumerate(name):
            if ch.isupper() and idx > 0:
                prev = name[idx - 1]
                next_char = name[idx + 1] if idx + 1 < len(name) else ""
                if prev.islower() or (next_char and next_char.islower()):
                    chars.append("_")
            chars.append(ch.lower())
        return "".join(chars)

    @staticmethod
    def _normalize_rules(raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, list):
            if raw and all(isinstance(item, dict) and "type" in item for item in raw):
                return [{"hooks": raw}]
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            if "hooks" in raw:
                return [raw]
            if "type" in raw:
                return [{"hooks": [raw]}]
            return [raw]
        return []

    @staticmethod
    def _extract_handlers(rule: dict[str, Any]) -> list[dict[str, Any]]:
        handlers = rule.get("hooks")
        if handlers is None:
            if "type" in rule:
                return [rule]
            return []
        if isinstance(handlers, list):
            return [item for item in handlers if isinstance(item, dict)]
        if isinstance(handlers, dict):
            return [handlers]
        return []

    def _run_single_handler(
        self,
        *,
        handler: dict[str, Any],
        payload: dict[str, Any],
        full_access_enabled: bool,
    ) -> HookResult:
        kind = str(handler.get("type", "")).strip().lower()
        if kind == "command":
            command = str(handler.get("command", "") or "")
            return run_command_handler(
                command=command,
                payload=payload,
                full_access_enabled=full_access_enabled,
                config=self._config,
            )
        if kind == "prompt":
            return run_prompt_handler(config_map=handler)
        if kind == "agent":
            return run_agent_handler(config_map=handler)
        if kind:
            return HookResult(
                decision=HookDecision.CONTINUE,
                reason=f"不支持的 hook 类型: {kind}",
            )
        return HookResult()

    @staticmethod
    def _merge_result(current: HookResult, incoming: HookResult) -> HookResult:
        decision = current.decision
        if _DECISION_PRIORITY[incoming.decision] > _DECISION_PRIORITY[decision]:
            decision = incoming.decision

        reason_parts = [part for part in [current.reason, incoming.reason] if part]
        reason = " | ".join(reason_parts)

        additional = current.additional_context
        if incoming.additional_context:
            additional = (
                f"{additional}\n{incoming.additional_context}"
                if additional
                else incoming.additional_context
            )

        updated_input = incoming.updated_input if incoming.updated_input is not None else current.updated_input
        agent_action = incoming.agent_action if incoming.agent_action is not None else current.agent_action
        raw_output = dict(current.raw_output)
        if incoming.raw_output:
            raw_output.update(incoming.raw_output)

        return HookResult(
            decision=decision,
            reason=reason,
            updated_input=updated_input,
            additional_context=additional,
            agent_action=agent_action,
            raw_output=raw_output,
        )
