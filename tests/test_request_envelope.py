"""请求信封：前缀字节门禁。"""

from __future__ import annotations

import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.memory import ConversationMemory
from excelmanus.mentions.parser import Mention, ResolvedMention
from excelmanus.prompt.envelope import (
    RequestEnvelope,
    assemble_envelope,
    assert_in_history_keeps_head,
    assert_prefix_stable,
    invalidate_envelope,
    seal_envelope,
    session_prompt_cache_key,
)
from excelmanus.prompt.assemble import prepare_system_prompts_for_request


def _config(**kwargs: object) -> ExcelManusConfig:
    values = {"api_key": "t", "base_url": "https://x.example/v1", "model": "test-model"}
    values.update(kwargs)
    return ExcelManusConfig(**values)


def _engine(*, session_id: str = "sess-1") -> MagicMock:
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
            "function": {"name": "inspect_spreadsheet", "description": "d", "parameters": {}},
        },
        {
            "type": "function",
            "function": {"name": "run_code", "description": "d", "parameters": {}},
        },
    ]
    engine._current_chat_mode = "write"
    engine._present_as = "native"
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
    engine._registry.get_tool_names.return_value = ["inspect_spreadsheet", "run_code"]
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


def test_same_identity_two_steps_share_tools_system_and_history_prefix() -> None:
    engine = _engine()
    engine.memory.add_user_message("hello")
    first, err1 = assemble_envelope(engine, tool_access="may_write")
    assert err1 is None and first is not None
    engine.memory.add_assistant_message("ok")
    engine.memory.add_user_message("next")
    second, err2 = assemble_envelope(engine, tool_access="may_write")
    assert err2 is None and second is not None
    assert_prefix_stable(first, second)
    assert first.tools == second.tools
    assert first.system == second.system
    assert second.messages[: len(first.messages)] == first.messages


def test_mention_then_plain_turn_keeps_history_prefix() -> None:
    engine = _engine()
    engine.memory.add_user_message("看这张表")
    engine._mention_contexts = [
        ResolvedMention(
            mention=Mention(kind="file", value="sales.xlsx", raw="@file:sales.xlsx", start=0, end=16),
            context_block="Sheets: Sheet1",
        )
    ]
    first, err1 = assemble_envelope(engine)
    assert err1 is None and first is not None
    assert any("<mention_context>" in str(m.get("content", "")) for m in engine.memory.messages)
    engine._mention_contexts = []
    engine.memory.add_assistant_message("已读")
    engine.memory.add_user_message("继续")
    second, err2 = assemble_envelope(engine)
    assert err2 is None and second is not None
    assert first.system == second.system
    assert first.messages[0] == second.messages[0]
    history = [m for m in second.messages if m.get("role") != "system"]
    assert history[0]["content"] == "看这张表"
    assert any("<mention_context>" in str(m.get("content", "")) for m in history)


def test_hook_clear_does_not_shift_prefix() -> None:
    engine = _engine()
    engine.memory.add_user_message("hi")
    engine._transient_hook_contexts = ["审批已通过"]
    first, err1 = assemble_envelope(engine)
    assert err1 is None and first is not None
    engine.memory.add_assistant_message("done")
    second, err2 = assemble_envelope(engine)
    assert err2 is None and second is not None
    assert_prefix_stable(first, second)
    assert any("审批已通过" in str(m.get("content", "")) for m in second.messages)


def test_nine_tool_batches_keep_early_tool_result_bytes() -> None:
    engine = _engine()
    engine.memory.add_user_message("go")
    early = "EARLY_TOOL_RESULT " + ("Z" * 400)
    engine.memory.add_tool_call("c0", "inspect_spreadsheet", "{}")
    engine.memory.add_tool_result("c0", early)
    for i in range(1, 9):
        engine.memory.add_tool_call(f"c{i}", "inspect_spreadsheet", "{}")
        engine.memory.add_tool_result(f"c{i}", f"later-{i}-" + ("Y" * 400))
    env, err = assemble_envelope(engine)
    assert err is None and env is not None
    tool_msgs = [m for m in env.messages if m.get("role") == "tool"]
    assert tool_msgs[0]["content"] == early


