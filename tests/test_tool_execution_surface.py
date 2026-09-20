"""统一执行不再暴露调用方式开关。"""

import inspect
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from excelmanus.agent.session import AgentEngine
from excelmanus.api_routes_chat import ChatRequest
from excelmanus.api_routes_config import RuntimeConfigUpdate
from excelmanus.api_routes_sessions import router
from excelmanus.config import ExcelManusConfig
from excelmanus.control_commands import NORMALIZED_ALIAS_TO_CANONICAL_CONTROL_COMMAND
from excelmanus.engine_core.command_handler import CommandHandler
from excelmanus.engine_core.session_state import SessionState
from excelmanus.stores.config_store import UserConfigStore
from excelmanus.system_one.policy import JevSettings


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/code", "/code on", "/code off", "/code status", "/code_mode"])
async def test_removed_commands_have_no_control_handler(command):
    assert await CommandHandler(SimpleNamespace()).handle(command) is None
    assert "code" not in NORMALIZED_ALIAS_TO_CANONICAL_CONTROL_COMMAND
    assert "codemode" not in NORMALIZED_ALIAS_TO_CANONICAL_CONTROL_COMMAND


@pytest.mark.parametrize("mode", ["native", "code", "both"])
def test_chat_request_has_no_call_syntax_setting(mode):
    assert "present_as" not in ChatRequest.model_fields
    with pytest.raises(ValidationError):
        ChatRequest(message="hello", present_as=mode)


def test_removed_api_route_and_state_are_absent():
    assert not any(route.path.endswith("/present-as") for route in router.routes)
    assert "present_as" not in inspect.signature(AgentEngine.followup).parameters
    assert "present_as" not in SessionState().to_dict()
    assert not hasattr(SessionState.from_dict({"present_as": "code"}), "present_as")
    assert not hasattr(UserConfigStore, "get_present_as")
    assert not hasattr(UserConfigStore, "set_present_as")
    assert "jev_present_as_auto" not in ExcelManusConfig.__dataclass_fields__
    assert "jev_present_as_auto" not in RuntimeConfigUpdate.model_fields
    assert "present_as_auto" not in JevSettings.__dataclass_fields__


@pytest.mark.parametrize("invalid_mode", ["code", "both", "unknown"])
@pytest.mark.parametrize("invalid_role", ["parent", "child"])
def test_child_permission_comparison_rejects_removed_or_unknown_modes(invalid_mode, invalid_role):
    from excelmanus.subagent.child import assert_child_capability_subset
    from excelmanus.subagent.errors import SubagentError
    from excelmanus.tools.context import CallerCapability

    parent = SimpleNamespace(_fixed_capability=CallerCapability(catalog_mode="write"))
    child = SimpleNamespace(_fixed_capability=CallerCapability(catalog_mode="read"))
    target = parent if invalid_role == "parent" else child
    target._fixed_capability = CallerCapability(catalog_mode=invalid_mode)
    with pytest.raises(SubagentError, match="read/plan/write"):
        assert_child_capability_subset(parent, child)
