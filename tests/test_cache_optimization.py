"""Prompt cache 分层优化回归测试。

验证:
- system prompt 拆分为稳定前缀 + 动态后缀
- 稳定前缀在同一 session 内保持一致
- Claude cache_control breakpoint 放在第一个 system block
- cache 预热仅对 ClaudeClient 触发
"""

from __future__ import annotations

import re


# ── A. system prompt 拆分 ──────────────────────────────────


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
    engine.state.execution_guard_fired = False
    engine.state.has_write_tool_call = False
    engine.state.silent_call_count = 0
    engine.state.reasoned_call_count = 0
    engine.state.reasoning_level_mismatch_count = 0
    engine.config.max_iterations = 20
    engine._active_skills = []
    engine._last_route_result = None
    engine._current_chat_mode = "write"
    return engine


def _make_mock_cb(engine):
    """Helper: 创建 ContextBuilder 并 mock 所有 notice 方法返回空。"""
    from excelmanus.engine_core.context_builder import ContextBuilder
    cb = ContextBuilder(engine)
    for name in (
        "_build_rules_notice", "_build_channel_notice", "_build_access_notice",
        "_build_backup_notice", "_build_mcp_context_notice",
        "_build_file_registry_notice",
    ):
        setattr(cb, name, lambda: "")
    return cb


def _route():
    from unittest.mock import MagicMock

    route = MagicMock()
    route.route_mode = "all_tools"
    route.system_contexts = []
    return route


def test_system_prompt_stable_only_when_snapshot_empty():
    """没有动态快照时只发稳定前缀，不再每步塞 Runtime 行。"""
    engine = _make_mock_engine()
    cb = _make_mock_cb(engine)

    prompts, error = cb._prepare_system_prompts_for_request([], route_result=_route())
    assert error is None
    assert len(prompts) == 1
    assert "ExcelManus" in prompts[0]
    assert "Runtime:" not in prompts[0]


def test_stable_prompt_consistency():
    """同一 session 连续两次调用，稳定前缀不变；快照未变则不再追加动态块。"""
    engine = _make_mock_engine()
    cb = _make_mock_cb(engine)
    panorama = "## 文件全景\nsales.xlsx | 1 sheet"
    cb._build_file_registry_notice = lambda: panorama

    prompts1, _ = cb._prepare_system_prompts_for_request([], route_result=_route())
    engine._session_turn = 2
    cb._turn_notice_cache.clear()
    cb._turn_notice_cache_key = -1
    prompts2, _ = cb._prepare_system_prompts_for_request([], route_result=_route())

    assert prompts1[0] == prompts2[0]
    assert len(prompts1) == 2
    assert panorama in prompts1[1]
    assert len(prompts2) == 1
    assert all(panorama not in block for block in prompts2)


def test_dynamic_prompt_independence():
    """改 iteration 不影响稳定前缀。"""
    engine = _make_mock_engine()
    cb = _make_mock_cb(engine)

    stable1 = cb._build_stable_system_prompt()
    engine.state.last_iteration_count = 5
    stable2 = cb._build_stable_system_prompt()

    assert stable1 == stable2


def test_unchanged_panorama_not_reinjected():
    """连续两步文件全景未变时，第二步请求不再重复那一段。"""
    engine = _make_mock_engine()
    cb = _make_mock_cb(engine)
    panorama = "## 文件全景\nworkbook.xlsx | sheets=3 | rows=1200"
    cb._build_file_registry_notice = lambda: panorama
    route = _route()

    first, err1 = cb._prepare_system_prompts_for_request([], route_result=route)
    second, err2 = cb._prepare_system_prompts_for_request([], route_result=route)
    assert err1 is None and err2 is None
    assert any(panorama in block for block in first)
    assert all(panorama not in block for block in second)
    assert engine.state.injected_context_fingerprint

    cb._build_file_registry_notice = lambda: panorama + "\n# changed"
    third, err3 = cb._prepare_system_prompts_for_request([], route_result=route)
    assert err3 is None
    assert any("changed" in block for block in third)


