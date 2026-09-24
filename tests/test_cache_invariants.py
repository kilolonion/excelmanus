"""缓存前缀不变量：请求是 durable 历史的纯函数投影，出网参数可字节比对。

现有 tests/test_request_envelope.py 用信封自我比较覆盖单调前缀；
本文件升级为：① 由 durable 独立重构 wire；② 假 client 捕获真实 create 参数；
③ 伪造 usage 走 loop 命中率日志 / 连续 miss 告警。
"""

from __future__ import annotations

import copy
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from excelmanus.agent.loop import run_tool_loop
from excelmanus.attachments.project import strip_projection_meta
from excelmanus.config import ExcelManusConfig
from excelmanus.engine_core.llm_caller import LLMCaller
from excelmanus.engine_types import ThinkingConfig
from excelmanus.engine_utils import (
    _extract_anthropic_cache_tokens,
    _extract_cached_tokens,
)
from excelmanus.memory import ConversationMemory
from excelmanus.prompt.envelope import assemble_envelope, seal_envelope


def _config(**kwargs: object) -> ExcelManusConfig:
    values = {"api_key": "t", "base_url": "https://x.example/v1", "model": "test-model"}
    values.update(kwargs)
    return ExcelManusConfig(**values)


def _engine(*, session_id: str = "sess-1") -> MagicMock:
    """与 tests/test_request_envelope.py::_engine 同源形状，避免造重型 AgentEngine。"""
    engine = MagicMock()
    engine._session_id = session_id
    engine._session_turn = 1
    engine._prompt_composer = None
    engine._transient_hook_contexts = []
    engine._mention_contexts = []
    engine._mention_flush_digest = None
    engine._prompt_user_contexts = []
    engine._prompt_tool_snapshot = [
        {
            "type": "function",
            "function": {"name": "observe_spreadsheet", "description": "d", "parameters": {}},
        },
        {
            "type": "function",
            "function": {"name": "run_code", "description": "d", "parameters": {}},
        },
    ]
    engine._current_chat_mode = "write"
    engine._compaction_generation = 0
    engine._projection_generation = 0
    engine._image_wire_pin_seq = ()
    engine._files_wire_mode = None
    engine._last_wire_messages = None
    engine._last_envelope = None
    engine._last_system_msgs = None
    engine._client = None
    engine._active_skills = []
    engine._runtime_vars = {"workspace_root": "/tmp/ws", "model": "test-model"}
    engine.max_context_tokens = 128000
    engine._is_vision_capable = False
    engine.memory = ConversationMemory(_config())
    engine.memory.system_prompt = "You are ExcelManus."
    engine._memory = engine.memory
    engine.state = SimpleNamespace(
        prompt_injection_snapshots=[],
        injected_context_fingerprint=None,
    )
    engine._registry.get_tool_names.return_value = ["observe_spreadsheet", "run_code"]
    engine.registry = engine._registry
    engine._child_system_prompt = None
    engine._meta_tool_builder.build_v5_tools.return_value = list(engine._prompt_tool_snapshot)
    engine._config = _config()
    engine.config = engine._config
    engine._active_profile = None
    engine._active_model = "test-model"
    engine._envelope_system_head = None
    engine._envelope_system_effective = None
    return engine


