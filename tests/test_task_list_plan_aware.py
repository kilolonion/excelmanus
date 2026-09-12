"""任务清单不再注入 system prompt。"""

from __future__ import annotations

from unittest.mock import MagicMock

from excelmanus.prompt.assemble import prepare_system_prompts_for_request
from excelmanus.task_list import TaskStore


def test_task_list_not_injected_into_system() -> None:
    store = TaskStore()
    store.create("测试计划", ["任务A", "任务B"])
    store.plan_file_path = "plans/plan.md"
    engine = MagicMock()
    engine.memory.system_prompt = "You are ExcelManus."
    engine._prompt_composer = None
    engine._transient_hook_contexts = []
    engine.full_access_enabled = False
    engine.max_context_tokens = 100000
    engine._effective_system_mode.return_value = "multi"
    engine.state.prompt_injection_snapshots = []
    engine.state.injected_context_fingerprint = None
    engine._task_store = store
    engine._current_chat_mode = "write"
    engine._present_as = "native"
    engine._runtime_vars = {"workspace_root": "/tmp/ws", "model": "test-model"}

    prompts, error = prepare_system_prompts_for_request(engine, [])
    assert error is None
    blob = "\n".join(prompts)
    assert "当前计划与任务清单" not in blob
    assert "📄 计划文档" not in blob
    assert "任务A" not in blob