def test_plan_mode_injects_policy_then_skips():
    """plan 只靠策略段，不靠路由标签；同一 plan 快照第二步不再重注。"""
    from pathlib import Path

    from excelmanus.prompt_composer import PromptComposer

    engine = _make_mock_engine()
    composer = PromptComposer(Path("excelmanus/prompts"))
    composer.load_all()
    engine._prompt_composer = composer
    engine._current_chat_mode = "write"
    cb = _make_mock_cb(engine)
    route = _route()

    write_prompts, _ = cb._prepare_system_prompts_for_request([], route_result=route)
    assert all("## Plan mode" not in block for block in write_prompts)

    engine._current_chat_mode = "plan"
    plan_prompts, _ = cb._prepare_system_prompts_for_request([], route_result=route)
    assert any("## Plan mode" in block for block in plan_prompts)
    assert any("write_plan" in block for block in plan_prompts)

    again, _ = cb._prepare_system_prompts_for_request([], route_result=route)
    assert all("## Plan mode" not in block for block in again)


def test_hook_injects_without_repeating_panorama():
    """一次性 hook 只追加 hook 段，不把未变的全景再发一遍。"""
    engine = _make_mock_engine()
    cb = _make_mock_cb(engine)
    panorama = "## 文件全景\nledger.xlsx"
    cb._build_file_registry_notice = lambda: panorama
    route = _route()

    first, _ = cb._prepare_system_prompts_for_request([], route_result=route)
    assert panorama in first[1]
    engine._transient_hook_contexts = ["审批已通过"]
    second, _ = cb._prepare_system_prompts_for_request([], route_result=route)
    assert len(second) == 2
    assert panorama not in second[1]
    assert "审批已通过" in second[1]


# ── B. Claude cache_control breakpoint 位置 ─────────────────


def test_claude_cache_breakpoint_on_first_block():
    """多个 system block 时，cache_control 应在第一个 block 上。"""
    from excelmanus.providers.claude import _openai_messages_to_claude

    messages = [
        {"role": "system", "content": "Stable prefix content"},
        {"role": "system", "content": "Dynamic content with runtime data"},
        {"role": "user", "content": "hello"},
    ]
    system, claude_msgs = _openai_messages_to_claude(messages)

    # system 应是 list（多个 block）
    assert isinstance(system, list), f"Expected list, got {type(system)}"
    assert len(system) == 2

    # 第一个 block 有 cache_control
    assert "cache_control" in system[0], "First block should have cache_control"
    assert system[0]["cache_control"] == {"type": "ephemeral"}

    # 第二个 block 无 cache_control
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


# ── D. Cache 预热 ───────────────────────────────────────────


def test_warmup_skips_non_claude_client():
    """非 ClaudeClient 时 warmup_prompt_cache 应静默跳过。"""
    import asyncio
    from unittest.mock import MagicMock

    engine = MagicMock()
    engine._client = MagicMock()  # 非 ClaudeClient
    engine._context_builder = MagicMock()

    from excelmanus.engine import AgentEngine
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(AgentEngine.warmup_prompt_cache(engine))
    finally:
        loop.close()
    # 不应调用 _build_stable_system_prompt（因为 isinstance 检查失败）
    engine._context_builder._build_stable_system_prompt.assert_not_called()


def test_warmup_fires_for_claude_client():
    """ClaudeClient 时 warmup_prompt_cache 应发送预热请求。"""
    import asyncio
    from unittest.mock import MagicMock, AsyncMock

    from excelmanus.providers.claude import ClaudeClient

    engine = MagicMock()
    # 创建一个真实的 ClaudeClient 实例，然后 mock 其方法
    real_client = ClaudeClient(api_key="test", base_url="http://localhost")
    mock_create = AsyncMock()
    real_client.chat.completions.create = mock_create
    engine._client = real_client
    engine._active_model = "claude-sonnet-4-6"
    engine._context_builder._build_stable_system_prompt.return_value = "A" * 200

    from excelmanus.engine import AgentEngine
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(AgentEngine.warmup_prompt_cache(engine))
    finally:
        loop.close()
    # 应调用 chat.completions.create
    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args
    messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
    # 应包含 system 和 user 消息
    assert any(m["role"] == "system" for m in messages)
    assert any(m["role"] == "user" for m in messages)
