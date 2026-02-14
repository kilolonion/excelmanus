"""hooks.handlers 单元测试。"""

from __future__ import annotations

from excelmanus.config import ExcelManusConfig
from excelmanus.hooks.handlers import run_agent_handler, run_command_handler
from excelmanus.hooks.models import HookDecision


def _config(**overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def test_command_handler_respects_global_enabled_switch() -> None:
    config = _config(
        hooks_command_enabled=False,
        hooks_command_allowlist=("printf",),
    )
    result = run_command_handler(
        command='printf \'{"decision":"deny","reason":"blocked"}\'',
        payload={"x": 1},
        full_access_enabled=True,
        config=config,
    )
    assert result.decision == HookDecision.CONTINUE
    assert "已禁用" in result.reason


def test_command_handler_allowlist_allows_single_segment_command() -> None:
    config = _config(
        hooks_command_enabled=True,
        hooks_command_allowlist=("printf",),
    )
    result = run_command_handler(
        command='printf \'{"decision":"deny","reason":"blocked"}\'',
        payload={"x": 1},
        full_access_enabled=False,
        config=config,
    )
    assert result.decision == HookDecision.DENY
    assert result.reason == "blocked"


def test_command_handler_rejects_multi_segment_shell_chain() -> None:
    config = _config(
        hooks_command_enabled=True,
        hooks_command_allowlist=("printf",),
    )
    result = run_command_handler(
        command='printf \'{"decision":"deny","reason":"blocked"}\'; echo HACK',
        payload={"x": 1},
        full_access_enabled=False,
        config=config,
    )
    assert result.decision == HookDecision.CONTINUE
    assert "未获授权" in result.reason


def test_command_handler_parses_non_json_as_additional_context() -> None:
    config = _config(
        hooks_command_enabled=True,
        hooks_command_allowlist=("printf",),
        hooks_output_max_chars=5,
    )
    result = run_command_handler(
        command="printf hello-world",
        payload={},
        full_access_enabled=False,
        config=config,
    )
    assert result.decision == HookDecision.CONTINUE
    assert "非 JSON" in result.reason
    assert result.additional_context == "hello"


def test_agent_handler_builds_action_payload() -> None:
    result = run_agent_handler(
        config_map={
            "type": "agent",
            "decision": "continue",
            "agent_name": "explorer",
            "task": "检查风险点",
            "on_failure": "deny",
            "inject_summary_as_context": False,
        }
    )
    assert result.decision == HookDecision.CONTINUE
    assert result.agent_action is not None
    assert result.agent_action.agent_name == "explorer"
    assert result.agent_action.task == "检查风险点"
    assert result.agent_action.on_failure == "deny"
    assert result.agent_action.inject_summary_as_context is False


def test_agent_handler_supports_hook_specific_output_agent_action() -> None:
    result = run_agent_handler(
        config_map={
            "type": "agent",
            "hookSpecificOutput": {
                "agentAction": {
                    "agent_name": "explorer",
                    "task": "二次检查",
                    "on_failure": "continue",
                    "inject_summary_as_context": True,
                }
            },
        }
    )
    assert result.agent_action is not None
    assert result.agent_action.agent_name == "explorer"
    assert result.agent_action.task == "二次检查"