def _canon_bytes(value: object) -> bytes:
    """出网语义的 JSON 字节。不 sort_keys：键序漂移也算前缀破坏。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _reconstruct_wire(engine) -> list[dict]:
    """独立从 durable 投影出网 messages，不读 envelope.messages / wire_messages。"""
    last = engine._last_envelope
    leading = last.system_head if last is not None else ""
    prompts = [leading] if isinstance(leading, str) and leading.strip() else []
    projected = engine.memory.project_for_request(
        system_prompts=prompts,
        vision_capable=bool(engine._is_vision_capable),
        image_pins=getattr(engine, "_image_wire_pin_seq", None),
    )
    return strip_projection_meta(projected)


def _assert_wire_is_durable_projection(engine, sealed) -> None:
    before = copy.deepcopy(engine.memory._messages)
    reconstructed = _reconstruct_wire(engine)
    assert engine.memory._messages == before
    assert sealed.wire_messages == reconstructed
    assert _canon_bytes(sealed.wire_messages) == _canon_bytes(reconstructed)


async def _assemble_seal(engine):
    env, err = assemble_envelope(engine)
    assert err is None, err
    assert env is not None
    sealed, seal_err = await seal_envelope(engine, env)
    assert seal_err is None, seal_err
    assert sealed is not None
    return sealed


async def _capture_create(engine, sealed) -> dict:
    """经 LLMCaller 打到假 client.create，记录真实出网 kwargs。"""
    captured: list[dict] = []

    async def create(**kwargs):
        captured.append({key: copy.deepcopy(value) for key, value in kwargs.items()})
        return SimpleNamespace(ok=True)

    engine._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    kwargs: dict = {
        "model": engine._active_model,
        "messages": list(sealed.wire_messages),
    }
    if sealed.tools:
        kwargs["tools"] = list(sealed.tools)
    if engine._config.prompt_cache_key_enabled:
        kwargs["prompt_cache_key"] = sealed.prompt_cache_key
    await LLMCaller(engine).create_chat_completion_with_retry(kwargs)
    assert captured, "假 client.create 未被调用"
    return captured[0]


def _set_tools(engine, tools: list[dict]) -> None:
    engine._prompt_tool_snapshot = tools
    engine._meta_tool_builder.build_v5_tools.return_value = tools
    engine._registry.get_tool_names.return_value = [
        item["function"]["name"] for item in tools
    ]


def _starts_series(prev, curr) -> bool:
    """与 envelope.assemble_envelope 的 series 判定同构（tools / compaction）。"""
    if prev is None:
        return True
    return (
        curr.compaction_generation != prev.compaction_generation
        or prev.tools != curr.tools
    )


def _prepare_loop_engine(engine):
    """轻量补齐 run_tool_loop 所需字段，不构造 AgentEngine。"""
    engine._driver = None
    engine._turn_budget = None
    engine._tool_dispatcher = None
    engine._llm_call_store = None
    engine._show_reasoning = False
    engine._model_capabilities = None
    engine._active_profile = None
    engine._thinking_config = ThinkingConfig(effort="none")
    engine._refresh_credential_if_needed = AsyncMock()
    engine._try_refresh_registry = MagicMock()
    engine.save_session_snapshot = MagicMock()
    engine._emit = MagicMock()
    engine._cache_miss_streak = 0
    engine._cache_miss_warned = False
    engine._last_failure_count = 0
    engine._last_iteration_count = 0
    engine._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock())),
    )
    engine._llm_caller = LLMCaller(engine)
    return engine


def _completion(*, content: str = "ok", usage: object):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=None),
            )
        ],
        usage=usage,
    )


# ── a) 重构不变量 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_wire_messages_equal_durable_projection_across_steps() -> None:
    """每步 wire_messages 必须等于 durable 经 project_for_request+strip 的投影。"""
    engine = _engine()
    engine.memory.system_prompt = "WRITE_HEAD"
    engine.memory.add_user_message("hello")
    first = await _assemble_seal(engine)
    _assert_wire_is_durable_projection(engine, first)

    engine.memory.add_assistant_message("ok")
    engine.memory.add_user_message("next")
    second = await _assemble_seal(engine)
    _assert_wire_is_durable_projection(engine, second)

    engine.memory.add_tool_call("c1", "observe_spreadsheet", "{}")
    engine.memory.add_tool_result("c1", "sheet=Sheet1")
    third = await _assemble_seal(engine)
    _assert_wire_is_durable_projection(engine, third)

    engine._current_chat_mode = "plan"
    engine.memory.system_prompt = "WRITE_HEAD\n\n当前是计划模式。"
    engine.memory.add_assistant_message("看到了")
    engine.memory.add_user_message("改成计划")
    fourth = await _assemble_seal(engine)
    _assert_wire_is_durable_projection(engine, fourth)

    _set_tools(
        engine,
        [{
            "type": "function",
            "function": {"name": "new_tool", "description": "d", "parameters": {}},
        }],
    )
    fifth = await _assemble_seal(engine)
    _assert_wire_is_durable_projection(engine, fifth)


@pytest.mark.asyncio
async def test_projection_does_not_mutate_durable_history() -> None:
    """project_for_request / strip_projection_meta 不得改写 memory._messages。"""
    engine = _engine()
    engine.memory.add_user_message("hello")
    engine.memory.add_assistant_message("ok")
    engine.memory.add_user_message("next")
    sealed = await _assemble_seal(engine)
    before = copy.deepcopy(engine.memory._messages)
    _reconstruct_wire(engine)
    strip_projection_meta(list(sealed.messages))
    assert engine.memory._messages == before
    assert _canon_bytes(engine.memory._messages) == _canon_bytes(before)


# ── b) 出网参数捕获 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_outbound_same_series_keeps_tools_system_message_prefix_bytes() -> None:
    """同 series 连续两步：假 client 捕获的 tools / system / messages 前缀字节一致。"""
    engine = _engine()
    engine.memory.system_prompt = "WRITE_HEAD"
    engine.memory.add_user_message("hello")
    first_env = await _assemble_seal(engine)
    first = await _capture_create(engine, first_env)

    engine.memory.add_assistant_message("ok")
    engine.memory.add_user_message("next")
    second_env = await _assemble_seal(engine)
    second = await _capture_create(engine, second_env)

    assert not _starts_series(first_env, second_env)
    assert first_env.identity.tools_digest == second_env.identity.tools_digest
    assert _canon_bytes(second["tools"]) == _canon_bytes(first["tools"])
    assert _canon_bytes(second["messages"][0]) == _canon_bytes(first["messages"][0])
    prefix = second["messages"][: len(first["messages"])]
    assert _canon_bytes(prefix) == _canon_bytes(first["messages"])
    assert first["messages"][0]["role"] == "system"
    assert first["messages"][0]["content"] == "WRITE_HEAD"
    assert first.get("prompt_cache_key") == second.get("prompt_cache_key")
    assert str(first.get("prompt_cache_key") or "").startswith("em_sess-1-")
    assert first_env.epoch is not None
    assert first_env.epoch.key() == first.get("prompt_cache_key")
    assert first_env.transport == "inline"
    assert first_env.digest_payload
    assert second_env.digest_payload.startswith(first_env.digest_payload)


@pytest.mark.asyncio
async def test_outbound_plan_switch_keeps_messages_0_and_appends_system() -> None:
    """plan 切换：出网 messages[0] 不变，尾部多一条 system。"""
    engine = _engine()
    engine.memory.system_prompt = "WRITE_HEAD"
    engine.memory.add_user_message("hello")
    first_env = await _assemble_seal(engine)
    first = await _capture_create(engine, first_env)

    engine.memory.add_assistant_message("ok")
    engine.memory.add_user_message("改成计划")
    engine._current_chat_mode = "plan"
    engine.memory.system_prompt = "WRITE_HEAD\n\n当前是计划模式。"
    second_env = await _assemble_seal(engine)
    second = await _capture_create(engine, second_env)

    assert not _starts_series(first_env, second_env)
    assert _canon_bytes(second["tools"]) == _canon_bytes(first["tools"])
    assert _canon_bytes(second["messages"][0]) == _canon_bytes(first["messages"][0])
    # 历史内 system_update 在 wire 上降级为 user（严格 OpenAI 网关拒绝中位 system）
    assert second["messages"][-1]["role"] == "user"
    assert "计划模式" in second["messages"][-1]["content"]
    assert len(second["messages"]) == len(first["messages"]) + 3


@pytest.mark.asyncio
async def test_outbound_tools_change_reopens_series_exactly_once() -> None:
    """write → plan → 改工具目录：series 只在工具变化那一步重开一次。"""
    engine = _engine()
    engine.memory.system_prompt = "WRITE_HEAD"
    engine.memory.add_user_message("hello")
    first = await _assemble_seal(engine)
    await _capture_create(engine, first)

    engine.memory.add_assistant_message("ok")
    engine.memory.add_user_message("改成计划")
    engine._current_chat_mode = "plan"
    engine.memory.system_prompt = "WRITE_HEAD\n\n当前是计划模式。"
    second = await _assemble_seal(engine)
    await _capture_create(engine, second)

    _set_tools(
        engine,
        [{
            "type": "function",
            "function": {"name": "new_tool", "description": "d", "parameters": {}},
        }],
    )
    third = await _assemble_seal(engine)
    outbound = await _capture_create(engine, third)

    envelopes = [first, second, third]
    reopen_at = [
        index
        for index in range(1, len(envelopes))
        if _starts_series(envelopes[index - 1], envelopes[index])
    ]
    assert reopen_at == [2]
    assert first.identity.tools_digest == second.identity.tools_digest
    assert third.identity.tools_digest != second.identity.tools_digest
    assert third.system_head == third.system
    assert "计划模式" in third.system_head
    assert all(msg.get("_prompt_kind") != "system_update" for msg in engine.memory._messages)
    system_msgs = [msg for msg in outbound["messages"] if msg.get("role") == "system"]
    assert len(system_msgs) == 1
    assert system_msgs[0]["content"] == third.system_head
    assert outbound["messages"][0]["content"] == third.system_head


# ── c) 命中观测 ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("usage", "want_cached", "want_creation", "want_read"),
    [
        (
            {
                "prompt_tokens": 5000,
                "prompt_tokens_details": {"cached_tokens": 4000},
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            4000,
            0,
            0,
        ),
        (
            SimpleNamespace(
                prompt_tokens=5000,
                prompt_tokens_details=SimpleNamespace(cached_tokens=2500),
                cache_creation_input_tokens=100,
                cache_read_input_tokens=3000,
            ),
            2500,
            100,
            3000,
        ),
        (None, 0, 0, 0),
        ({"prompt_tokens": 5000}, 0, 0, 0),
        ({"prompt_cache_hit_tokens": 8000}, 8000, 0, 0),
        (SimpleNamespace(prompt_cache_hit_tokens=8000), 8000, 0, 0),
        (
            {
                "prompt_tokens_details": {"cached_tokens": 4000},
                "prompt_cache_hit_tokens": 8000,
            },
            8000,
            0,
            0,
        ),
    ],
)
def test_extract_cached_tokens_from_usage_shapes(
    usage, want_cached, want_creation, want_read,
) -> None:
    """loop 使用点的纯函数：OpenAI/Gemini details、DeepSeek hit、Anthropic cache_read。"""
    assert _extract_cached_tokens(usage) == want_cached
    assert _extract_anthropic_cache_tokens(usage) == (want_creation, want_read)


async def _run_loop_with_usage(engine, usage) -> None:
    engine.memory.add_user_message("turn")

    async def create(**_kwargs):
        return _completion(usage=usage)

    engine._client.chat.completions.create = create
    engine._llm_caller = LLMCaller(engine)
    await run_tool_loop(engine, None, on_event=None)


@pytest.mark.asyncio
async def test_cache_hit_logs_positive_ratio(caplog: pytest.LogCaptureFixture) -> None:
    """prompt>=2000 且命中为正时记 info 比率，不累计 miss streak。"""
    engine = _prepare_loop_engine(_engine())
    usage = SimpleNamespace(
        prompt_tokens=5000,
        completion_tokens=8,
        prompt_tokens_details=SimpleNamespace(cached_tokens=4000),
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    with caplog.at_level(logging.INFO, logger="excelmanus.agent.loop"):
        await _run_loop_with_usage(engine, usage)
    assert engine._cache_miss_streak == 0
    assert engine._cache_miss_warned is False
    assert any("cache: read=4000 / prompt=5000 (80%)" in rec.getMessage() for rec in caplog.records)
    diag = engine._turn_diagnostics[-1]
    assert diag.cached_tokens == 4000
    assert diag.prompt_tokens == 5000


@pytest.mark.asyncio
async def test_anthropic_cache_read_counts_as_positive_hit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Anthropic cache_read_input_tokens 走同一比率，命中为正时写 Prompt Cache 诊断。"""
    engine = _prepare_loop_engine(_engine())
    usage = {
        "prompt_tokens": 8000,
        "completion_tokens": 3,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 6400,
    }
    with caplog.at_level(logging.INFO, logger="excelmanus.agent.loop"):
        await _run_loop_with_usage(engine, usage)
    assert engine._cache_miss_streak == 0
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("cache: read=6400 / prompt=8000 (80%)" in text for text in messages)
    assert any("cache_hit_ratio=80.0%" in text for text in messages)
    diag = engine._turn_diagnostics[-1]
    assert diag.cache_read_input_tokens == 6400
    assert diag.cache_creation_input_tokens == 10