def test_prompt_cache_key_stable_across_turns() -> None:
    engine = _engine(session_id="abc")
    engine._session_turn = 1
    assert session_prompt_cache_key(engine) == "em_abc"
    engine._session_turn = 9
    assert session_prompt_cache_key(engine) == "em_abc"
    first, _ = assemble_envelope(engine)
    engine._session_turn = 10
    second, _ = assemble_envelope(engine)
    assert first is not None and second is not None
    assert first.prompt_cache_key == second.prompt_cache_key == "em_abc"


def test_compaction_replay_starts_with_envelope_system_and_shadowed_region() -> None:
    from excelmanus.compaction import CompactionManager

    engine = _engine()
    for i in range(6):
        engine.memory.add_user_message(f"用户 {i}")
        engine.memory.add_assistant_message(f"助手 {i}")
    first, err = assemble_envelope(engine)
    assert err is None and first is not None

    captured: dict[str, list] = {}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    captured["messages"] = kwargs["messages"]
                    captured["tools"] = kwargs.get("tools")
                    return SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content="摘要正文"))]
                    )

    mgr = CompactionManager(_config(compaction_keep_recent_turns=2))
    import asyncio

    asyncio.run(
        mgr.auto_compact(
            memory=engine.memory,
            system_msgs=first.system_messages,
            client=_Client(),
            summary_model="test-model",
            tools=first.tools,
        )
    )
    sent = captured["messages"]
    assert sent[0] == first.system_messages[0]
    assert captured["tools"] == first.tools
    assert sent[-1]["role"] == "user"


def test_prepare_returns_only_stable_system() -> None:
    engine = _engine()
    engine._transient_hook_contexts = ["hook-once"]
    prompts, err = prepare_system_prompts_for_request(engine)
    assert err is None
    assert len(prompts) == 1
    assert "hook-once" not in prompts[0]


def test_compact_for_pre_step_does_not_consume_hooks() -> None:
    import asyncio
    from excelmanus.compaction import compact_for_pre_step

    engine = _engine()
    engine._transient_hook_contexts = ["hook-must-survive"]

    class _Mgr:
        def should_compact(self, *_args: object, **_kwargs: object) -> bool:
            return False

    engine._compaction_manager = _Mgr()
    asyncio.run(compact_for_pre_step(engine))
    env, err = assemble_envelope(engine)
    assert err is None and env is not None
    assert any("hook-must-survive" in str(m.get("content", "")) for m in env.messages)


def test_warmup_tools_match_first_envelope() -> None:
    engine = _engine()
    from excelmanus.prompt.envelope import resolve_envelope_tools

    tools = resolve_envelope_tools(engine, tool_access="may_write")
    env, err = assemble_envelope(engine, tool_access="may_write")
    assert err is None and env is not None
    assert env.tools == tools
    assert env.system == engine.memory.system_prompt


def test_assert_prefix_stable_allows_identity_change() -> None:
    engine = _engine()
    engine.memory.add_user_message("a")
    first, _ = assemble_envelope(engine)
    engine._current_chat_mode = "plan"
    engine.memory.system_prompt = "plan system"
    second = RequestEnvelope(
        identity=first.identity.__class__(
            present_as=first.identity.present_as,
            plan_active=True,
            tool_access=first.identity.tool_access,
            tools_digest=first.identity.tools_digest,
            system_digest="changed",
            catalog_digest=first.identity.catalog_digest,
        ),
        system="plan system",
        tools=first.tools,
        messages=first.messages,
        prompt_cache_key=first.prompt_cache_key,
    )
    assert_prefix_stable(first, second)


