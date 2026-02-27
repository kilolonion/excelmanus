"""hooks.runner 单元测试。"""

from __future__ import annotations

from excelmanus.config import ExcelManusConfig
from excelmanus.hooks.models import HookCallContext, HookDecision, HookEvent
from excelmanus.hooks.runner import SkillHookRunner
from excelmanus.skillpacks.models import Skillpack


def _config() -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
    )


def _skill_with_hooks(hooks: dict) -> Skillpack:
    return Skillpack(
        name="hook/test",
        description="test",
        instructions="",
        source="project",
        root_dir="/tmp/hook",
        hooks=hooks,
    )


def test_runner_supports_pascal_camel_and_snake_event_keys() -> None:
    runner = SkillHookRunner(_config())
    for key in ("PreToolUse", "preToolUse", "pre_tool_use"):
        skill = _skill_with_hooks({
            key: {"type": "prompt", "decision": "deny", "reason": key}
        })
        result = runner.run(
            skill=skill,
            context=HookCallContext(
                event=HookEvent.PRE_TOOL_USE,
                skill_name=skill.name,
                payload={},
                tool_name="add_numbers",
            ),
        )
        assert result.decision == HookDecision.DENY
        assert result.reason == key


def test_runner_applies_decision_priority() -> None:
    runner = SkillHookRunner(_config())
    skill = _skill_with_hooks({
        "PreToolUse": [
            {
                "hooks": [
                    {"type": "prompt", "decision": "allow", "reason": "allow"},
                    {"type": "prompt", "decision": "deny", "reason": "deny"},
                ]
            }
        ]
    })
    result = runner.run(
        skill=skill,
        context=HookCallContext(
            event=HookEvent.PRE_TOOL_USE,
            skill_name=skill.name,
            payload={},
            tool_name="add_numbers",
        ),
    )
    assert result.decision == HookDecision.DENY
    assert "allow" in result.reason and "deny" in result.reason


def test_runner_respects_matcher_glob() -> None:
    runner = SkillHookRunner(_config())
    skill = _skill_with_hooks({
        "PreToolUse": [
            {
                "matcher": "read_*",
                "hooks": [{"type": "prompt", "decision": "deny", "reason": "blocked"}],
            }
        ]
    })
    result = runner.run(
        skill=skill,
        context=HookCallContext(
            event=HookEvent.PRE_TOOL_USE,
            skill_name=skill.name,
            payload={},
            tool_name="add_numbers",
        ),
    )
    assert result.decision == HookDecision.CONTINUE


def test_runner_keeps_agent_action_when_later_handler_has_no_action() -> None:
    runner = SkillHookRunner(_config())
    skill = _skill_with_hooks({
        "PreToolUse": [
            {
                "hooks": [
                    {"type": "agent", "task": "检查参数"},
                    {"type": "prompt", "decision": "deny", "reason": "blocked"},
                ]
            }
        ]
    })
    result = runner.run(
        skill=skill,
        context=HookCallContext(
            event=HookEvent.PRE_TOOL_USE,
            skill_name=skill.name,
            payload={},
            tool_name="add_numbers",
        ),
    )
    assert result.decision == HookDecision.DENY
    assert result.agent_action is not None
    assert result.agent_action.task == "检查参数"
