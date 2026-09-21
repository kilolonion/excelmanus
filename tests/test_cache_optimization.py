"""Prompt cache 分层优化回归测试。

验证:
- system prompt 拆分为稳定前缀 + 动态后缀
- 稳定前缀在同一 session 内保持一致
- Claude cache_control breakpoint 放在第一个 system block
- 会话缓存只由正式模型请求建立
"""

from __future__ import annotations

from excelmanus.prompt.assemble import (
    build_stable_system_prompt,
    prepare_system_prompts_for_request,
)


def _make_mock_engine():
    """Helper: 创建带有所有必要属性的 mock engine。"""
    from unittest.mock import MagicMock
    engine = MagicMock()
    engine.memory.system_prompt = "You are ExcelManus."
    engine._session_turn = 1
    engine._prompt_composer = None
    engine._transient_hook_contexts = []
    engine.full_access_enabled = False
    engine.max_context_tokens = 100000
    engine.state.prompt_injection_snapshots = []
    engine.state.injected_context_fingerprint = None
    engine._task_store.current = None
    engine.state.last_iteration_count = 0
    engine.state.last_failure_count = 0
    engine.state.last_success_count = 0
    engine.state.has_write_tool_call = False
    engine.config.max_iterations = 20
    engine._active_skills = []
    engine._last_route_result = None
    engine._current_chat_mode = "write"
    engine._runtime_vars = {"workspace_root": "/tmp/ws", "model": "test-model"}
    return engine


def _route():
    from unittest.mock import MagicMock

    route = MagicMock()
    route.route_mode = "all_tools"
    route.system_contexts = []
    return route


def test_system_prompt_stable_only_when_snapshot_empty():
    """没有动态快照时只发稳定前缀，不再每步塞 Runtime 行。"""
    engine = _make_mock_engine()

    prompts, error = prepare_system_prompts_for_request(engine, [])
    assert error is None
    assert len(prompts) == 1
    assert "ExcelManus" in prompts[0]
    assert "Runtime:" not in prompts[0]


def test_stable_prompt_consistency():
    """同一 session 连续两次调用，稳定前缀不变；默认不再注入文件全景。"""
    engine = _make_mock_engine()
    panorama = "## 文件全景\nsales.xlsx | 1 sheet"

    prompts1, _ = prepare_system_prompts_for_request(engine, [])
    engine._session_turn = 2
    prompts2, _ = prepare_system_prompts_for_request(engine, [])

    assert prompts1[0] == prompts2[0]
    assert len(prompts1) == 1
    assert all(panorama not in block for block in prompts1)
    assert len(prompts2) == 1
    assert all(panorama not in block for block in prompts2)


def test_dynamic_prompt_independence():
    """改 iteration 不影响稳定前缀。"""
    engine = _make_mock_engine()

    stable1 = build_stable_system_prompt(engine)
    engine.state.last_iteration_count = 5
    stable2 = build_stable_system_prompt(engine)

    assert stable1 == stable2


def test_unchanged_panorama_not_reinjected():
    """文件全景不再进入默认 system；两步前缀字节相同。"""
    engine = _make_mock_engine()
    panorama = "## 文件全景\nworkbook.xlsx | sheets=3 | rows=1200"
    route = _route()

    first, err1 = prepare_system_prompts_for_request(engine, [])
    second, err2 = prepare_system_prompts_for_request(engine, [])
    assert err1 is None and err2 is None
    assert all(panorama not in block for block in first)
    assert all(panorama not in block for block in second)
    assert first[0] == second[0]

    third, err3 = prepare_system_prompts_for_request(engine, [])
    assert err3 is None
    assert all("changed" not in block for block in third)
    assert third[0] == first[0]


