"""上下文优化配置默认值一致性测试。"""

from __future__ import annotations

import re
from pathlib import Path

from excelmanus.config import ExcelManusConfig, load_config

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ENV_EXAMPLE_PATH = _PROJECT_ROOT / ".env.example"
_README_PATH = _PROJECT_ROOT / "README.md"

_CONTEXT_OPTIMIZATION_ENV_TO_FIELD: dict[str, str] = {
    "EXCELMANUS_MAX_CONTEXT_TOKENS": "max_context_tokens",
    "EXCELMANUS_PROMPT_CACHE_KEY_ENABLED": "prompt_cache_key_enabled",
    "EXCELMANUS_SUMMARIZATION_ENABLED": "summarization_enabled",
    "EXCELMANUS_SUMMARIZATION_THRESHOLD_RATIO": "summarization_threshold_ratio",
    "EXCELMANUS_SUMMARIZATION_KEEP_RECENT_TURNS": "summarization_keep_recent_turns",
    "EXCELMANUS_COMPACTION_ENABLED": "compaction_enabled",
    "EXCELMANUS_COMPACTION_THRESHOLD_RATIO": "compaction_threshold_ratio",
    "EXCELMANUS_COMPACTION_KEEP_RECENT_TURNS": "compaction_keep_recent_turns",
    "EXCELMANUS_COMPACTION_MAX_SUMMARY_TOKENS": "compaction_max_summary_tokens",
}


def _field_default(field_name: str):
    field = ExcelManusConfig.__dataclass_fields__[field_name]
    return field.default


def _format_default_for_docs(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _extract_env_example_value(key: str) -> str:
    text = _ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    pattern = re.compile(rf"^\s*#\s*{re.escape(key)}=(.+?)\s*$", re.MULTILINE)
    match = pattern.search(text)
    assert match is not None, f".env.example 中缺少 {key} 示例项"
    return match.group(1).strip()


def _extract_readme_default(key: str) -> str:
    text = _README_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^\|\s*`{re.escape(key)}`\s*\|.*\|\s*`([^`]+)`\s*\|\s*$",
        re.MULTILINE,
    )
    match = pattern.search(text)
    assert match is not None, f"README 中缺少 {key} 默认值"
    return match.group(1).strip()


def test_load_config_uses_context_optimization_defaults(monkeypatch, tmp_path) -> None:
    """运行时默认值应与 ExcelManusConfig 声明保持一致。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
    monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")

    for env_key in _CONTEXT_OPTIMIZATION_ENV_TO_FIELD:
        monkeypatch.delenv(env_key, raising=False)

    cfg = load_config()

    for env_key, field_name in _CONTEXT_OPTIMIZATION_ENV_TO_FIELD.items():
        assert getattr(cfg, field_name) == _field_default(field_name), (
            f"{env_key} 的运行时默认值与 ExcelManusConfig.{field_name} 不一致"
        )


def test_env_example_matches_context_optimization_defaults() -> None:
    """.env.example 应与运行时默认值一致。"""
    for env_key, field_name in _CONTEXT_OPTIMIZATION_ENV_TO_FIELD.items():
        assert _extract_env_example_value(env_key) == _format_default_for_docs(
            _field_default(field_name)
        )


def test_readme_matches_context_optimization_defaults() -> None:
    """README 表格中的默认值应与运行时默认值一致。"""
    for env_key, field_name in _CONTEXT_OPTIMIZATION_ENV_TO_FIELD.items():
        assert _extract_readme_default(env_key) == _format_default_for_docs(
            _field_default(field_name)
        )
