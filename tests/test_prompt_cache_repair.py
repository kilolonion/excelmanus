"""Cache repair regressions: production compiler, no paid provider calls."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import httpx
import json
import pytest

from excelmanus.engine_core.llm_caller import LLMCaller, reset_degraded_params
from excelmanus.prompt.envelope import canonical_json, digest_text, session_prompt_cache_key
from excelmanus.request import compiler
from excelmanus.request.series import RequestSeries, series_of
from excelmanus.request.types import RequestHeader
from tests.test_prepared_request_integration import engine_for


@pytest.fixture(autouse=True)
def reset_degradation():
    reset_degraded_params()
    yield
    reset_degraded_params()


async def prepare(engine, **kwargs):
    request, error = await compiler.compile_request(engine, **kwargs)
    assert error is None, error
    assert request is not None
    return request


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["openai", "anthropic", "gemini", "openai_responses"])
async def test_append_keeps_native_prefix_and_actual_key(protocol):
    engine = engine_for(protocol)
    first = await prepare(engine)
    series_of(engine).accept(first.header)
    # Adjacent users exercise Claude's same-role block merging.
    engine.memory.add_user_message("additional evidence")
    second = await prepare(engine)
    assert second.header.provider_prefix[:len(first.header.provider_prefix)] == first.header.provider_prefix
    assert second.header.prompt_cache_key == first.header.prompt_cache_key
    assert second.header.provider_digest != first.header.provider_digest
    assert second.header.provider_digest == digest_text(canonical_json(second.create_kwargs().get("_prepared_body") or second.create_kwargs()))
    assert engine._last_envelope.prompt_cache_key == second.header.prompt_cache_key
    assert engine._envelope_prefix_snapshot["epoch_key"] == second.header.prompt_cache_key
    assert session_prompt_cache_key(engine) == second.header.prompt_cache_key


@pytest.mark.asyncio
@pytest.mark.parametrize("setting", [
    {"response_format": {"type": "json_object"}},
    {"parallel_tool_calls": False},
    {"reasoning_effort": "high"},
    {"tool_choice": "none"},
])
async def test_native_setting_change_is_observed(setting):
    engine = engine_for()
    first = await prepare(engine)
    series_of(engine).accept(first.header)
    second = await prepare(engine, extra={"extra_body": setting})
    assert second.header.content_payload == first.header.content_payload
    assert second.header.provider_config_digest != first.header.provider_config_digest
    assert second.header.prompt_cache_key != first.header.prompt_cache_key
    assert any(e["type"] == "cache/config" for e in series_of(engine).events)
    assert series_of(engine).last_accepted == first.header


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [None, {"extra_body": {"tool_choice": "none"}}])
async def test_mapper_history_rewrite_rejected_and_failed_attempt_rolled_back(monkeypatch, extra):
    engine = engine_for()
    first = await prepare(engine)
    original_envelope = engine._last_envelope
    series_of(engine).accept(first.header)
    engine.memory.add_assistant_message("ok")
    engine.memory.add_user_message("next")
    original_body = compiler._provider_body

    def corrupt(*args, **kwargs):
        body = original_body(*args, **kwargs)
        body["messages"][1]["content"] = "mutated by adapter"
        return body

    with monkeypatch.context() as patch:
        patch.setattr(compiler, "_provider_body", corrupt)
        request, error = await compiler.compile_request(engine, extra=extra)
        assert request is None
        assert "Provider 请求前缀" in error
    assert engine._prepared_request is first
    assert engine._last_envelope is original_envelope
    assert series_of(engine).last_accepted == first.header
    await prepare(engine)


@pytest.mark.asyncio
async def test_cache_policy_change_cannot_mask_native_rewrite(monkeypatch):
    engine = engine_for()
    first = await prepare(engine)
    series_of(engine).accept(first.header)
    original_body = compiler._provider_body

    def corrupt(*args, **kwargs):
        body = original_body(*args, **kwargs)
        body["messages"][1]["content"] = "tampered"
        body["prompt_cache_retention"] = "24h"
        return body

    monkeypatch.setattr(compiler, "_provider_body", corrupt)
    request, error = await compiler.compile_request(engine)
    assert request is None and "Provider 请求前缀" in error


@pytest.mark.asyncio
async def test_claude_marker_policy_and_position_are_not_content_rewrites(monkeypatch):
    engine = engine_for("anthropic")
    first = await prepare(engine)
    series_of(engine).accept(first.header)
    engine.memory.add_user_message("another block")
    original_body = compiler._provider_body

    def move_marker(*args, **kwargs):
        body = original_body(*args, **kwargs)
        blocks = body["messages"][0]["content"]
        blocks[0].pop("cache_control", None)
        blocks[-1]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
        return body

    monkeypatch.setattr(compiler, "_provider_body", move_marker)
    moved = await prepare(engine)
    assert moved.header.provider_prefix[:len(first.header.provider_prefix)] == first.header.provider_prefix
    assert moved.header.cache_policy_digest != first.header.cache_policy_digest
    assert any(e["type"] == "cache/policy" for e in series_of(engine).events)


@pytest.mark.asyncio
async def test_dynamic_rollback_agrees_with_event_log():
    from excelmanus.session_log import SessionEventLog

    engine = engine_for()
    engine.memory.clear()
    log = SessionEventLog("offline")
    engine.memory.attach_event_log(log)
    engine.memory.add_user_message("first")
    await prepare(engine)
    engine.memory.system_prompt += "\nupdated policy"
    engine._transient_hook_contexts = ["hook"]
    failed, error = await compiler.compile_request(engine, extra={"extra_body": {"messages": []}})
    assert failed is None and error
    assert len(log.surface_messages()) == len(engine.memory.messages) == 1
    await prepare(engine)
    assert [m["content"] for m in log.surface_messages()] == [m["content"] for m in engine.memory.messages]


@pytest.mark.asyncio
async def test_config_change_cannot_mask_rewritten_durable_history():
    engine = engine_for()
    first = await prepare(engine)
    series_of(engine).accept(first.header)
    engine.memory.messages[0]["content"] = "tampered"
    engine._last_envelope = None  # Exercise the durable guard after restoration.
    request, error = await compiler.compile_request(engine, extra={"extra_body": {"tool_choice": "none"}})
    assert request is None and "前缀" in error
    assert series_of(engine).last_accepted == first.header


@pytest.mark.asyncio
async def test_failure_rolls_back_hook_and_system_update_then_retry_injects_once():
    engine = engine_for()
    await prepare(engine)
    original = deepcopy(engine.memory.messages)
    engine._transient_hook_contexts = ["hook-once"]
    engine.memory.system_prompt += "\nupdated policy"
    request, error = await compiler.compile_request(engine, extra={"extra_body": {"messages": []}})
    assert request is None and error
    assert engine.memory.messages == original
    assert engine._transient_hook_contexts == ["hook-once"]
    fixed = await prepare(engine)
    text = str(fixed.create_kwargs())
    assert text.count("hook-once") == 1
    assert text.count("updated policy") == 1
    assert engine._transient_hook_contexts == []


@pytest.mark.asyncio
async def test_failed_tool_catalog_change_does_not_drop_system_updates():
    engine = engine_for()
    await prepare(engine)
    engine.memory.system_prompt += "\nupdated policy"
    previous = await prepare(engine)
    original = deepcopy(engine.memory.messages)
    assert any(m.get("_prompt_kind") == "system_update" for m in original)
    tools = list(engine._meta_tool_builder.build_v5_tools.return_value)
    tools.append({"type": "function", "function": {"name": "extra_tool", "parameters": {}}})
    engine._meta_tool_builder.build_v5_tools.return_value = tools
    failed, error = await compiler.compile_request(engine, extra={"extra_body": {"messages": []}})
    assert failed is None and error
    assert engine.memory.messages == original
    assert engine._prepared_request is previous
    await prepare(engine)
    assert all(m.get("_prompt_kind") != "system_update" for m in engine.memory.messages)


@pytest.mark.asyncio
async def test_cancellation_during_files_seal_restores_compile_state(monkeypatch):
    engine = engine_for()
    first = await prepare(engine)
    original_envelope = engine._last_envelope
    original = deepcopy(engine.memory.messages)
    engine._transient_hook_contexts = ["retry hook"]

    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError()

    with monkeypatch.context() as patch:
        patch.setattr(compiler, "seal_envelope", cancel)
        with pytest.raises(asyncio.CancelledError):
            await compiler.compile_request(engine)
    assert engine._last_envelope is original_envelope
    assert engine._prepared_request is first
    assert engine.memory.messages == original
    assert engine._transient_hook_contexts == ["retry hook"]
    await prepare(engine)


@pytest.mark.asyncio
async def test_inline_file_renew_and_fallback_preserve_content_identity(monkeypatch):
    engine = engine_for()
    first = await prepare(engine)
    # Use the real seal/compiler while mocking only remote file upload.
    projected = [*engine._last_envelope.messages, {"role": "user", "content": [{
        "type": "image_url", "image_url": {"url": "data:image/png;base64,eA=="},
        "_attachment_id": "attachment", "_variant_id": "variant",
    }]}]
    envelope = replace(engine._last_envelope, messages=projected)
    engine._client = SimpleNamespace(files=object())
    from excelmanus.attachments import files_api
    monkeypatch.setattr(files_api, "files_api_enabled", lambda *a: True)
    file_id = None

    async def upload(messages, *a, **kw):
        if not file_id:
            return deepcopy(messages)
        rows = deepcopy(messages)
        rows[-1]["content"] = [{"type": "file", "file": {"file_id": file_id}}]
        return rows

    monkeypatch.setattr(files_api, "apply_files_transport", upload)
    inline = await prepare(engine, envelope=envelope)
    series_of(engine).accept(inline.header)
    file_id = "file-one"
    engine._files_inline_until = 0
    file = await prepare(engine, envelope=envelope)
    assert file.header.transport == "file"
    assert file.header.content_payload == inline.header.content_payload
    series_of(engine).accept(file.header)
    file_id = "file-two"
    renewed = await prepare(engine, envelope=envelope)
    assert renewed.header.provider_prefix != file.header.provider_prefix
    assert renewed.header.prompt_cache_key == file.header.prompt_cache_key
    assert renewed.header.content_payload == file.header.content_payload
    series_of(engine).accept(renewed.header)
    file_id = None
    fallback = await prepare(engine, envelope=envelope)
    assert fallback.header.transport == "inline"
    assert fallback.header.content_payload == inline.header.content_payload
    assert sum(e["type"] == "transport/renew" for e in series_of(engine).events) == 3


@pytest.mark.asyncio
async def test_key_override_disable_and_degrade_match_real_http_body():
    import openai

    engine = engine_for()
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(400, json={"error": {"message": "unknown parameter prompt_cache_key"}})
        return httpx.Response(200, json={"id": "c", "object": "chat.completion", "created": 0,
                                       "model": "offline-model", "choices": []})

    client = openai.AsyncOpenAI(api_key="offline", base_url="https://offline.invalid/v1",
                               http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    engine._client = client
    engine._active_profile = SimpleNamespace(thinking_mode="auto", custom_extra_headers="",
                                            custom_extra_body='{"prompt_cache_key":"explicit-key"}')
    try:
        prepared = await prepare(engine)
        await LLMCaller(engine).create_chat_completion_with_retry(prepared.create_kwargs())
        assert requests[0]["prompt_cache_key"] == prepared.header.prompt_cache_key == "explicit-key"
        assert "prompt_cache_key" not in requests[-1]
        assert engine._sent_prepared_request.header.prompt_cache_key == ""
        assert engine._last_envelope.prompt_cache_key == ""
        assert engine._envelope_prefix_snapshot["epoch_key"] == ""
        reset_degraded_params()
        object.__setattr__(engine._config, "prompt_cache_key_enabled", False)
        disabled = await prepare(engine)
        await LLMCaller(engine).create_chat_completion_with_retry(disabled.create_kwargs())
        assert "prompt_cache_key" not in requests[-1]
        assert disabled.header.prompt_cache_key == ""
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_native_baseline_survives_snapshot_and_long_child_key():
    engine = engine_for()
    engine._session_id = "x" * 36 + "-child-0123456789ab"
    first = await prepare(engine)
    assert len(first.header.prompt_cache_key) <= 64
    series_of(engine).accept(first.header)
    restored = RequestSeries.from_dict(json.loads(json.dumps(series_of(engine).to_dict())))
    assert restored.last_accepted == first.header
    engine._request_series = restored
    engine.memory.add_assistant_message("ok")
    second = await prepare(engine)
    assert second.header.prompt_cache_key == first.header.prompt_cache_key
    raw = first.header.to_dict()
    raw["provider_prefix"] = "corrupt"
    assert RequestHeader.from_dict(raw) is None


@pytest.mark.asyncio
async def test_metadata_only_catalog_change_does_not_rotate_sent_key():
    engine = engine_for()
    engine._registry.catalog_digest.return_value = "v1"
    first = await prepare(engine)
    series_of(engine).accept(first.header)
    engine._registry.catalog_digest.return_value = "v2"
    second = await prepare(engine)
    assert second.header.catalog_digest != first.header.catalog_digest
    assert second.header.provider_config_digest == first.header.provider_config_digest
    assert second.header.prompt_cache_key == first.header.prompt_cache_key


@pytest.mark.asyncio
async def test_claude_usage_counts_cache_tokens_in_prompt():
    from excelmanus.providers.claude import _claude_usage
    from excelmanus.request.usage import extract_cache_usage

    usage = _claude_usage({
        "input_tokens": 10,
        "cache_creation_input_tokens": 5,
        "cache_read_input_tokens": 100,
        "output_tokens": 3,
    })
    assert usage.prompt_tokens == 115
    assert usage.total_tokens == 118
    assert usage.cache_read_input_tokens == 100

    plain = _claude_usage({"input_tokens": 10, "output_tokens": 3})
    assert not hasattr(plain, "cache_read_input_tokens")
    assert not hasattr(plain, "cache_creation_input_tokens")

    cache = extract_cache_usage(usage)
    assert cache.hit == 100
    assert cache.write == 5


@pytest.mark.asyncio
async def test_claude_stream_usage_merges_message_start():
    from excelmanus.providers.claude import ClaudeClient

    frames = [
        {"type": "message_start", "message": {"usage": {
            "input_tokens": 10,
            "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 100,
            "output_tokens": 0,
        }}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta",
         "delta": {"stop_reason": "end_turn"},
         "usage": {"output_tokens": 3}},
    ]
    payload = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
    client = ClaudeClient(api_key="offline", base_url="https://offline.invalid")
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
    )
    try:
        stream = await client.chat.completions.create(
            model="claude-sonnet-5",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
        deltas = [delta async for delta in stream]
    finally:
        await client.close()
    usage = next(
        (delta.usage for delta in reversed(deltas) if getattr(delta, "usage", None)),
        None,
    )
    assert usage is not None
    assert usage.prompt_tokens == 115
    assert usage.completion_tokens == 3
    assert usage.total_tokens == 118
    assert usage.cache_read_input_tokens == 100
    assert usage.cache_creation_input_tokens == 5


@pytest.mark.asyncio
async def test_cache_usage_zero_hit_is_unknown_not_miss():
    from excelmanus.request.usage import extract_cache_usage

    zero = extract_cache_usage({
        "prompt_tokens": 500,
        "prompt_tokens_details": {"cached_tokens": 0},
    })
    assert zero.hit == 0
    assert zero.miss_reason == "unknown"

    negative = extract_cache_usage({
        "prompt_tokens": 500,
        "prompt_tokens_details": {"cached_tokens": -3},
    })
    assert negative.hit is None
    assert negative.miss_reason == "unknown"

    write = extract_cache_usage({
        "prompt_tokens": 500,
        "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 7},
    })
    assert write.write == 7


async def _responses_replay_prepared(scenario: str, *, base_url: str | None = None):
    from excelmanus.request.route import resolve_route

    engine = engine_for("openai_responses")
    if base_url is not None:
        engine._active_base_url = base_url
    first = await prepare(engine)
    series_of(engine).accept(first.header)
    engine.memory.add_assistant_message("ok")
    assistant = engine.memory.messages[-1]
    assistant["replay_state"] = {"response_id": "resp_1"}
    route = resolve_route(engine)
    source = {
        "protocol": "openai_responses",
        "model": route.model,
        "compaction_generation": 0,
    }
    if scenario != "unscoped":
        source.update({
            "credential_scope": (
                "other" if scenario == "foreign_scope" else route.credential_scope
            ),
            "request_content_identity": first.header.content_identity,
            "output_identity": digest_text(compiler.content_payload([{
                "role": "assistant",
                "content": "ok",
                "replay_state": {"response_id": "resp_1"},
            }])),
        })
    assistant["replay_source"] = source
    engine.memory.add_user_message("after")
    prepared = await prepare(engine, extra={"_responses_previous_response_id": "resp_1"})
    return engine, prepared


def _input_texts(body: dict) -> list:
    return [
        item.get("content")
        for item in body.get("input") or []
        if isinstance(item, dict) and item.get("type") == "message"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["unscoped", "valid", "foreign_scope"])
async def test_responses_previous_id_requires_scoped_replay_source(scenario):
    _, prepared = await _responses_replay_prepared(scenario)
    body = prepared.create_kwargs()["_prepared_body"]
    texts = _input_texts(body)
    if scenario == "valid":
        assert body["previous_response_id"] == "resp_1"
        assert "after" in texts
        assert "first" not in texts
        assert "ok" not in texts
    else:
        assert "previous_response_id" not in body
        assert "first" in texts
        assert "ok" in texts
        assert "after" in texts


@pytest.mark.asyncio
async def test_stateless_responses_host_disables_stored_continuation():
    from excelmanus.request.route import resolve_route

    deepseek = engine_for("openai_responses")
    deepseek._active_base_url = "https://api.deepseek.com/v1"
    assert resolve_route(deepseek).capabilities["stored_responses"] is False

    openai_engine = engine_for("openai_responses")
    openai_engine._active_base_url = "https://api.openai.com/v1"
    assert resolve_route(openai_engine).capabilities["stored_responses"] is True

    _, prepared = await _responses_replay_prepared(
        "valid", base_url="https://api.deepseek.com/v1"
    )
    body = prepared.create_kwargs()["_prepared_body"]
    assert "previous_response_id" not in body
    assert "background" not in body
    assert body["store"] is False
    texts = _input_texts(body)
    assert "first" in texts and "ok" in texts and "after" in texts


@pytest.mark.asyncio
async def test_request_body_never_sends_previous_id_with_full_replay():
    from excelmanus.providers.request_body import responses_body

    extra = {"_responses_previous_response_id": "resp_x"}
    body = responses_body(
        "offline-model",
        [{"role": "user", "content": "hi"}],
        extra_kwargs=extra,
    )
    assert "previous_response_id" not in body
    assert extra == {"_responses_previous_response_id": "resp_x"}


@pytest.mark.asyncio
async def test_previous_id_rejection_retries_without_continuation():
    engine, prepared = await _responses_replay_prepared("valid")
    body = prepared.create_kwargs()["_prepared_body"]
    assert body["previous_response_id"] == "resp_1"

    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("previous_response_id not found")
        return SimpleNamespace(choices=[], usage=None)

    engine._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    await LLMCaller(engine).create_chat_completion_with_retry(
        prepared.create_kwargs()
    )
    assert len(calls) == 2
    retry_body = calls[1]["_prepared_body"]
    assert "previous_response_id" not in retry_body
    texts = _input_texts(retry_body)
    assert "first" in texts and "ok" in texts and "after" in texts


@pytest.mark.asyncio
async def test_chat_stream_and_subscribe_emit_heartbeat_while_idle(monkeypatch):
    from unittest.mock import AsyncMock, patch

    from httpx import ASGITransport, AsyncClient

    import excelmanus.api_routes_chat as chat_routes
    from excelmanus.api import app
    from excelmanus.engine import ChatResult
    from tests.test_api import _setup_api_globals

    monkeypatch.setattr(chat_routes, "_CHAT_HEARTBEAT_SECONDS", 0.05)

    async def slow_chat(_: str, **kwargs) -> ChatResult:
        await asyncio.sleep(0.3)
        return ChatResult(reply="ok")

    with _setup_api_globals():
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            with patch(
                "excelmanus.engine.AgentEngine.followup",
                new_callable=AsyncMock,
                side_effect=slow_chat,
            ):
                resp = await client.post(
                    "/api/v1/chat/stream",
                    json={"message": "heartbeat probe"},
                )

    assert resp.status_code == 200
    body = resp.text
    assert body.count("event: heartbeat") >= 2
    assert "event: heartbeat\ndata: {}" in body
    assert body.index("event: heartbeat") < body.index("event: reply")
    assert "event: done" in body
    assert "event: error" not in body


@pytest.mark.asyncio
async def test_loaded_tools_survive_snapshot_and_reset():
    from excelmanus.engine_core.session_state import SessionState

    state = SessionState()
    state.loaded_tool_names = {"b", "a"}
    assert state.to_dict()["loaded_tool_names"] == ["a", "b"]
    restored = SessionState.from_dict(state.to_dict())
    assert restored.loaded_tool_names == {"a", "b"}
    dirty = SessionState.from_dict({"loaded_tool_names": ["a", 5, "", "b", None]})
    assert dirty.loaded_tool_names == {"a", "b"}
    restored.reset_session()
    assert restored.loaded_tool_names == set()


@pytest.mark.asyncio
async def test_sort_tool_schemas_normalizes_nested_key_order():
    from excelmanus.prompt.envelope import sort_tool_schemas

    schema_a = [{
        "function": {"name": "run_code", "parameters": {"b": 1, "a": {"y": 2, "x": 1}},
                     "description": "d"},
        "type": "function",
    }]
    schema_b = [{
        "type": "function",
        "function": {"parameters": {"a": {"x": 1, "y": 2}, "b": 1},
                     "description": "d", "name": "run_code"},
    }]
    out_a = sort_tool_schemas(schema_a)
    out_b = sort_tool_schemas(schema_b)
    assert json.dumps(out_a, ensure_ascii=False) == json.dumps(out_b, ensure_ascii=False)
    assert schema_a[0]["function"]["parameters"]["a"] == {"y": 2, "x": 1}
    assert list(schema_b[0].keys()) == ["type", "function"]


@pytest.mark.asyncio
async def test_trace_request_records_cache_identity():
    from unittest.mock import AsyncMock

    from excelmanus.trace import TraceRecorder, traced_request

    engine = engine_for()
    prepared = await prepare(engine)
    engine._sent_prepared_request = prepared
    engine._trace = TraceRecorder()
    engine._model_idle_seconds = 12.5
    send = AsyncMock(return_value=SimpleNamespace(choices=[], usage=None))
    await traced_request(engine, send, prepared.create_kwargs())
    send.assert_awaited_once()
    span = next(s for s in engine._trace.spans if s.kind == "request")
    assert span.attributes["prompt_cache_key"] == prepared.header.prompt_cache_key
    assert span.attributes["model_idle_seconds"] == 12.5
    assert span.attributes["tools_digest"]


@pytest.mark.asyncio
async def test_idle_tracker_exclusive_buckets():
    import time

    from excelmanus.engine_core.idle_tracker import idle_segment

    engine = SimpleNamespace()
    started = time.monotonic()
    with idle_segment(engine, "tool"):
        await asyncio.sleep(0.03)
        with idle_segment(engine, "approval"):
            await asyncio.sleep(0.06)
        await asyncio.sleep(0.03)
    outer = time.monotonic() - started
    totals = engine._idle_tracker["totals"]
    assert engine._idle_tracker["stack"] == []
    # 内层时长计入 approval，并从外层 tool 中独占扣除。
    assert totals["approval"] >= 0.05
    assert totals["approval"] <= outer + 0.5
    assert totals["tool"] >= 0.05
    assert totals["tool"] <= outer - totals["approval"] + 0.5
    assert totals["tool"] + totals["approval"] <= outer + 0.5


@pytest.mark.asyncio
async def test_idle_tracker_resets_each_gap():
    from excelmanus.engine_core.idle_tracker import idle_segment, reset_idle_tracker

    engine = SimpleNamespace()
    with idle_segment(engine, "tool"):
        await asyncio.sleep(0.01)
    assert engine._idle_tracker["totals"]
    reset_idle_tracker(engine)
    assert engine._idle_tracker["totals"] == {}
    assert engine._idle_tracker["stack"] == []


@pytest.mark.asyncio
async def test_idle_breakdown_includes_residual_other():
    import time
    from unittest.mock import AsyncMock

    from excelmanus.trace import TraceRecorder

    engine = engine_for()
    prepared = await prepare(engine)
    engine._trace = TraceRecorder()
    engine._idle_tracker = {"totals": {"tool": 1.0}, "stack": []}
    engine._last_model_response_at = time.monotonic() - 5
    create = AsyncMock(return_value=SimpleNamespace(choices=[], usage=None))
    engine._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    await LLMCaller(engine).create_chat_completion_with_retry(
        prepared.create_kwargs()
    )
    create.assert_awaited_once()
    breakdown = engine._model_idle_breakdown
    assert breakdown["tool"] == 1.0
    assert breakdown["other"] == pytest.approx(engine._model_idle_seconds - 1.0)
    assert 3.0 <= breakdown["other"] <= 6.0
    span = next(s for s in engine._trace.spans if s.kind == "request")
    assert span.attributes["idle_breakdown"] == breakdown
    assert span.attributes["model_idle_seconds"] == engine._model_idle_seconds


@pytest.mark.asyncio
async def test_question_wait_records_question_segment():
    from excelmanus.engine_core.idle_tracker import idle_segment
    from excelmanus.engine_core.interaction_handler import InteractionHandler

    engine = engine_for()
    engine._interaction_handler = InteractionHandler(engine)

    async def resolver(_pending):
        await asyncio.sleep(0.05)
        return "ok"

    engine._question_resolver = resolver
    pending_q = SimpleNamespace(question_id="q1", raw="?")
    result = await engine._interaction_handler.await_question_answer(pending_q)
    assert result is not None
    totals = engine._idle_tracker["totals"]
    assert totals["question"] > 0
    assert totals["question"] < 5.0
    # 再确认段挂在 engine 上且可叠加：外层同名段不再重复计满。
    with idle_segment(engine, "question"):
        pass
    assert engine._idle_tracker["totals"]["question"] >= 0.04


def _collect_cache_controls(value) -> list:
    markers = []
    if isinstance(value, dict):
        marker = value.get("cache_control")
        if isinstance(marker, dict):
            markers.append(marker)
        for item in value.values():
            markers.extend(_collect_cache_controls(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            markers.extend(_collect_cache_controls(item))
    return markers


def _engine_with_retention(protocol, base_url, retention):
    engine = engine_for(protocol)
    engine._active_base_url = base_url
    object.__setattr__(engine._config, "prompt_cache_retention", retention)
    return engine


@pytest.mark.asyncio
async def test_retention_capability_only_first_party():
    from excelmanus.request.route import resolve_route

    def cache_retention(protocol, base_url, retention="extended"):
        engine = _engine_with_retention(protocol, base_url, retention)
        return resolve_route(engine).cache_retention

    assert cache_retention("anthropic", "https://api.anthropic.com") == "extended"
    assert cache_retention("anthropic", "https://staging.anthropic.com") == "extended"
    assert cache_retention("openai", "https://api.openai.com/v1") == "extended"
    assert cache_retention("openai_responses", "https://api.openai.com/v1") == "extended"
    # 兼容网关 / 自部署 / 其他协议：即使配置为 extended 也一律不支持。
    assert cache_retention("anthropic", "https://gateway.example.com/anthropic") == ""
    assert cache_retention("anthropic", "https://anthropic.example.com") == ""
    assert cache_retention("openai", "https://api.deepseek.com/v1") == ""
    assert cache_retention("openai_responses", "https://api.deepseek.com/v1") == ""
    assert cache_retention("openai", "https://openai.example.com/v1") == ""
    assert cache_retention("gemini", "https://generativelanguage.googleapis.com") == ""
    # default 配置即使一方端点也不启用。
    assert cache_retention("anthropic", "https://api.anthropic.com", "default") == ""
    assert cache_retention("openai", "https://api.openai.com/v1", "default") == ""
    # config 缺失按 default 处理。
    engine = engine_for("anthropic")
    engine._active_base_url = "https://api.anthropic.com"
    engine._config = None
    engine.config = None
    assert resolve_route(engine).cache_retention == ""


@pytest.mark.asyncio
async def test_claude_extended_retention_marks_ttl_and_beta():
    engine = _engine_with_retention(
        "anthropic", "https://api.anthropic.com", "extended"
    )
    prepared = await prepare(engine)
    kwargs = prepared.create_kwargs()
    markers = _collect_cache_controls(kwargs["_prepared_body"])
    assert markers
    assert all(marker.get("ttl") == "1h" for marker in markers)
    beta = kwargs["extra_headers"]["anthropic-beta"]
    assert "extended-cache-ttl-2025-04-11" in beta.split(",")
    # transport_headers 冻结在 prepared 上，且与 kwargs 一致。
    assert prepared.transport_headers["anthropic-beta"] == beta

    # default 配置：marker 不加 ttl、不发 beta header。
    plain = await prepare(
        _engine_with_retention("anthropic", "https://api.anthropic.com", "default")
    )
    plain_kwargs = plain.create_kwargs()
    plain_markers = _collect_cache_controls(plain_kwargs["_prepared_body"])
    assert plain_markers
    assert all("ttl" not in marker for marker in plain_markers)
    assert "extra_headers" not in plain_kwargs

    # 兼容网关：即使配置 extended 也不加 ttl、不发 beta。
    gateway = await prepare(
        _engine_with_retention(
            "anthropic", "https://gateway.example.com/anthropic", "extended"
        )
    )
    gateway_kwargs = gateway.create_kwargs()
    gateway_markers = _collect_cache_controls(gateway_kwargs["_prepared_body"])
    assert gateway_markers
    assert all("ttl" not in marker for marker in gateway_markers)
    assert "extra_headers" not in gateway_kwargs

    # 已有 anthropic-beta 值时合并去重而非覆盖。
    merged_engine = _engine_with_retention(
        "anthropic", "https://api.anthropic.com", "extended"
    )
    merged = await prepare(
        merged_engine,
        extra={"extra_headers": {"anthropic-beta": "extended-cache-ttl-2025-04-11,other-beta"}},
    )
    betas = merged.create_kwargs()["extra_headers"]["anthropic-beta"].split(",")
    assert betas.count("extended-cache-ttl-2025-04-11") == 1
    assert "other-beta" in betas


@pytest.mark.asyncio
async def test_openai_extended_retention_sets_field():
    for protocol in ("openai", "openai_responses"):
        engine = _engine_with_retention(
            protocol, "https://api.openai.com/v1", "extended"
        )
        prepared = await prepare(engine)
        kwargs = prepared.create_kwargs()
        body = kwargs.get("_prepared_body") or kwargs
        assert body["prompt_cache_retention"] == "24h"

        default_engine = _engine_with_retention(
            protocol, "https://api.openai.com/v1", "default"
        )
        default_kwargs = (await prepare(default_engine)).create_kwargs()
        default_body = default_kwargs.get("_prepared_body") or default_kwargs
        assert "prompt_cache_retention" not in default_body

    for protocol in ("openai", "openai_responses"):
        deepseek = _engine_with_retention(
            protocol, "https://api.deepseek.com/v1", "extended"
        )
        kwargs = (await prepare(deepseek)).create_kwargs()
        body = kwargs.get("_prepared_body") or kwargs
        assert "prompt_cache_retention" not in body


@pytest.mark.asyncio
async def test_retention_config_parse():
    from excelmanus.config import _load_context_optimization_config, load_config
    from excelmanus.settings_runtime import using_values

    # get_setting 不读进程环境；using_values 等价于主库/覆盖层中的设置值。
    with using_values({"EXCELMANUS_PROMPT_CACHE_RETENTION": "extended"}):
        assert _load_context_optimization_config().prompt_cache_retention == "extended"
    with using_values({"EXCELMANUS_PROMPT_CACHE_RETENTION": " Extended "}):
        assert _load_context_optimization_config().prompt_cache_retention == "extended"
    with using_values({"EXCELMANUS_PROMPT_CACHE_RETENTION": "bogus"}):
        assert _load_context_optimization_config().prompt_cache_retention == "default"
    with using_values({}):
        assert _load_context_optimization_config().prompt_cache_retention == "default"

    config = load_config({
        "EXCELMANUS_API_KEY": "k",
        "EXCELMANUS_BASE_URL": "https://api.openai.com/v1",
        "EXCELMANUS_MODEL": "gpt-5",
        "EXCELMANUS_PROMPT_CACHE_RETENTION": "extended",
    })
    assert config.prompt_cache_retention == "extended"


@pytest.mark.asyncio
async def test_mcp_tool_set_change_is_classified_not_prefix_rewrite():
    engine = engine_for()
    first = await prepare(engine)
    series_of(engine).accept(first.header)
    baseline_tools = list(engine._meta_tool_builder.build_v5_tools.return_value)
    baseline_names = list(engine._registry.get_tool_names.return_value)

    # MCP 工具上线：授权目录与 wire schema 同步新增一个 MCP 工具。
    mcp_tool = {
        "type": "function",
        "function": {"name": "mcp_docs_search", "description": "d", "parameters": {}},
    }
    engine._meta_tool_builder.build_v5_tools.return_value = [*baseline_tools, mcp_tool]
    engine._registry.get_tool_names.return_value = [*baseline_names, "mcp_docs_search"]
    engine.memory.add_user_message("after mcp join")

    added = await prepare(engine)
    assert added.header.tools_digest != first.header.tools_digest
    assert added.header.catalog_digest != first.header.catalog_digest
    # 消息仍是追加式：内容身份与原生逐块前缀都只做前缀延伸。
    assert added.header.content_payload.startswith(first.header.content_payload)
    added_prefix = added.header.provider_prefix
    assert added_prefix[:len(first.header.provider_prefix)] == first.header.provider_prefix
    # 变化被分类为 catalog/change；编译成功本身即证明没有误报历史改写。
    assert any(e["type"] == "catalog/change" for e in series_of(engine).events)
    assert not any(e["type"] == "request/failed" for e in series_of(engine).events)
    series_of(engine).accept(added.header)

    # MCP 工具下线：同样归类为 catalog/change，而不是前缀改写错误。
    engine._meta_tool_builder.build_v5_tools.return_value = baseline_tools
    engine._registry.get_tool_names.return_value = baseline_names
    engine.memory.add_user_message("after mcp leave")
    removed = await prepare(engine)
    assert removed.header.tools_digest == first.header.tools_digest
    removed_prefix = removed.header.provider_prefix
    assert removed_prefix[:len(added_prefix)] == added_prefix
    assert sum(e["type"] == "catalog/change" for e in series_of(engine).events) >= 2
    assert series_of(engine).last_accepted == added.header


@pytest.mark.asyncio
async def test_chat_subscribe_emits_heartbeat_while_idle(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    import excelmanus.api_routes_chat as chat_routes
    from excelmanus.api import app
    from excelmanus.api_sse import SessionStreamState
    from excelmanus.engine import ChatResult
    from tests.test_api import _setup_api_globals

    monkeypatch.setattr(chat_routes, "_CHAT_HEARTBEAT_SECONDS", 0.05)

    async def idle_chat() -> ChatResult:
        await asyncio.sleep(0.3)
        return ChatResult(reply="ok")

    with _setup_api_globals() as state:
        manager = state["manager"]
        # 轻量构造活跃 stream：真实会话 + 注入 stream_state 与在途 chat 任务，
        # subscribe 与 /chat/stream 共用同一等待循环，无需先跑完整流式请求。
        session_id, _engine = await manager.acquire_for_chat(None)
        runtime = app.state.runtime
        runtime.session_stream_states[session_id] = SessionStreamState()
        runtime.active_chat_tasks[session_id] = asyncio.create_task(idle_chat())
        try:
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/v1/chat/subscribe",
                    json={"session_id": session_id, "after_seq": 0},
                )
        finally:
            runtime.active_chat_tasks.pop(session_id, None)
            runtime.session_stream_states.pop(session_id, None)
            await manager.release_for_chat(session_id)

    assert resp.status_code == 200
    body = resp.text
    assert "event: session_init" in body
    assert '"status": "reconnected"' in body
    assert "event: heartbeat\ndata: {}" in body
    assert body.index("event: heartbeat") < body.index("event: done")
    assert "event: error" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["/compact", "/rollback", "/clear", "/undo"])
async def test_history_rewrite_commands_rejected_while_running(command):
    from excelmanus.engine_core.command_handler import CommandHandler

    engine = engine_for()
    engine._command_handler = CommandHandler(engine)
    engine._driver = SimpleNamespace(running=True)
    series = series_of(engine)
    series_id = series.series_id
    messages_before = len(engine.memory.messages)

    reply = await engine._command_handler.handle(command)
    assert reply is not None and "正在执行" in reply
    # 历史与 series 基线均未被执行路径触碰。
    assert len(engine.memory.messages) == messages_before
    assert series.series_id == series_id
    assert series.last_accepted is None

    # driver 空闲后同一入口恢复正常路径。
    engine._driver = SimpleNamespace(running=False)
    idle_reply = await engine._command_handler.handle("/rollback")
    assert idle_reply is not None and "正在执行" not in idle_reply
    assert "用户对话轮次" in idle_reply