def test_plan_mode_injects_policy_then_skips():
    """plan 只改 order 50 前缀；同一 plan 快照两步字节相同。"""
    from pathlib import Path

    from excelmanus.prompt.load import PromptComposer

    engine = _make_mock_engine()
    composer = PromptComposer(Path(__file__).resolve().parent.parent / "excelmanus" / "prompts")
    composer.load_all()
    engine._prompt_composer = composer
    engine._runtime_vars = {"workspace_root": "/tmp/ws", "model": "test-model"}
    engine._current_chat_mode = "write"
    route = _route()

    write_prompts, _ = prepare_system_prompts_for_request(engine, [])
    assert all("当前是计划模式" not in block for block in write_prompts)
    write_prefix = write_prompts[0]

    engine._current_chat_mode = "plan"
    plan_prompts, _ = prepare_system_prompts_for_request(engine, [])
    assert any("当前是计划模式" in block for block in plan_prompts)
    assert any("write_plan" in block for block in plan_prompts)
    assert any("exit_plan_mode" in block for block in plan_prompts)
    plan_prefix = plan_prompts[0]
    assert plan_prefix != write_prefix

    again, _ = prepare_system_prompts_for_request(engine, [])
    assert again[0] == plan_prefix
    assert "当前是计划模式" in again[0]


def test_hook_injects_without_repeating_panorama():
    """一次性 hook 进入 durable 尾部，不进 system，也不插在历史前。"""
    from excelmanus.config import ExcelManusConfig
    from excelmanus.memory import ConversationMemory
    from excelmanus.prompt.envelope import flush_dynamic_contexts

    engine = _make_mock_engine()
    engine.memory = ConversationMemory(
        ExcelManusConfig(api_key="t", base_url="https://x", model="m")
    )
    engine._memory = engine.memory
    panorama = "## 文件全景\nledger.xlsx"

    first, _ = prepare_system_prompts_for_request(engine, [])
    assert all(panorama not in block for block in first)
    engine._transient_hook_contexts = ["审批已通过"]
    second, _ = prepare_system_prompts_for_request(engine, [])
    assert len(second) == 1
    assert all(panorama not in block for block in second)
    assert any("审批已通过" in ctx for ctx in engine._prompt_user_contexts)
    flush_dynamic_contexts(engine)
    assert any(
        "审批已通过" in str(m.get("content", ""))
        for m in engine.memory.messages
    )


def test_claude_cache_breakpoint_on_first_block():
    """映射层拒绝 mid-history system；单条 leading system 仍钉 cache_control。"""
    import pytest
    from excelmanus.providers.claude import _openai_messages_to_claude

    with pytest.raises(ValueError, match="mid-history system is not representable"):
        _openai_messages_to_claude([
            {"role": "system", "content": "Stable prefix content"},
            {"role": "system", "content": "Dynamic content with runtime data"},
            {"role": "user", "content": "hello"},
        ])
    system, _ = _openai_messages_to_claude([
        {"role": "system", "content": "Stable prefix content"},
        {"role": "user", "content": "hello"},
    ])
    assert isinstance(system, list)
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_claude_last_tool_has_cache_control():
    from excelmanus.providers.claude import _openai_tools_to_claude

    tools = [
        {"type": "function", "function": {"name": "a", "description": "A", "parameters": {}}},
        {"type": "function", "function": {"name": "b", "description": "B", "parameters": {}}},
    ]
    claude = _openai_tools_to_claude(tools)
    assert claude is not None
    assert "cache_control" not in claude[0]
    assert claude[-1]["cache_control"] == {"type": "ephemeral"}


def test_claude_single_block_has_cache_control():
    """单个 system block 时，该 block 上有 cache_control。"""
    from excelmanus.providers.claude import _openai_messages_to_claude

    messages = [
        {"role": "system", "content": "Single system prompt"},
        {"role": "user", "content": "hello"},
    ]
    system, _ = _openai_messages_to_claude(messages)

    assert isinstance(system, list)
    assert len(system) == 1
    assert "cache_control" in system[0]
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_claude_breakpoints_advance_with_history() -> None:
    from excelmanus.providers.claude import _openai_messages_to_claude
    rows = [{"role": "system", "content": "Stable"}]
    for i in range(30):
        rows.extend([{"role": "user", "content": str(i)}, {"role": "assistant", "content": "ok"}])
    _, messages = _openai_messages_to_claude(rows)
    marked = [i for i, row in enumerate(messages) if isinstance(row["content"], list)
              and any("cache_control" in b for b in row["content"])]
    assert marked == [56, 58]