def test_in_history_plan_switch_appends_system_keeps_head() -> None:
    engine = _engine()
    engine.memory.system_prompt = "WRITE_HEAD"
    engine.memory.add_user_message("hello")
    first, err1 = assemble_envelope(engine)
    assert err1 is None and first is not None
    assert first.messages[0]["content"] == "WRITE_HEAD"
    engine.memory.add_assistant_message("ok")
    engine.memory.add_user_message("改成计划")
    engine._current_chat_mode = "plan"
    engine.memory.system_prompt = "WRITE_HEAD\n\n当前是计划模式。"
    second, err2 = assemble_envelope(engine)
    assert err2 is None and second is not None
    assert_in_history_keeps_head(first, second)
    assert first.messages[0] == second.messages[0]
    assert first.tools == second.tools
    # 历史内 system_update 在 wire 上降级为 user（严格网关拒绝中位 system）
    assert second.messages[-1]["role"] == "user"
    assert "计划模式" in second.messages[-1]["content"]
    assert second.system_head == "WRITE_HEAD"
    assert "计划模式" in second.system
    history = [m for m in second.messages if m.get("role") != "system"]
    assert history[0]["content"] == "hello"
    engine.memory.add_assistant_message("计划中")
    engine.memory.add_user_message("下一步")
    third, err3 = assemble_envelope(engine)
    assert err3 is None and third is not None
    assert_prefix_stable(second, third)
    assert third.messages[0] == first.messages[0]
    assert any(m.get("role") == "user" and "计划模式" in str(m.get("content", "")) for m in third.messages[1:])


def test_in_history_plan_exit_appends_write_system() -> None:
    engine = _engine()
    engine.memory.system_prompt = "WRITE_HEAD"
    engine.memory.add_user_message("hello")
    assemble_envelope(engine)
    engine._current_chat_mode = "plan"
    engine.memory.system_prompt = "WRITE_HEAD\n\n当前是计划模式。"
    assemble_envelope(engine)
    engine._current_chat_mode = "write"
    engine.memory.system_prompt = "WRITE_HEAD"
    engine.memory.add_assistant_message("计划完成")
    third, err = assemble_envelope(engine)
    assert err is None and third is not None
    assert third.messages[0]["content"] == "WRITE_HEAD"
    tails = [m for m in third.messages if m.get("role") == "system"]
    assert tails == [{"role": "system", "content": "WRITE_HEAD"}]
    # 退出计划模式的更新在 wire 上投影为 user 消息
    assert any(
        m.get("role") == "user" and "WRITE_HEAD" in str(m.get("content", ""))
        for m in third.messages[1:]
    )


def test_plan_switch_never_replaces_messages_0() -> None:
    engine = _engine()
    engine.memory.system_prompt = "WRITE_HEAD"
    engine.memory.add_user_message("hello")
    first, _ = assemble_envelope(engine)
    engine._current_chat_mode = "plan"
    engine.memory.system_prompt = "WRITE_HEAD\n\n当前是计划模式。"
    second, _ = assemble_envelope(engine)
    assert first is not None and second is not None
    assert first.messages[0] == second.messages[0]
    assert second.messages[-1]["role"] == "user"
    assert "计划模式" in second.messages[-1]["content"]


def test_in_history_tools_change_rebases_head() -> None:
    engine = _engine()
    engine.memory.system_prompt = "WRITE_HEAD"
    engine.memory.add_user_message("hello")
    first, _ = assemble_envelope(engine)
    new_tools = [
        {
            "type": "function",
            "function": {"name": "run_code", "description": "only", "parameters": {}},
        }
    ]
    engine._prompt_tool_snapshot = new_tools
    engine._meta_tool_builder.build_v5_tools.return_value = new_tools
    engine.memory.system_prompt = "CODE_HEAD"
    second, _ = assemble_envelope(engine)
    assert first is not None and second is not None
    assert second.messages[0]["content"] == "CODE_HEAD"
    assert second.system_head == "CODE_HEAD"
    assert second.tools != first.tools


def test_in_history_compaction_rebases_head() -> None:
    engine = _engine()
    engine.memory.system_prompt = "WRITE_HEAD"
    engine.memory.add_user_message("hello")
    first, _ = assemble_envelope(engine)
    engine._compaction_generation = 1
    engine.memory.system_prompt = "AFTER_COMPACT"
    second, _ = assemble_envelope(engine)
    assert first is not None and second is not None
    assert first.messages[0]["content"] == "WRITE_HEAD"
    assert second.messages[0]["content"] == "AFTER_COMPACT"
    assert second.system_head == "AFTER_COMPACT"