@pytest.mark.asyncio
async def test_cache_miss_streak_warns_once_after_three(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """prompt>=2000 且比率 <10% 连续三次才告警，第四次不再重复。"""
    engine = _prepare_loop_engine(_engine())
    miss = SimpleNamespace(
        prompt_tokens=3000,
        completion_tokens=4,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    with caplog.at_level(logging.INFO, logger="excelmanus.agent.loop"):
        await _run_loop_with_usage(engine, miss)
        assert engine._cache_miss_streak == 1
        await _run_loop_with_usage(engine, miss)
        assert engine._cache_miss_streak == 2
        assert engine._cache_miss_warned is False
        await _run_loop_with_usage(engine, miss)
        assert engine._cache_miss_streak == 3
        assert engine._cache_miss_warned is True
        await _run_loop_with_usage(engine, miss)
        assert engine._cache_miss_streak == 4

    warnings = [
        rec.getMessage()
        for rec in caplog.records
        if rec.levelno >= logging.WARNING and "缓存命中近零" in rec.getMessage()
    ]
    assert len(warnings) == 1
    assert "连续 3 次" in warnings[0]


@pytest.mark.asyncio
async def test_short_prompt_resets_miss_streak_without_cache_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """prompt<2000 不记 cache 比率、不计入 miss streak（即使 cached=0）。"""
    engine = _prepare_loop_engine(_engine())
    miss = SimpleNamespace(
        prompt_tokens=3000,
        completion_tokens=4,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    short = SimpleNamespace(
        prompt_tokens=1999,
        completion_tokens=4,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    await _run_loop_with_usage(engine, miss)
    assert engine._cache_miss_streak == 1
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="excelmanus.agent.loop"):
        await _run_loop_with_usage(engine, short)
    assert engine._cache_miss_streak == 0
    assert engine._cache_miss_warned is False
    assert not any("cache: read=" in rec.getMessage() for rec in caplog.records)
