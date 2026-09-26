from __future__ import annotations

import re
from pathlib import Path

from excelmanus.config import (
    _DEFAULT_CONTEXT_TOKENS,
    _infer_context_tokens_for_model,
    get_deprecated_model_replacement,
)


_ROOT = Path(__file__).resolve().parents[1]
_README_CN = _ROOT / "README.md"
_README_EN = _ROOT / "README_EN.md"
_PRESETS = _ROOT / "web" / "src" / "components" / "settings" / "model" / "constants.tsx"
_PROVIDER_GUIDES = _ROOT / "web" / "src" / "components" / "onboarding" / "provider-guides.ts"


_BARE_ENV_KEYS = (
    "API_KEY",
    "BASE_URL",
    "MODEL",
    "IMAGE_PIXEL_BUDGET",
    "SESSION_SUMMARY_ENABLED",
    "SESSION_SUMMARY_MIN_TURNS",
)


def test_readme_quick_config_uses_database_settings() -> None:
    cn = _README_CN.read_text(encoding="utf-8")
    en = _README_EN.read_text(encoding="utf-8")

    for text in (cn, en):
        assert "`GUARD_MODE`" not in text
        assert "`EXCELMANUS_GUARD_MODE`" not in text
        assert "`model_profiles`" in text
        assert "`config_kv`" in text
        assert "`EXCELMANUS_HOME`" in text
        for key in _BARE_ENV_KEYS:
            assert f"`{key}`" not in text, key
            assert f"`EXCELMANUS_{key}`" not in text, key


def test_presets_share_the_sourced_catalog() -> None:
    from excelmanus.model_catalog import catalog
    presets = _PRESETS.read_text(encoding="utf-8")
    guides = _PROVIDER_GUIDES.read_text(encoding="utf-8")
    for provider in catalog()["providers"]:
        assert f'...providerDefaults("{provider}")' in presets
    assert 'model: preset.model' in guides
    assert catalog()["providers"]["anthropic"]["model"] == "claude-sonnet-4-6"
    assert catalog()["providers"]["deepseek"]["model"] == "deepseek-flash"
    assert catalog()["providers"]["gemini"]["thinking_mode"] == "auto"


def test_unverified_context_is_a_local_budget_not_a_claim() -> None:
    assert _DEFAULT_CONTEXT_TOKENS == 32_000
    assert _infer_context_tokens_for_model("custom-model") == 32_000
    assert _infer_context_tokens_for_model("gpt-4.10-mystery") == 32_000


def test_verified_budgets_use_input_and_transport_limits() -> None:
    assert _infer_context_tokens_for_model("gpt-6-astra") == 1_050_000
    assert _infer_context_tokens_for_model("gemini-2.5-flash") == 1_048_576
    assert _infer_context_tokens_for_model("qwen-max") == 30_720
    assert _infer_context_tokens_for_model("qwen-turbo") == 98_304
    assert _infer_context_tokens_for_model("qwen-long") == 1_000_000
    assert _infer_context_tokens_for_model("claude-sonnet-4.6") == 1_000_000
    assert _infer_context_tokens_for_model("kimi-k2.6") == 256_000


def test_redirects_are_not_retired_models() -> None:
    assert get_deprecated_model_replacement("deepseek-v4-flash") is None
    assert get_deprecated_model_replacement("claude-sonnet-4") == ("claude-sonnet-4", "claude-sonnet-4-6")
    assert get_deprecated_model_replacement("custom/legacy-model") is None


def test_codex_catalog_is_shared_and_excludes_unverified_id() -> None:
    from excelmanus.model_catalog import catalog
    from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
    expected = {m["model"] for m in catalog()["codex_models"]}
    assert "gpt-5-codex-mini" not in expected
    assert set(OpenAICodexProvider.list_supported_models()) == expected
    assert "MODEL_CATALOG.codex_models.map" in _PRESETS.read_text(encoding="utf-8")
