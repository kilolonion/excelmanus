"""Settings UI contract: valid values persist unchanged and automatic context is reversible."""
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from excelmanus import api_routes_config as routes
from excelmanus.config import ExcelManusConfig, load_config
from excelmanus.settings_runtime import override_settings


@pytest.fixture
def runtime(monkeypatch):
    config = ExcelManusConfig(api_key="offline", base_url="https://example.com/v1", model="test-model")
    saved = {}

    def persist(values):
        saved.update(values)
        override_settings(values)

    monkeypatch.setattr(routes, "get_config", lambda: config)
    monkeypatch.setattr(routes, "get_session_manager", lambda: None)
    monkeypatch.setattr(routes, "_persist_settings", persist)
    return config, saved


@pytest.mark.asyncio
async def test_context_override_can_return_to_automatic(runtime, monkeypatch):
    config, saved = runtime
    manager = type("Manager", (), {"broadcast_context_optimization": AsyncMock()})()
    monkeypatch.setattr(routes, "get_session_manager", lambda: manager)
    await routes.update_runtime_config(routes.RuntimeConfigUpdate(max_context_tokens=64000), None)
    before = json.loads((await routes.get_runtime_config(None)).body)
    assert before["max_context_tokens_override"] == 64000
    await routes.update_runtime_config(routes.RuntimeConfigUpdate(max_context_tokens_override=0), None)
    assert saved["EXCELMANUS_MAX_CONTEXT_TOKENS"] == ""
    assert config.max_context_tokens == 256000
    manager.broadcast_context_optimization.assert_awaited_with(
        max_context_tokens=0, compaction_enabled=None, compaction_threshold_ratio=None,
    )
    after = json.loads((await routes.get_runtime_config(None)).body)
    assert after["max_context_tokens_override"] == 0
    assert after["max_context_tokens"] == 256000
    reloaded = load_config(allow_incomplete=True)
    assert reloaded.max_context_tokens > 1


@pytest.mark.asyncio
async def test_conflicting_context_fields_do_not_persist(runtime):
    _, saved = runtime
    response = await routes.update_runtime_config(routes.RuntimeConfigUpdate(
        max_context_tokens=64000, max_context_tokens_override=0,
    ), None)
    assert response.status_code == 400
    assert not saved


@pytest.mark.asyncio
async def test_new_settings_survive_reload_and_match_runtime_types(runtime):
    config, saved = runtime
    response = await routes.update_runtime_config(routes.RuntimeConfigUpdate(
        hooks_command_allowlist="python /trusted/hook.py, node /trusted/hook.js",
        skills_discovery_extra_dirs="/one, /two",
        prompt_cache_retention="extended",
        memory_auto_load_lines=350,
        memory_maintenance_interval_hours=48,
        input_cost_per_1k_usd=0.000125,
    ), None)
    assert response.status_code == 200
    assert config.hooks_command_allowlist == ("python /trusted/hook.py", "node /trusted/hook.js")
    assert config.skills_discovery_extra_dirs == ("/one", "/two")
    assert saved["EXCELMANUS_INPUT_COST_PER_1K_USD"] == "0.000125"
    reloaded = load_config(allow_incomplete=True)
    assert reloaded.skills_discovery_extra_dirs == config.skills_discovery_extra_dirs
    assert reloaded.hooks_command_allowlist == config.hooks_command_allowlist
    assert reloaded.prompt_cache_retention == "extended"
    assert reloaded.memory_maintenance_interval_hours == 48
    await routes.update_runtime_config(routes.RuntimeConfigUpdate(hooks_command_allowlist="", skills_discovery_extra_dirs=""), None)
    assert config.hooks_command_allowlist == ()
    assert load_config(allow_incomplete=True).skills_discovery_extra_dirs == ()


@pytest.mark.asyncio
async def test_retry_cross_field_validation_precedes_all_writes(runtime):
    config, saved = runtime
    response = await routes.update_runtime_config(routes.RuntimeConfigUpdate(llm_retry_base_delay_seconds=31, memory_enabled=False), None)
    assert response.status_code == 400
    assert not saved
    assert config.memory_enabled is True


@pytest.mark.parametrize("value", ["garbage", "", 0, -1, "1.5"])
def test_invalid_image_budget_is_rejected_instead_of_silently_replaced(value):
    with pytest.raises(ValidationError):
        routes.RuntimeConfigUpdate(image_pixel_budget=value)


@pytest.mark.parametrize("value, expected", [("LOW", "low"), ("640000", 640000), (1024, 1024)])
def test_valid_image_budget_is_normalized(value, expected):
    assert routes.RuntimeConfigUpdate(image_pixel_budget=value).image_pixel_budget == expected


@pytest.mark.parametrize("value", [float("inf"), float("nan"), -1])
def test_cost_rejects_non_finite_or_negative_values(value):
    with pytest.raises(ValidationError):
        routes.RuntimeConfigUpdate(turn_cost_budget_usd=value)


def test_canonical_model_keeps_automatic_context_unpinned():
    from excelmanus.config import _infer_context_tokens_for_model, is_context_window_user_pinned
    inferred = _infer_context_tokens_for_model("claude-sonnet-4-6")
    assert not is_context_window_user_pinned(inferred, "custom-alias", "claude-sonnet-4-6")
