"""Model output limits reach native requests without forwarding modality metadata."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from excelmanus.config import ModelProfile
from excelmanus.request.compiler import compile_request
from tests.test_prepared_request_integration import engine_for


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol, model, key", [
    ("openai", "offline-model", "max_tokens"),
    ("openai", "gpt-6-astra", "max_completion_tokens"),
    ("openai_responses", "openai-codex/gpt-6-astra", "max_output_tokens"),
    ("anthropic", "claude-sonnet-4-6", "max_tokens"),
    ("gemini", "gemini-3-pro", "maxOutputTokens"),
    ("antigravity", "antigravity/claude-sonnet-4-6", "maxOutputTokens"),
])
async def test_profile_output_cap_on_wire(protocol, model, key):
    engine = engine_for(protocol)
    engine._active_model = model
    from excelmanus.engine_types import ThinkingConfig
    engine._thinking_config = ThinkingConfig(effort="low")
    engine._active_profile = ModelProfile(
        name="limited", model=model, api_key="offline-only", base_url=engine._active_base_url,
        max_output_tokens=2048, input_modalities=("text", "image", "audio", "video"),
    )
    prepared, error = await compile_request(engine)
    assert error is None
    body = prepared.provider_body
    values = body.get("generationConfig", body)
    assert values[key] == 2048
    assert "input_modalities" not in body
    assert "audio" not in body and "video" not in body
    if protocol == "antigravity":
        from excelmanus.providers.antigravity import build_antigravity_envelope
        request = build_antigravity_envelope(prepared.create_kwargs()["_prepared_body"], model=model, project_id="offline")
        assert request["request"]["generationConfig"][key] == 2048
    engine._active_profile = replace(engine._active_profile, max_output_tokens=0)
    standard, error = await compile_request(engine)
    assert error is None
    values = standard.provider_body.get("generationConfig", standard.provider_body)
    if protocol == "anthropic":
        assert values[key] != 2048  # Anthropic requires a protocol default.
    else:
        assert key not in values


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["claude-sonnet-4-5", "claude-sonnet-4-6"])
@pytest.mark.parametrize("limit", [512, 2048])
async def test_thinking_defaults_do_not_raise_explicit_output_limit(model, limit):
    engine = engine_for("anthropic")
    engine._active_model = model
    engine._active_profile = ModelProfile(
        name="limited", model=model, api_key="offline-only", base_url=engine._active_base_url,
        max_output_tokens=limit, thinking_mode="claude",
    )
    engine._thinking_config = SimpleNamespace(is_disabled=False, effective_budget=lambda: 8192, claude_effort="high")
    prepared, error = await compile_request(engine)
    assert error is None
    assert prepared.provider_body["max_tokens"] == limit
    thinking = prepared.provider_body.get("thinking", {})
    if thinking.get("type") == "enabled":
        assert 1024 <= thinking["budget_tokens"] < limit