def test_last_system_msgs_stores_leading_not_full_render() -> None:
    engine = _engine()
    engine.memory.system_prompt = "WRITE_HEAD"
    engine.memory.add_user_message("hello")
    first, err1 = assemble_envelope(engine)
    assert err1 is None and first is not None
    engine.memory.add_assistant_message("ok")
    engine.memory.add_user_message("改成计划")
    engine._current_chat_mode = "plan"
    engine.memory.system_prompt = "WRITE_HEAD\n\n当前是计划模式。"
    second, err2 = assemble_envelope(engine)
    assert err2 is None and second is not None
    assert engine._last_system_msgs == [{"role": "system", "content": "WRITE_HEAD"}]
    assert second.system_head == "WRITE_HEAD"
    assert "计划模式" in second.system
    from excelmanus.prompt.envelope import compaction_wire_context

    prefix, tools = compaction_wire_context(engine)
    assert prefix == [{"role": "system", "content": "WRITE_HEAD"}]
    assert tools == second.tools


def test_assemble_persist_false_does_not_write_envelope_state() -> None:
    engine = _engine()
    engine._last_envelope = None
    engine._last_system_msgs = None
    engine.memory.add_user_message("hello")
    engine._transient_hook_contexts = ["hook-must-survive"]
    peeked, err = assemble_envelope(engine, persist=False)
    assert err is None and peeked is not None
    assert engine._last_envelope is None
    assert engine._last_system_msgs is None
    assert engine._transient_hook_contexts == ["hook-must-survive"]
    env, err3 = assemble_envelope(engine)
    assert err3 is None and env is not None
    assert engine._last_envelope is env
    assert any("hook-must-survive" in str(m.get("content", "")) for m in env.messages)


def test_replace_tool_result_before_send_does_not_bump_projection() -> None:
    engine = _engine()
    engine.memory.add_user_message("go")
    engine.memory.add_tool_call("c1", "inspect_spreadsheet", "{}")
    engine.memory.add_tool_result("c1", "pending")
    engine.memory.replace_tool_result("c1", "real-result")
    assert engine.memory._projection_dirty is False
    env, err = assemble_envelope(engine)
    assert err is None and env is not None
    assert env.projection_generation == 0
    tool_msgs = [m for m in env.messages if m.get("role") == "tool"]
    assert tool_msgs[-1]["content"] == "real-result"


def test_replace_tool_result_after_envelope_bumps_projection() -> None:
    engine = _engine()
    engine.memory.add_user_message("go")
    engine.memory.add_tool_call("c1", "inspect_spreadsheet", "{}")
    engine.memory.add_tool_result("c1", "pending-approval")
    first, err1 = assemble_envelope(engine)
    assert err1 is None and first is not None
    assert "pending-approval" in str(first.messages)
    engine.memory.replace_tool_result("c1", "accepted-result")
    assert engine.memory._projection_dirty is True
    second, err2 = assemble_envelope(engine)
    assert err2 is None and second is not None
    assert second.projection_generation == first.projection_generation + 1
    tool_msgs = [m for m in second.messages if m.get("role") == "tool"]
    assert tool_msgs[-1]["content"] == "accepted-result"


def test_stale_tool_snapshot_rebuilds_when_catalog_digest_changes() -> None:
    engine = _engine()
    first, err1 = assemble_envelope(engine)
    assert err1 is None and first is not None
    engine._prompt_tool_snapshot = [
        {
            "type": "function",
            "function": {"name": "stale_only", "description": "old", "parameters": {}},
        }
    ]
    engine._registry.get_tool_names.return_value = ["inspect_spreadsheet", "run_code", "new_mcp"]
    rebuilt, err2 = assemble_envelope(engine)
    assert err2 is None and rebuilt is not None
    names = [t["function"]["name"] for t in rebuilt.tools]
    assert "stale_only" not in names


def test_envelope_is_always_in_history() -> None:
    engine = _engine()
    engine._config = _config(base_url="https://api.openai.com/v1")
    engine.config = engine._config
    openai_env, err = assemble_envelope(engine)
    assert err is None and openai_env is not None and openai_env.in_history is True

    engine._config = _config(base_url="https://api.deepseek.com/v1")
    engine.config = engine._config
    deepseek_env, err = assemble_envelope(engine)
    assert err is None and deepseek_env is not None and deepseek_env.in_history is True


