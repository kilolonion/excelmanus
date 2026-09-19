import json
from types import SimpleNamespace

import httpx
import pytest

from excelmanus.engine_core.llm_caller import LLMCaller, reset_degraded_params
from excelmanus.providers.claude import ClaudeClient
from excelmanus.request.compiler import compile_request
from tests.test_request_envelope import _engine


def engine_for(protocol="openai"):
    engine = _engine()
    engine._active_model = "offline-model"
    engine._active_base_url = "https://offline.invalid/v1"
    engine._active_api_key = "offline-only"
    engine._active_protocol = protocol
    engine._thinking_config = SimpleNamespace(is_disabled=True, effective_budget=lambda: 0)
    engine._model_capabilities = None
    engine.memory.add_user_message("first")
    return engine


@pytest.mark.asyncio
async def test_nested_request_frozen_and_transport_copy_detached():
    prepared, err = await compile_request(engine_for())
    assert err is None
    with pytest.raises(TypeError):
        prepared.provider_body["messages"][0]["content"] = "mutated"
    kwargs = prepared.create_kwargs()
    kwargs["messages"][-1]["content"] = "mutated"
    assert prepared.create_kwargs()["messages"][-1]["content"] == "first"
    assert prepared.header.provider_digest


@pytest.mark.asyncio
async def test_signed_stream_survives_durable_history_and_native_compile():
    engine = engine_for("anthropic")
    frames = [
        {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}},
        {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"reason"}},
        {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"sig-offline"}},
        {"type":"content_block_stop","index":0},
        {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"call1","name":"inspect_spreadsheet"}},
        {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{}"}},
        {"type":"content_block_stop","index":1},
    ]
    payload = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
    client = ClaudeClient(api_key="offline", base_url="https://offline.invalid")
    await client._http.aclose()
    requests = []
    def response(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, text=payload)
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(response))
    engine._client = client
    try:
        first, err = await compile_request(engine)
        assert err is None
        kwargs = first.create_kwargs()
        stream = await LLMCaller(engine).create_chat_completion_with_retry({**kwargs, "stream": True})
        message, _ = await LLMCaller(engine).consume_stream(stream, None, 1)
        assert message.replay_state["thinking_blocks"][0]["signature"] == "sig-offline"
        from excelmanus.message_serialization import assistant_message_to_dict
        message.replay_source = {"protocol": "anthropic", "model": "offline-model"}
        engine.memory.add_assistant_tool_message(assistant_message_to_dict(message))
        engine.memory.add_tool_result("call1", "ok")
        second, err = await compile_request(engine)
        assert err is None
        native = second.create_kwargs()["_prepared_body"]
        assistant = next(m for m in native["messages"] if m["role"] == "assistant")
        assert assistant["content"][0]["signature"] == "sig-offline"
        await client.chat.completions.create(**second.create_kwargs(), stream=True)
        assert "system" in first.provider_body
        assert requests[0]["messages"] == kwargs["_prepared_body"]["messages"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_degrade_records_actual_sent_attempt():
    reset_degraded_params()
    engine = engine_for()
    calls = []
    async def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ValueError("unknown parameter prompt_cache_key")
        return SimpleNamespace(choices=[])
    engine._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    first, err = await compile_request(engine)
    assert err is None
    await LLMCaller(engine).create_chat_completion_with_retry(first.create_kwargs())
    sent = engine._sent_prepared_request
    assert sent.request_id != first.request_id
    assert sent is engine._prepared_request
    assert "prompt_cache_key" not in calls[-1]
    assert sent.create_kwargs() == calls[-1]
    reset_degraded_params()
