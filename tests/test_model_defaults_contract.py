from __future__ import annotations

import re
from pathlib import Path

from excelmanus.config import (
    DEFAULT_EMBEDDING_MODEL,
    _infer_context_tokens_for_model,
    get_deprecated_model_replacement,
)


_ROOT = Path(__file__).resolve().parents[1]
_README_CN = _ROOT / "README.md"
_README_EN = _ROOT / "README_EN.md"
_MODEL_TAB = _ROOT / "web" / "src" / "components" / "settings" / "ModelTab.tsx"
_PROVIDER_GUIDES = _ROOT / "web" / "src" / "components" / "onboarding" / "provider-guides.ts"


def test_default_embedding_model_is_current_openai_name() -> None:
    assert DEFAULT_EMBEDDING_MODEL == "text-embedding-3-small"


def test_readme_quick_config_uses_prefixed_env_keys() -> None:
    cn = _README_CN.read_text(encoding="utf-8")
    en = _README_EN.read_text(encoding="utf-8")

    assert "`EXCELMANUS_EMBEDDING_MODEL`" in cn
    assert "`EXCELMANUS_SESSION_SUMMARY_MIN_TURNS`" in cn
    assert "`EMBEDDING_MODEL`" not in cn
    assert "`SESSION_SUMMARY_MIN_TURNS`" not in cn

    assert "`EXCELMANUS_EMBEDDING_MODEL`" in en
    assert "`EXCELMANUS_SESSION_SUMMARY_MIN_TURNS`" in en
    assert "`EMBEDDING_MODEL`" not in en
    assert "`SESSION_SUMMARY_MIN_TURNS`" not in en


def test_frontend_anthropic_presets_use_latest_sonnet_alias() -> None:
    tab = _MODEL_TAB.read_text(encoding="utf-8")
    guides = _PROVIDER_GUIDES.read_text(encoding="utf-8")

    assert 'model: "claude-sonnet-4-6"' in tab
    assert 'model: "anthropic/claude-sonnet-4-6"' in tab
    assert 'model: "claude-sonnet-4"' not in tab
    assert 'model: "anthropic/claude-sonnet-4"' not in tab

    assert 'model: "claude-sonnet-4-6"' in guides
    assert 'model: "anthropic/claude-sonnet-4-6"' in guides
    assert 'model: "claude-sonnet-4"' not in guides
    assert 'model: "anthropic/claude-sonnet-4"' not in guides


def test_frontend_openai_presets_use_current_recommended_model() -> None:
    tab = _MODEL_TAB.read_text(encoding="utf-8")
    guides = _PROVIDER_GUIDES.read_text(encoding="utf-8")

    assert re.search(r'id:\s*"openai"[\s\S]*?model:\s*"gpt-5\.2"', tab)
    assert re.search(r'id:\s*"openai"[\s\S]*?model:\s*"gpt-5\.2"', guides)


def test_frontend_openai_presets_use_latest_gpt_5_2() -> None:
    tab = _MODEL_TAB.read_text(encoding="utf-8")
    guides = _PROVIDER_GUIDES.read_text(encoding="utf-8")

    tab_openai = re.search(r'id: "openai",[\s\S]{0,260}?model: "([^"]+)"', tab)
    guide_openai = re.search(r'id: "openai",[\s\S]{0,260}?model: "([^"]+)"', guides)

    assert tab_openai is not None
    assert guide_openai is not None
    assert tab_openai.group(1) == "gpt-5.2"
    assert guide_openai.group(1) == "gpt-5.2"


def test_anthropic_current_haiku_aliases_resolve_to_200k_context() -> None:
    assert _infer_context_tokens_for_model("claude-haiku-4-5") == 200_000
    assert _infer_context_tokens_for_model("claude-haiku-4-5-20251001") == 200_000


def test_deprecated_haiku_replacement_uses_current_alias() -> None:
    assert get_deprecated_model_replacement("claude-3-haiku") == (
        "claude-3-haiku",
        "claude-haiku-4-5",
    )


def test_deprecated_openai_turbo_replacements_use_gpt_5_2() -> None:
    assert get_deprecated_model_replacement("gpt-4-turbo") == ("gpt-4-turbo", "gpt-5.2")
    assert get_deprecated_model_replacement("gpt-4-turbo-preview") == (
        "gpt-4-turbo-preview",
        "gpt-5.2",
    )
    assert get_deprecated_model_replacement("gpt-4-0125-preview") == (
        "gpt-4-0125-preview",
        "gpt-5.2",
    )
    assert get_deprecated_model_replacement("gpt-4-1106-preview") == (
        "gpt-4-1106-preview",
        "gpt-5.2",
    )


def test_gemini_3_preview_context_windows_match_official_limits() -> None:
    assert _infer_context_tokens_for_model("gemini-3.0-pro-preview-02-2026") == 1_048_576
    assert _infer_context_tokens_for_model("gemini-3.0-flash-preview-02-2026") == 1_048_576
    assert _infer_context_tokens_for_model("gemini-3.0-flash-lite-preview-02-2026") == 1_048_576
    assert _infer_context_tokens_for_model("gemini-3.0-flash-thinking-preview-02-2026") == 262_144
