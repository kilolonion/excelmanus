"""用户配置的可用思考等级为未入目录模型补位的契约测试。

能力目录的裁决永远优先：已收录模型（含"只有开关"的条目）行为不变；
只有目录沉默、思考方言已知且等级能传出去时，用户配置才成为可选等级。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from excelmanus.engine_types import ThinkingConfig
from excelmanus.providers.thinking import compile_thinking, thinking_controls

DECLARED = ["low", "medium", "high", "xhigh"]
URL = "https://gateway.example/v1"
PROBED = SimpleNamespace(supports_thinking=True, thinking_type="enable_thinking")


def controls(model="custom-effort-model", mode="auto", caps=None, requested="medium",
             declared=DECLARED, protocol="openai", base_url=URL):
    return thinking_controls(model, base_url, protocol, mode, caps, requested, declared)


class TestUserDeclaredLevels:
    """未入目录的自定义模型使用用户配置的等级。"""

    def test_custom_model_with_known_dialect_offers_declared_levels(self):
        result = controls(caps=PROBED, requested="xhigh")
        assert result["control_kind"] == "effort"
        assert result["model_allowed_efforts"] == DECLARED
        assert result["levels_source"] == "user_declared"
        assert result["effective_effort"] == "xhigh"

    def test_explicit_thinking_mode_also_declares_levels(self):
        result = controls(mode="glm_thinking")
        assert result["control_kind"] == "effort"
        assert result["model_allowed_efforts"] == DECLARED

    def test_declared_levels_keep_canonical_order(self):
        result = controls(caps=PROBED, declared=["xhigh", "low"])
        assert result["model_allowed_efforts"] == ["low", "xhigh"]

    def test_none_is_offered_when_disable_is_possible(self):
        result = controls(caps=PROBED, declared=["none", "high"])
        assert result["model_allowed_efforts"] == ["none", "high"]

    def test_effective_effort_clamps_to_declared_levels(self):
        result = controls(caps=PROBED, requested="max", declared=["low", "medium"])
        assert result["effective_effort"] == "medium"

    def test_single_off_level_does_not_declare_levels(self):
        result = controls(caps=PROBED, declared=["none"])
        assert result["control_kind"] == "unknown"
        assert result["model_allowed_efforts"] == []


class TestUndeclaredStaysAutomatic:
    """无法证明等级可用时保持"自动"，不展示发不出去的档位。"""

    def test_custom_model_without_dialect_stays_unknown(self):
        result = controls()
        assert result["control_kind"] == "unknown"
        assert result["model_allowed_efforts"] == []
        assert result["levels_source"] is None

    def test_disabled_mode_never_declares_levels(self):
        result = controls(mode="disabled", caps=PROBED)
        assert result["control_kind"] == "unknown"

    def test_auto_reasoning_dialects_stay_unknown(self):
        for mode in ("deepseek", "reasoning_content_auto"):
            result = controls(mode=mode)
            assert result["control_kind"] == "unknown", mode

    def test_without_declared_config_nothing_changes(self):
        result = thinking_controls("custom-effort-model", URL, "openai", "auto", PROBED, "medium", None)
        assert result["control_kind"] == "unknown"
        assert result["model_allowed_efforts"] == []


class TestCatalogVerdictsWin:
    """已收录模型不受用户配置影响。"""

    def test_catalog_levels_win_over_declared(self):
        result = thinking_controls("gpt-5.2-codex", "https://api.openai.com/v1", "openai_responses",
                                  "auto", None, "medium", ["low"])
        assert result["model_allowed_efforts"] == ["low", "medium", "high", "xhigh"]
        assert result["levels_source"] == "catalog"

    def test_catalog_switch_only_model_stays_toggle(self):
        result = thinking_controls("deepseek-flash", "https://api.deepseek.com/v1", "openai",
                                  "auto", None, "medium", DECLARED)
        assert result["control_kind"] == "toggle"
        assert result["model_allowed_efforts"] == ["none", "high"]

    def test_custom_chat_template_model_gets_toggle_only(self):
        result = controls(mode="chat_template")
        assert result["control_kind"] == "toggle"
        assert result["model_allowed_efforts"] == ["none", "high"]


class TestCompileUsesDeclaredLevels:
    """请求编译与选择器读到同一份等级。"""

    def test_custom_openai_model_sends_declared_effort(self):
        body = compile_thinking("custom-glm", URL, "openai", "glm_thinking",
                                ThinkingConfig(effort="high"), None, DECLARED)
        assert body["extra_body"]["reasoning_effort"] == "high"

    def test_custom_openai_model_without_declaration_sends_switch_only(self):
        body = compile_thinking("custom-glm", URL, "openai", "glm_thinking",
                                ThinkingConfig(effort="high"), None, None)
        assert "reasoning_effort" not in body["extra_body"]

    def test_budget_dialect_follows_declared_effort_ratio(self):
        body = compile_thinking("custom-qwen", URL, "openai", "enable_thinking",
                                ThinkingConfig(effort="max"), None, DECLARED)
        assert body["extra_body"]["enable_thinking"] is True
        assert body["extra_body"]["thinking_budget"] == 16384

    def test_declared_effort_is_clamped_at_compile_time(self):
        body = compile_thinking("custom-glm", URL, "openai", "glm_thinking",
                                ThinkingConfig(effort="max"), None, ["low", "medium"])
        assert body["extra_body"]["reasoning_effort"] == "medium"


from tests.test_model_profile_sync import setup, profile  # noqa: E402,F401 (shared API fixture)


@pytest.mark.asyncio
async def test_thinking_api_exposes_user_declared_levels_for_custom_models(setup):
    from tests.test_model_profile_sync import engine_for

    s = setup
    payload = {**profile("custom-thinking", "custom-effort-model"),
               "base_url": URL, "protocol": "openai"}
    assert (await s.client.post("/api/v1/config/models/profiles", json=payload)).status_code == 201
    sid, engine = await engine_for(s)
    engine.set_model_capabilities(SimpleNamespace(supports_thinking=True, thinking_type="enable_thinking"))
    assert (await s.client.put("/api/v1/thinking",
                               json={"allowed_efforts": ["low", "medium", "high", "xhigh"]})).status_code == 200

    result = await s.client.get("/api/v1/thinking", params={"session_id": sid})
    body = result.json()
    assert body["control_kind"] == "effort"
    assert body["model_allowed_efforts"] == ["low", "medium", "high", "xhigh"]
    assert body["levels_source"] == "user_declared"

    assert (await s.client.put("/api/v1/thinking", json={"session_id": sid, "effort": "xhigh"})).status_code == 200
    assert engine.thinking_config.effort == "xhigh"
    assert (await s.client.put("/api/v1/thinking", json={"session_id": sid, "effort": "max"})).status_code == 400
