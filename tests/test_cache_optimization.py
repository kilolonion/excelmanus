"""Prompt cache 分层优化回归测试。

验证:
- system prompt 拆分为稳定前缀 + 动态后缀
- 稳定前缀在同一 session 内保持一致
- Claude cache_control breakpoint 放在第一个 system block
- chitchat 路由跳过工具 schema
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
    engine._effective_window_return_mode.return_value = "enriched"
    engine.state.prompt_injection_snapshots = []
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
    return engine


def _make_mock_cb(engine):
    """Helper: 创建 ContextBuilder 并 mock 所有 notice 方法返回空。"""
    from excelmanus.engine_core.context_builder import ContextBuilder
    cb = ContextBuilder(engine)
    for name in (
        "_build_rules_notice", "_build_channel_notice", "_build_access_notice",
        "_build_backup_notice", "_build_mcp_context_notice",
        "_build_file_registry_notice", "_build_memory_notice",
        "_build_playbook_notice", "_build_skill_hints_notice",
        "_build_meta_cognition_notice", "_build_verification_fix_notice",
        "_build_post_write_verification_hint", "_build_scan_tool_hint",
        "_build_explorer_report_notice", "_build_window_perception_notice",
    ):
        setattr(cb, name, lambda: "")
    return cb


def test_system_prompt_returns_multiple_blocks():
    """_prepare_system_prompts_for_request 应返回至少 2 个 system prompt
    （稳定前缀 + 动态后缀），以支持分层 cache。"""
    from unittest.mock import MagicMock

    from excelmanus.engine_core.context_builder import ContextBuilder

    engine = _make_mock_engine()

    cb = _make_mock_cb(engine)

    route_result = MagicMock()
    route_result.route_mode = "all_tools"
    route_result.system_contexts = []
    route_result.write_hint = "unknown"
    route_result.sheet_count = 0
    route_result.max_total_rows = 0
    route_result.task_tags = []

    prompts, error = cb._prepare_system_prompts_for_request(
        [], route_result=route_result,
    )
    assert error is None
    # 至少 2 个：stable_prompt + dynamic_prompt（含 runtime_metadata）
    assert len(prompts) >= 2, f"Expected >=2 prompts, got {len(prompts)}"
    # 第一个应包含 identity
    assert "ExcelManus" in prompts[0]
    # 第二个应包含 runtime metadata
    assert "Runtime:" in prompts[1]


def test_stable_prompt_consistency():
    """同一 session 连续两次调用，stable_prompt（第一个 block）内容完全相同。"""
    from unittest.mock import MagicMock

    engine = _make_mock_engine()
    cb = _make_mock_cb(engine)

    route = MagicMock()
    route.route_mode = "all_tools"
    route.system_contexts = []
    route.write_hint = "unknown"
    route.sheet_count = 0
    route.max_total_rows = 0
    route.task_tags = []

    prompts1, _ = cb._prepare_system_prompts_for_request([], route_result=route)
    # 模拟 session_turn 变化使 runtime_metadata 变化
    engine._session_turn = 2
    cb._turn_notice_cache.clear()
    cb._turn_notice_cache_key = -1
    prompts2, _ = cb._prepare_system_prompts_for_request([], route_result=route)

    # 稳定前缀应完全相同
    assert prompts1[0] == prompts2[0], "Stable prefix should be identical across turns"
    # 动态部分应不同（runtime_metadata 含 turn 号）
    assert prompts1[1] != prompts2[1], "Dynamic part should differ across turns"


def test_dynamic_prompt_independence():
    """修改 runtime_metadata 后，stable_prompt 不受影响。"""
    engine = _make_mock_engine()
    cb = _make_mock_cb(engine)

    stable1 = cb._build_stable_system_prompt()
    # 改变 iteration count（影响 runtime_metadata）
    engine.state.last_iteration_count = 5
    stable2 = cb._build_stable_system_prompt()

    assert stable1 == stable2, "Stable prompt must not change when runtime state changes"


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


# ── C. Chitchat 路由 ────────────────────────────────────────


def test_chitchat_regex_matches_greetings():
    """_CHITCHAT_RE 应匹配常见问候语。"""
    from excelmanus.skillpacks.router import _CHITCHAT_RE

    greetings = ["你好", "hello", "Hi", "嗨", "在吗", "你是谁", "帮助", "怎么用"]
    for g in greetings:
        assert _CHITCHAT_RE.match(g), f"Should match: {g!r}"


def test_chitchat_regex_no_match_for_tasks():
    """_CHITCHAT_RE 不应匹配任务型消息。"""
    from excelmanus.skillpacks.router import _CHITCHAT_RE

    tasks = [
        "帮我读取 A1 单元格",
        "创建一个新的工作表",
        "把第一列的数据排序",
    ]
    for t in tasks:
        assert not _CHITCHAT_RE.match(t), f"Should NOT match: {t!r}"


def test_chitchat_route_returns_no_tools_prompt():
    """chitchat route_mode 时，_prepare_system_prompts_for_request 仅返回 1 个 prompt。"""
    from unittest.mock import MagicMock

    engine = _make_mock_engine()
    cb = _make_mock_cb(engine)

    route = MagicMock()
    route.route_mode = "chitchat"

    prompts, error = cb._prepare_system_prompts_for_request([], route_result=route)
    assert error is None
    # chitchat 仅返回 stable_prompt
    assert len(prompts) == 1
    assert "ExcelManus" in prompts[0]
    # 不应包含 runtime metadata
    assert "Runtime:" not in prompts[0]


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
