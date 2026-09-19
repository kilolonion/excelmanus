from __future__ import annotations

import re
from pathlib import Path

from excelmanus.config import (
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


def test_readme_quick_config_uses_prefixed_env_keys() -> None:
    cn = _README_CN.read_text(encoding="utf-8")
    en = _README_EN.read_text(encoding="utf-8")

    for text in (cn, en):
        assert "`GUARD_MODE`" not in text
        assert "`EXCELMANUS_GUARD_MODE`" not in text
        for key in _BARE_ENV_KEYS:
            assert f"`{key}`" not in text, key
            assert f"`EXCELMANUS_{key}`" in text, key


def test_frontend_anthropic_presets_use_latest_sonnet_alias() -> None:
    presets = _PRESETS.read_text(encoding="utf-8")
    guides = _PROVIDER_GUIDES.read_text(encoding="utf-8")

    assert 'model: "claude-sonnet-5"' in presets
    assert 'model: "anthropic/claude-sonnet-5"' in presets
    assert 'model: "claude-sonnet-4-6"' not in presets
    assert 'model: "anthropic/claude-sonnet-4-6"' not in presets

    assert 'model: "claude-sonnet-5"' in guides
    assert 'model: "anthropic/claude-sonnet-5"' in guides
    assert 'model: "claude-sonnet-4-6"' not in guides
    assert 'model: "anthropic/claude-sonnet-4-6"' not in guides


def test_frontend_openai_presets_use_current_recommended_model() -> None:
    presets = _PRESETS.read_text(encoding="utf-8")
    guides = _PROVIDER_GUIDES.read_text(encoding="utf-8")

    assert re.search(r'id:\s*"openai"[\s\S]*?model:\s*"gpt-6-astra"', presets)
    assert re.search(r'id:\s*"openai"[\s\S]*?model:\s*"gpt-6-astra"', guides)


def test_frontend_openai_presets_use_latest_gpt_6_astra() -> None:
    presets = _PRESETS.read_text(encoding="utf-8")
    guides = _PROVIDER_GUIDES.read_text(encoding="utf-8")

    presets_openai = re.search(r'id: "openai",[\s\S]{0,320}?model: "([^"]+)"', presets)
    guide_openai = re.search(r'id: "openai",[\s\S]{0,320}?model: "([^"]+)"', guides)

    assert presets_openai is not None
    assert guide_openai is not None
    assert presets_openai.group(1) == "gpt-6-astra"
    assert guide_openai.group(1) == "gpt-6-astra"


def test_frontend_vendor_presets_use_current_flagships() -> None:
    presets = _PRESETS.read_text(encoding="utf-8")
    guides = _PROVIDER_GUIDES.read_text(encoding="utf-8")

    assert 'model: "gemini-3.8-flash"' in presets
    assert 'model: "deepseek-flash"' in presets
    assert 'model: "qwen3.7-plus"' in presets
    assert 'model: "glm-5.3"' in presets
    assert 'model: "kimi-k3"' in presets
    assert 'model: "MiniMax-M3"' in presets
    assert 'model: "grok-4.6"' in presets
    assert 'model: "doubao-seed-2.1-pro"' in presets

    assert 'model: "gemini-3.8-flash"' in guides
    assert 'model: "deepseek-flash"' in guides
    assert 'model: "qwen3.7-plus"' in guides
    assert 'model: "glm-5.3"' in guides


def test_anthropic_current_haiku_aliases_resolve_to_200k_context() -> None:
    assert _infer_context_tokens_for_model("claude-haiku-4-5") == 200_000
    assert _infer_context_tokens_for_model("claude-haiku-4-5-20251001") == 200_000


def test_current_flagship_context_windows_match_official_limits() -> None:
    assert _infer_context_tokens_for_model("gpt-6-astra") == 1_050_000
    assert _infer_context_tokens_for_model("gpt-5.6-terra") == 1_050_000
    assert _infer_context_tokens_for_model("claude-sonnet-5") == 1_000_000
    assert _infer_context_tokens_for_model("claude-opus-5") == 1_000_000
    assert _infer_context_tokens_for_model("claude-fable-5-1") == 1_000_000
    assert _infer_context_tokens_for_model("gemini-3.8-flash") == 1_048_576
    assert _infer_context_tokens_for_model("qwen3.8-max") == 1_000_000
    assert _infer_context_tokens_for_model("qwen3.7-plus") == 1_000_000
    assert _infer_context_tokens_for_model("glm-5.3") == 1_000_000
    assert _infer_context_tokens_for_model("kimi-k3") == 1_000_000
    assert _infer_context_tokens_for_model("kimi-k2.6") == 256_000
    assert _infer_context_tokens_for_model("MiniMax-M3") == 1_000_000
    assert _infer_context_tokens_for_model("deepseek-flash") == 1_000_000
    assert _infer_context_tokens_for_model("grok-4.6") == 500_000
    assert _infer_context_tokens_for_model("doubao-seed-2.1-pro") == 256_000


def test_deprecated_haiku_replacement_uses_current_alias() -> None:
    assert get_deprecated_model_replacement("claude-3-haiku") == (
        "claude-3-haiku",
        "claude-haiku-4-5",
    )


def test_deprecated_openai_turbo_replacements_use_gpt_6_astra() -> None:
    assert get_deprecated_model_replacement("gpt-4-turbo") == ("gpt-4-turbo", "gpt-6-astra")
    assert get_deprecated_model_replacement("gpt-4-turbo-preview") == (
        "gpt-4-turbo-preview",
        "gpt-6-astra",
    )
    assert get_deprecated_model_replacement("gpt-4-0125-preview") == (
        "gpt-4-0125-preview",
        "gpt-6-astra",
    )
    assert get_deprecated_model_replacement("gpt-4-1106-preview") == (
        "gpt-4-1106-preview",
        "gpt-6-astra",
    )


def test_retired_vendor_aliases_point_to_current_flagships() -> None:
    assert get_deprecated_model_replacement("deepseek-chat") == ("deepseek-chat", "deepseek-flash")
    assert get_deprecated_model_replacement("moonshot-v1-128k") == ("moonshot-v1", "kimi-k3")
    assert get_deprecated_model_replacement("kimi-k2.5") == ("kimi-k2.5", "kimi-k3")
    assert get_deprecated_model_replacement("glm-4-plus") == ("glm-4-plus", "glm-5.3")
    assert get_deprecated_model_replacement("kimi-k2.6") is None
    assert get_deprecated_model_replacement("kimi-k3") is None


def test_gemini_3_preview_context_windows_match_official_limits() -> None:
    assert _infer_context_tokens_for_model("gemini-3.0-pro-preview-02-2026") == 1_048_576
    assert _infer_context_tokens_for_model("gemini-3.0-flash-preview-02-2026") == 1_048_576
    assert _infer_context_tokens_for_model("gemini-3.0-flash-lite-preview-02-2026") == 1_048_576
    assert _infer_context_tokens_for_model("gemini-3.0-flash-thinking-preview-02-2026") == 262_144
