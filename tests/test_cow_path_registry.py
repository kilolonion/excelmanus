"""Overlay / CoW helpers are gone. cow_mapping is stripped from tool results."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from excelmanus.engine_core.session_state import SessionState
from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
from excelmanus.prompt.assemble import prepare_system_prompts_for_request


def test_session_state_has_no_cow_registry() -> None:
    state = SessionState()
    assert not hasattr(state, "register_cow_mappings")
    assert not hasattr(state, "get_cow_mappings")
    assert not hasattr(state, "lookup_cow_redirect")


def test_dispatcher_has_no_cow_redirect() -> None:
    engine = MagicMock()
    engine._state = SessionState()
    dispatcher = ToolDispatcher(engine)
    assert not hasattr(dispatcher, "_redirect_cow_paths")
    assert not hasattr(dispatcher, "_register_cow_mapping")


def test_cow_mapping_stripped_from_tool_result() -> None:
    engine = MagicMock()
    engine._state = SessionState()
    dispatcher = ToolDispatcher(engine)
    result = json.dumps({
        "status": "success",
        "cow_mapping": {"bench/external/data.xlsx": "outputs/data.xlsx"},
    })
    tr = dispatcher._coerce_tool_result(result)
    assert "cow_mapping" not in tr.model_text
    assert not hasattr(tr.ui_meta, "cow_mapping")


def test_cow_mapping_not_injected_into_system() -> None:
    engine = MagicMock()
    engine.memory.system_prompt = "You are ExcelManus."
    engine._prompt_composer = None
    engine._transient_hook_contexts = []
    engine.full_access_enabled = False
    engine.max_context_tokens = 100000
    engine.state.prompt_injection_snapshots = []
    engine.state.injected_context_fingerprint = None
    engine._current_chat_mode = "write"
    engine._present_as = "native"
    engine._runtime_vars = {"workspace_root": "/tmp/ws", "model": "test-model"}
    prompts, error = prepare_system_prompts_for_request(engine, [])
    assert error is None
    blob = "\n".join(prompts)
    assert "文件保护路径映射" not in blob
    assert "严禁访问原始路径" not in blob
    assert "cow_mapping" not in blob