def test_assemble_rejects_rewritten_history_prefix() -> None:
    engine = _engine()
    engine.memory.add_user_message("hello")
    first, err1 = assemble_envelope(engine)
    assert err1 is None and first is not None
    engine.memory._messages[0]["content"] = "MUTATED"
    engine.memory.add_assistant_message("ok")
    engine.memory.add_user_message("next")
    second, err2 = assemble_envelope(engine)
    assert second is None
    assert err2 is not None and "前缀" in err2
    assert engine._last_envelope is first
    assert engine._projection_generation == first.projection_generation


def test_assert_prefix_stable_requires_full_older_envelope() -> None:
    engine = _engine()
    engine.memory.add_user_message("hello")
    first, _ = assemble_envelope(engine)
    engine.memory.add_assistant_message("ok")
    engine.memory.add_user_message("next")
    second, err = assemble_envelope(engine)
    assert first is not None and second is not None and err is None
    mutated_head = dict(second.messages[1])
    mutated_head["content"] = "MUTATED"
    mutated = replace(
        second,
        messages=[second.messages[0], mutated_head, *second.messages[2:]],
    )
    with pytest.raises(AssertionError, match="prefix"):
        assert_prefix_stable(first, mutated)


MIN_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class _FakeFiles:
    def __init__(self) -> None:
        self.created: list[str] = []
        self._n = 0
        self.fail_always: Exception | None = None

    async def create(self, file, purpose):
        if self.fail_always is not None:
            raise self.fail_always
        self._n += 1
        self.created.append(purpose)
        return {"id": f"file-api-{self._n}", "expires_at": int(time.time()) + 86400}


def _projected_inline(variant: str = "sha256:" + "ab" * 32) -> list[dict]:
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": "see"},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{MIN_PNG_B64}"},
                "_variant_id": variant,
            },
        ],
    }]


def _files_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("EXCELMANUS_HOME", str(tmp_path))
    from excelmanus.attachments.store import reset_attachment_store

    reset_attachment_store()
    engine = _engine()
    engine._config = _config(base_url="https://api.deepseek.com/v1", image_files_api="true")
    engine.config = engine._config
    engine._active_base_url = "https://api.deepseek.com/v1"
    engine._active_api_key = "t"
    engine._active_model = "test-model"
    engine._active_protocol = "openai"
    engine._resolved_route = None
    files = _FakeFiles()
    engine._client = SimpleNamespace(files=files)
    return engine, files


@pytest.mark.asyncio
async def test_seal_pins_file_mode_and_reuses_file_id(tmp_path, monkeypatch) -> None:
    engine, files = _files_engine(tmp_path, monkeypatch)
    env, err = assemble_envelope(engine, persist=False)
    assert err is None and env is not None
    env = replace(env, messages=_projected_inline())
    first, err1 = await seal_envelope(engine, env)
    assert err1 is None and first is not None
    assert engine._files_wire_mode == "file"
    assert first.wire_messages[0]["content"][1]["type"] == "file"
    file_id = first.wire_messages[0]["content"][1]["file_id"]
    second, err2 = await seal_envelope(engine, env)
    assert err2 is None and second is not None
    assert len(files.created) == 1
    assert second.wire_messages[0]["content"][1]["file_id"] == file_id
    assert second.projection_generation == first.projection_generation


@pytest.mark.asyncio
async def test_seal_first_failure_pins_inline(tmp_path, monkeypatch) -> None:
    engine, files = _files_engine(tmp_path, monkeypatch)
    files.fail_always = RuntimeError("upload down")
    env, err = assemble_envelope(engine, persist=False)
    assert err is None and env is not None
    env = replace(env, messages=_projected_inline())
    first, err1 = await seal_envelope(engine, env)
    assert err1 is None and first is not None
    assert engine._files_wire_mode == "inline"
    assert first.wire_messages[0]["content"][1]["type"] == "image_url"
    files.fail_always = None
    second, err2 = await seal_envelope(engine, env)
    assert err2 is None and second is not None
    assert engine._files_wire_mode == "inline"
    assert second.wire_messages[0]["content"][1]["type"] == "image_url"
    assert files.created == []


