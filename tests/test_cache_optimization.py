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


def test_warmup_skips_without_real_user():
    """无真实 user 时不得发送假 hi。"""
    import asyncio
    from unittest.mock import AsyncMock

    from excelmanus.engine import AgentEngine
    from excelmanus.providers.claude import ClaudeClient

    engine = AgentEngine.__new__(AgentEngine)
    real_client = ClaudeClient(api_key="test", base_url="http://localhost")
    mock_create = AsyncMock()
    real_client.chat.completions.create = mock_create
    engine._client = real_client
    engine._memory = type("M", (), {"messages": []})()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(AgentEngine.warmup_prompt_cache(engine))
    finally:
        loop.close()
    mock_create.assert_not_called()


def test_warmup_sends_compiled_body_not_fake_hi():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from excelmanus.engine import AgentEngine
    from excelmanus.providers.claude import ClaudeClient
    from excelmanus.request.types import PreparedRequest, RequestHeader, ResolvedRoute

    engine = AgentEngine.__new__(AgentEngine)
    real_client = ClaudeClient(api_key="test", base_url="http://localhost")
    mock_create = AsyncMock()
    real_client.chat.completions.create = mock_create
    engine._client = real_client
    engine._memory = type("M", (), {"messages": [{"role": "user", "content": "real"}]})()
    header = RequestHeader(
        route_fingerprint="r",
        tools_digest="t",
        catalog_digest="c",
        system_head_digest="s",
        content_identity="id",
        content_payload="p",
        cache_policy_digest="cp",
        transport="inline",
        prompt_cache_key="em_key",
    )
    route = ResolvedRoute(
        session_id="s",
        model="claude-sonnet-4-6",
        protocol="anthropic",
        endpoint="https://api.anthropic.com",
        credential_scope="scope",
        api_key="k",
    )
    prepared = PreparedRequest(
        request_id="rid",
        series_id="sid",
        attempt=1,
        header=header,
        route=route,
        provider_body={
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "system", "content": "A" * 200},
                {"role": "user", "content": "real"},
            ],
            "tools": [{"type": "function", "function": {"name": "run_code"}}],
            "prompt_cache_key": "em_key",
        },
        file_leases=(),
        compiled_at=0.0,
    )

    async def _fake_compile(*_a, **_k):
        return prepared, None

    loop = asyncio.new_event_loop()
    try:
        with patch("excelmanus.request.compiler.compile_request", _fake_compile):
            loop.run_until_complete(AgentEngine.warmup_prompt_cache(engine))
    finally:
        loop.close()
    mock_create.assert_called_once()
    sent = mock_create.call_args.kwargs or mock_create.call_args[1]
    # anthropic 等 native 协议下编译体经 _prepared_body 传输，messages 顶层为空。
    body = sent.get("_prepared_body") or sent
    sent_messages = body.get("messages") or sent.get("messages") or []
    assert all(m.get("content") != "hi" for m in sent_messages if isinstance(m, dict))
    assert any(m.get("content") == "real" for m in sent_messages if isinstance(m, dict))


def test_claude_first_user_breakpoint_stays_put() -> None:
    """第一条 user 钉 cache_control；后续轮次不得改历史 user 的结构。"""
    from excelmanus.providers.claude import _openai_messages_to_claude

    first_turn = [
        {"role": "system", "content": "Stable"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "next"},
    ]
    _, first_msgs = _openai_messages_to_claude(first_turn)
    second_turn = [
        *first_turn,
        {"role": "assistant", "content": "still"},
        {"role": "user", "content": "again"},
    ]
    _, second_msgs = _openai_messages_to_claude(second_turn)

    def _user_texts(msgs: list) -> list:
        out = []
        for msg in msgs:
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            out.append(content)
        return out

    first_users = _user_texts(first_msgs)
    second_users = _user_texts(second_msgs)
    assert first_users[0] == second_users[0]
    assert isinstance(first_users[0], list)
    assert first_users[0][-1].get("cache_control") == {"type": "ephemeral"}
    later = second_users[1]
    if isinstance(later, list):
        assert "cache_control" not in later[-1]
    else:
        assert later == "next"
