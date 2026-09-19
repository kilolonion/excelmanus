"""发送路径不再滑窗改写已发出的 tool result。"""

from __future__ import annotations

import importlib

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.memory import ConversationMemory


def test_observation_masker_module_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("excelmanus.engine_core.observation_masker")


def test_project_for_request_does_not_mask_old_tool_results() -> None:
    mem = ConversationMemory(
        ExcelManusConfig(api_key="t", base_url="https://x", model="m")
    )
    long_result = "FULL-" + ("Z" * 400)
    mem.add_user_message("one")
    mem.add_tool_call("c1", "inspect_spreadsheet", "{}")
    mem.add_tool_result("c1", long_result)
    for i in range(5):
        mem.add_user_message(f"u{i}")
        mem.add_assistant_message(f"a{i}")
    msgs = mem.project_for_request(system_prompts=["sys"])
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert tool_msgs[0]["content"] == long_result
