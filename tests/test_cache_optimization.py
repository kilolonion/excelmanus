"""Prompt cache 分层优化回归测试。

验证:
- system prompt 拆分为稳定前缀 + 动态后缀
- 稳定前缀在同一 session 内保持一致
- Claude cache_control breakpoint 放在第一个 system block
- cache 预热仅对 ClaudeClient 触发
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
    engine._effective_system_mode.return_value = "multi"
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
    engine._present_as = "native"
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
    """一次性 hook 只追加 hook 段；文件全景不进默认 system。"""
    engine = _make_mock_engine()
    panorama = "## 文件全景\nledger.xlsx"
    route = _route()

    first, _ = prepare_system_prompts_for_request(engine, [])
    assert all(panorama not in block for block in first)
    engine._transient_hook_contexts = ["审批已通过"]
    second, _ = prepare_system_prompts_for_request(engine, [])
    assert len(second) == 2
    assert panorama not in second[1]
    assert "审批已通过" in second[1]


def test_claude_cache_breakpoint_on_first_block():
    """多个 system block 时，cache_control 应在第一个 block 上。"""
    from excelmanus.providers.claude import _openai_messages_to_claude

    messages = [
        {"role": "system", "content": "Stable prefix content"},
        {"role": "system", "content": "Dynamic content with runtime data"},
        {"role": "user", "content": "hello"},
    ]
    system, claude_msgs = _openai_messages_to_claude(messages)

    assert isinstance(system, list), f"Expected list, got {type(system)}"
    assert len(system) == 2

    assert "cache_control" in system[0], "First block should have cache_control"
    assert system[0]["cache_control"] == {"type": "ephemeral"}

    assert "cache_control" not in system[1], "Second block should NOT have cache_control"


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


def test_warmup_skips_non_claude_client():
    """非 ClaudeClient 时 warmup_prompt_cache 应静默跳过。"""
    import asyncio
    from unittest.mock import MagicMock

    engine = MagicMock()
    engine._client = MagicMock()  # 非 ClaudeClient

    from excelmanus.engine import AgentEngine
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(AgentEngine.warmup_prompt_cache(engine))
    finally:
        loop.close()
    engine._build_stable_system_prompt.assert_not_called()


def test_warmup_fires_for_claude_client():
    """ClaudeClient 时 warmup_prompt_cache 应发送预热请求。"""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from excelmanus.providers.claude import ClaudeClient

    engine = MagicMock()
    real_client = ClaudeClient(api_key="test", base_url="http://localhost")
    mock_create = AsyncMock()
    real_client.chat.completions.create = mock_create
    engine._client = real_client
    engine._active_model = "claude-sonnet-4-6"
    engine._build_stable_system_prompt.return_value = "A" * 200

    from excelmanus.engine import AgentEngine
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(AgentEngine.warmup_prompt_cache(engine))
    finally:
        loop.close()
    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args
    messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
    assert any(m["role"] == "system" for m in messages)
    assert any(m["role"] == "user" for m in messages)