@pytest.mark.asyncio
async def test_seal_file_mode_failure_bumps_projection(tmp_path, monkeypatch) -> None:
    engine, files = _files_engine(tmp_path, monkeypatch)
    env, err = assemble_envelope(engine, persist=False)
    assert err is None and env is not None
    env = replace(env, messages=_projected_inline())
    first, err1 = await seal_envelope(engine, env)
    assert err1 is None and first is not None
    assert engine._files_wire_mode == "file"
    extra = _projected_inline(variant="sha256:" + "cd" * 32)
    env2 = replace(
        env,
        messages=[{
            "role": "user",
            "content": [
                *env.messages[0]["content"],
                extra[0]["content"][1],
            ],
        }],
    )
    files.fail_always = RuntimeError("upload down")
    second, err2 = await seal_envelope(engine, env2)
    assert err2 is None and second is not None
    assert engine._files_wire_mode == "inline"
    assert second.wire_messages[0]["content"][1]["type"] == "image_url"
    assert second.projection_generation == first.projection_generation + 1


@pytest.mark.asyncio
async def test_seal_stale_file_id_bumps_projection(tmp_path, monkeypatch) -> None:
    from excelmanus.attachments.files_api import invalidate_file_ids

    engine, files = _files_engine(tmp_path, monkeypatch)
    env, err = assemble_envelope(engine, persist=False)
    assert err is None and env is not None
    env = replace(env, messages=_projected_inline())
    first, err1 = await seal_envelope(engine, env)
    assert err1 is None and first is not None
    file_id = first.wire_messages[0]["content"][1]["file_id"]
    invalidate_file_ids(
        [file_id],
        base_url="https://api.deepseek.com/v1",
        api_key="t",
    )
    second, err2 = await seal_envelope(engine, env)
    assert err2 is None and second is not None
    assert second.wire_messages[0]["content"][1]["file_id"] != file_id
    assert second.projection_generation == first.projection_generation + 1
    assert len(files.created) == 2


def test_assert_prefix_stable_rejects_history_shrink() -> None:
    """新的历史必须是旧的完整超集；缩短前缀也要 fail closed。"""
    engine = _engine()
    engine.memory.add_user_message("hello")
    first, err1 = assemble_envelope(engine)
    assert err1 is None and first is not None
    engine.memory.add_assistant_message("ok")
    engine.memory.add_user_message("next")
    second, err2 = assemble_envelope(engine)
    assert err2 is None and second is not None

    shrunk = replace(second, messages=second.messages[: len(first.messages) - 1])
    with pytest.raises(AssertionError, match="prefix"):
        assert_prefix_stable(second, shrunk)


def test_assert_prefix_stable_covers_older_last_message() -> None:
    """旧请求最后一条消息也被改写时，门禁必须报错而不是继续出网。"""
    engine = _engine()
    engine.memory.add_user_message("hello")
    first, err1 = assemble_envelope(engine)
    assert err1 is None and first is not None
    engine.memory.add_assistant_message("ok")
    engine.memory.add_user_message("next")
    second, err2 = assemble_envelope(engine)
    assert err2 is None and second is not None

    mutated_last = dict(first.messages[-1])
    mutated_last["content"] = "MUTATED-LAST"
    mutated = replace(
        first,
        messages=[*first.messages[:-1], mutated_last],
    )
    with pytest.raises(AssertionError, match="prefix"):
        assert_prefix_stable(mutated, second)


def test_assemble_rejects_in_place_history_shrink() -> None:
    """旧式原地截断（从头部 pop）没有推进 generation，必须被门禁拦下。"""
    engine = _engine()
    engine.memory.add_user_message("hello")
    first, err1 = assemble_envelope(engine)
    assert err1 is None and first is not None
    engine.memory.add_assistant_message("ok")
    engine.memory.add_user_message("next")
    second, err2 = assemble_envelope(engine)
    assert err2 is None and second is not None

    # 模拟旧原地截断入口：直接缩短 durable 历史，既不 bump compaction
    # 也不 bump projection。
    engine.memory._messages = engine.memory._messages[:1]
    third, err3 = assemble_envelope(engine)
    assert third is None
    assert err3 is not None and "前缀" in err3
    assert engine._last_envelope is second


