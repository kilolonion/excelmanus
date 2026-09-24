"""WorkBuddy reasoning detection must use the gateway's reasoning_effort dialect."""

import json
from types import SimpleNamespace

import httpx
import pytest

from excelmanus.engine_types import ThinkingConfig
from excelmanus.model_probe import ModelCapabilities, _detect_openai_provider, probe_thinking
from excelmanus.providers.workbuddy import WorkBuddyClient
from excelmanus.request.compiler import create_extra_from_engine


def _reasoning_response(enabled: bool) -> httpx.Response:
    deltas = [{"role": "assistant", "content": ""}]
    if enabled:
        deltas.append({"reasoning_content": "17 * 20 + 17 * 3 = 391."})
    deltas.append({"content": "391"})
    events = [
        {
            "id": "probe",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "deepseek-v4.1-flash",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        for delta in deltas
    ]
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text="".join(f"data: {json.dumps(event)}\n\n" for event in events)
        + "data: [DONE]\n\n",
    )


@pytest.mark.parametrize("base_url", [
    "https://copilot.tencent.com/v2",
    "https://workbuddy.ai/v2",
    "https://www.workbuddy.ai/v2",
])
def test_workbuddy_gateway_detection(base_url):
    assert _detect_openai_provider(base_url) == "workbuddy"


def test_workbuddy_name_in_unrelated_url_is_not_gateway():
    assert _detect_openai_provider("https://workbuddy.ai.example.com/v2") == "generic"


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", [
    "https://copilot.tencent.com/v2",
    "https://www.workbuddy.ai/v2",
])
@pytest.mark.parametrize("model", ["deepseek-v4.1-flash", "gateway-model-alias"])
async def test_workbuddy_probe_and_chat_enable_reasoning(base_url, model):
    """Detect from a real SDK stream, then replay the detected dialect in chat."""
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        return _reasoning_response(body.get("reasoning_effort") == "high")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = WorkBuddyClient(
            api_key="test", base_url=base_url, http_client=http, max_retries=0,
        )
        supported, error, thinking_type = await probe_thinking(client, model, base_url)

        assert supported is True
        assert error == ""
        assert thinking_type == "openai_reasoning"
        assert len(requests) == 1
        assert requests[0]["model"] == model

        engine = SimpleNamespace(
            _active_model=model,
            _active_protocol="openai",
            _model_capabilities=ModelCapabilities(
                supports_thinking=supported, thinking_type=thinking_type,
            ),
            _thinking_config=ThinkingConfig(effort="high"),
        )
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "What is 17*23?"}],
            **create_extra_from_engine(engine),
        )
        assert requests[-1]["reasoning_effort"] == "high"
        assert response.choices[0].message.reasoning_content
        assert response.choices[0].message.content == "391"


@pytest.mark.asyncio
async def test_workbuddy_rejected_effort_still_probes_plain_stream():
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {
                "message": "Unsupported parameter: reasoning_effort",
                "type": "invalid_request_error",
            }})
        return _reasoning_response(True)

    base_url = "https://www.workbuddy.ai/v2"
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = WorkBuddyClient(
            api_key="test", base_url=base_url, http_client=http, max_retries=0,
        )
        assert await probe_thinking(client, "legacy-reasoner", base_url) == (True, "", "deepseek")

    assert len(requests) == 2
    assert "reasoning_effort" in requests[0]
    assert "reasoning_effort" not in requests[1]
