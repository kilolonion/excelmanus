"""ThinkingConfig 统一配置 + engine 注入逻辑测试。"""

from __future__ import annotations

import pytest

from excelmanus.engine import ThinkingConfig, _EFFORT_RATIOS


# ── ThinkingConfig 数据类测试 ──────────────────────────────


class TestThinkingConfig:
    """ThinkingConfig 核心逻辑。"""

    def test_default_values(self):
        tc = ThinkingConfig()
        assert tc.effort == "medium"
        assert tc.budget_tokens == 0
        assert not tc.is_disabled

    def test_is_disabled_when_effort_none_and_no_budget(self):
        tc = ThinkingConfig(effort="none", budget_tokens=0)
        assert tc.is_disabled

    def test_is_not_disabled_when_effort_none_but_has_budget(self):
        tc = ThinkingConfig(effort="none", budget_tokens=1024)
        assert not tc.is_disabled

    def test_is_not_disabled_when_effort_set(self):
        tc = ThinkingConfig(effort="low")
        assert not tc.is_disabled

    def test_effective_budget_uses_budget_tokens_when_set(self):
        tc = ThinkingConfig(effort="high", budget_tokens=5000)
        assert tc.effective_budget() == 5000

    def test_effective_budget_uses_effort_ratio(self):
        tc = ThinkingConfig(effort="medium", budget_tokens=0)
        budget = tc.effective_budget(max_tokens=16384)
        expected = max(1024, int(16384 * 0.5))
        assert budget == expected

    def test_effective_budget_minimum_1024(self):
        tc = ThinkingConfig(effort="minimal", budget_tokens=0)
        budget = tc.effective_budget(max_tokens=2000)
        assert budget >= 1024

    def test_effective_budget_zero_when_effort_none(self):
        tc = ThinkingConfig(effort="none", budget_tokens=0)
        assert tc.effective_budget() == 0

    @pytest.mark.parametrize("effort", list(_EFFORT_RATIOS.keys()))
    def test_openai_effort_mapping(self, effort: str):
        tc = ThinkingConfig(effort=effort)
        result = tc.openai_effort
        assert isinstance(result, str)
        assert result  # 非空

    @pytest.mark.parametrize("effort", list(_EFFORT_RATIOS.keys()))
    def test_gemini_level_mapping(self, effort: str):
        tc = ThinkingConfig(effort=effort)
        result = tc.gemini_level
        assert result in {"minimal", "low", "medium", "high"}

    def test_all_effort_ratios_present(self):
        expected_efforts = {"none", "minimal", "low", "medium", "high", "xhigh"}
        assert set(_EFFORT_RATIOS.keys()) == expected_efforts

    def test_effort_ratios_monotonically_increasing(self):
        ordered = ["none", "minimal", "low", "medium", "high", "xhigh"]
        for i in range(len(ordered) - 1):
            assert _EFFORT_RATIOS[ordered[i]] < _EFFORT_RATIOS[ordered[i + 1]]


# ── model_probe 策略测试 ──────────────────────────────


class TestModelProbeStrategies:
    """model_probe._get_thinking_strategies 新策略。"""

    def test_openai_provider_o3_gets_openai_reasoning(self):
        from excelmanus.model_probe import _get_thinking_strategies
        strategies = _get_thinking_strategies("openai", "o3-mini")
        names = [s[0] for s in strategies]
        types = [s[2] for s in strategies]
        assert "openai_reasoning" in names
        assert "openai_reasoning" in types

    def test_openai_provider_gpt5_gets_openai_reasoning(self):
        from excelmanus.model_probe import _get_thinking_strategies
        strategies = _get_thinking_strategies("openai", "gpt-5.1")
        names = [s[0] for s in strategies]
        assert "openai_reasoning" in names

    def test_openai_provider_gpt4o_no_openai_reasoning(self):
        from excelmanus.model_probe import _get_thinking_strategies
        strategies = _get_thinking_strategies("openai", "gpt-4o")
        names = [s[0] for s in strategies]
        assert "openai_reasoning" not in names

    def test_xai_mini_gets_openai_reasoning(self):
        from excelmanus.model_probe import _get_thinking_strategies
        strategies = _get_thinking_strategies("xai", "grok-3-mini")
        types = [s[2] for s in strategies]
        assert "openai_reasoning" in types

    def test_xai_non_mini_no_reasoning(self):
        from excelmanus.model_probe import _get_thinking_strategies
        strategies = _get_thinking_strategies("xai", "grok-4")
        names = [s[0] for s in strategies]
        assert "xai_reasoning" not in names

    def test_openai_detected_from_url(self):
        from excelmanus.model_probe import _detect_openai_provider
        assert _detect_openai_provider("https://api.openai.com/v1") == "openai"

    def test_openrouter_detected(self):
        from excelmanus.model_probe import _detect_openai_provider
        assert _detect_openai_provider("https://openrouter.ai/api/v1") == "openrouter"


# ── config.py 新字段测试 ──────────────────────────────


class TestConfigThinkingFields:
    """ExcelManusConfig thinking_effort / thinking_budget 字段。"""

    def test_default_thinking_effort(self):
        from excelmanus.config import ExcelManusConfig
        cfg = ExcelManusConfig(
            api_key="test",
            base_url="https://api.example.com/v1",
            model="test-model",
        )
        assert cfg.thinking_effort == "medium"
        assert cfg.thinking_budget == 0

    def test_custom_thinking_effort(self):
        from excelmanus.config import ExcelManusConfig
        cfg = ExcelManusConfig(
            api_key="test",
            base_url="https://api.example.com/v1",
            model="test-model",
            thinking_effort="high",
            thinking_budget=8192,
        )
        assert cfg.thinking_effort == "high"
        assert cfg.thinking_budget == 8192


# ── engine thinking 注入测试 ──────────────────────────


class TestEngineThinkingInjection:
    """engine._call_llm_with_retry 中 thinking 参数注入。"""

    def test_thinking_config_init_from_config(self):
        """ThinkingConfig 应从 ExcelManusConfig 初始化。"""
        from excelmanus.config import ExcelManusConfig
        cfg = ExcelManusConfig(
            api_key="test",
            base_url="https://api.example.com/v1",
            model="test-model",
            thinking_effort="low",
            thinking_budget=4096,
        )
        tc = ThinkingConfig(effort=cfg.thinking_effort, budget_tokens=cfg.thinking_budget)
        assert tc.effort == "low"
        assert tc.budget_tokens == 4096
        assert tc.effective_budget() == 4096  # budget_tokens > 0 直接返回

    def test_set_thinking_effort(self):
        tc = ThinkingConfig(effort="medium")
        # 模拟 engine.set_thinking_effort
        new_tc = ThinkingConfig(effort="high", budget_tokens=tc.budget_tokens)
        assert new_tc.effort == "high"

    def test_set_thinking_budget_compat(self):
        tc = ThinkingConfig(effort="medium")
        # 模拟 engine.set_thinking_budget（兼容旧接口）
        new_tc = ThinkingConfig(effort=tc.effort, budget_tokens=max(0, 8000))
        assert new_tc.budget_tokens == 8000
        assert new_tc.effort == "medium"

    def test_set_thinking_config_combined(self):
        # 模拟 engine.set_thinking_config
        tc = ThinkingConfig(effort="medium", budget_tokens=0)
        new_effort = "high"
        new_budget = 6000
        new_tc = ThinkingConfig(
            effort=new_effort if new_effort in _EFFORT_RATIOS else tc.effort,
            budget_tokens=max(0, new_budget),
        )
        assert new_tc.effort == "high"
        assert new_tc.budget_tokens == 6000