def test_history_shrink_is_sticky_until_invalidate() -> None:
    """rollback/编辑重发改写 durable 历史后 fail-closed；invalidate_envelope 恢复。"""
    engine = _engine()
    engine.memory.add_user_message("hello")
    first, err = assemble_envelope(engine)
    assert err is None and first is not None

    # 模拟 rollback / 编辑重发：历史被截断并改写
    engine.memory._messages.clear()
    engine.memory.add_user_message("rewritten")

    # 未接信封协议时：前缀断言失败，且 _last_envelope 不更新 → 每次都失败
    _, sticky_err = assemble_envelope(engine)
    assert sticky_err is not None

    # 入口统一调用 invalidate_envelope 后重新开系列
    invalidate_envelope(engine)
    third, err3 = assemble_envelope(engine)
    assert err3 is None and third is not None


def test_generation_desync_is_sticky_until_engine_sync() -> None:
    """手动压缩只 bump memory 侧 generation 而 engine 侧未同步时粘性失败；
    同步 engine 侧 generation（command_handler 的修复）后恢复。"""
    engine = _engine()
    # 前提：会话经历过自动压缩，engine 侧 generation 已非零（engine 值优先）
    engine._compaction_generation = 1
    engine.memory._compaction_generation = 1
    engine.memory.add_user_message("hello")
    first, err = assemble_envelope(engine)
    assert err is None and first is not None

    # 模拟手动 /compact：替换历史 + 只 bump memory 侧 generation
    for msg in engine.memory._messages:
        msg["content"] = "[对话摘要]\n压缩后的摘要。"
    engine.memory._compaction_generation += 1

    # engine 侧仍是 1 → 信封取值与 prev 相等 → 前缀断言失败（粘性）
    _, sticky_err = assemble_envelope(engine)
    assert sticky_err is not None

    # command_handler /compact 的修复动作：同步 engine 侧 + invalidate
    engine._compaction_generation = int(engine.memory._compaction_generation)
    invalidate_envelope(engine)
    third, err3 = assemble_envelope(engine)
    assert err3 is None and third is not None


def test_series_restart_merges_stale_system_updates() -> None:
    """新 series 时清理历史尾部旧 system_update，由新 head 接管（head 合并）。"""
    engine = _engine()
    engine.memory.system_prompt = "WRITE_HEAD"
    engine.memory.add_user_message("hello")
    first, err = assemble_envelope(engine)
    assert err is None and first is not None
    assert first.system_head == "WRITE_HEAD"

    # plan 切换：渲染变化 → 尾部追加 system_update（series 延续，合法）
    engine._current_chat_mode = "plan"
    engine.memory.system_prompt = "WRITE_HEAD\n\n当前是计划模式。"
    second, err2 = assemble_envelope(engine)
    assert err2 is None and second is not None
    kinds = [m.get("_prompt_kind") for m in engine.memory._messages]
    assert "system_update" in kinds

    # 工具目录变化 → 新 series：旧 in-history system 必须被清掉，
    # 本请求由新 head 单独定义 system，历史里不得残留叠加指令
    engine._meta_tool_builder.build_v5_tools.return_value = [
        {
            "type": "function",
            "function": {"name": "new_tool", "description": "d", "parameters": {}},
        },
    ]
    third, err3 = assemble_envelope(engine)
    assert err3 is None and third is not None
    kinds_after = [m.get("_prompt_kind") for m in engine.memory._messages]
    assert "system_update" not in kinds_after
    # wire 上只有 leading head 一条 system（新 head = 渲染全文，已并入 plan 段）
    system_msgs = [m for m in third.messages if m.get("role") == "system"]
    assert len(system_msgs) == 1
    assert system_msgs[0]["content"] == third.system_head
    assert "计划模式" in third.system_head
