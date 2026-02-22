"""Embedding 配置默认值的一致性测试。"""

from __future__ import annotations

import re
from pathlib import Path

from excelmanus.config import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL,
    load_config,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ENV_EXAMPLE_PATH = _PROJECT_ROOT / ".env.example"
_README_PATH = _PROJECT_ROOT / "README.md"


def _extract_env_example_value(key: str) -> str:
    text = _ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    pattern = re.compile(rf"^\s*#\s*{re.escape(key)}=(.+?)\s*$", re.MULTILINE)
    matches = [item.strip() for item in pattern.findall(text)]
    assert matches, f".env.example 中缺少 {key} 示例项"
    assert len(matches) == 1, f".env.example 中 {key} 出现多次，需保持唯一"
    return matches[0]


def _extract_readme_default(key: str) -> str:
    text = _README_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^\|\s*`{re.escape(key)}`\s*\|.*\|\s*`([^`]+)`\s*\|\s*$",
        re.MULTILINE,
    )
    matches = [item.strip() for item in pattern.findall(text)]
    assert matches, f"README 中缺少 {key} 默认值"
    assert len(matches) == 1, f"README 中 {key} 默认值出现多次，需保持唯一"
    return matches[0]


def test_load_config_uses_embedding_defaults(monkeypatch, tmp_path) -> None:
    """运行时默认值应来自统一的 Embedding 默认常量。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
    monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
    monkeypatch.delenv("EXCELMANUS_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("EXCELMANUS_EMBEDDING_DIMENSIONS", raising=False)

    cfg = load_config()

    assert cfg.embedding_model == DEFAULT_EMBEDDING_MODEL
    assert cfg.embedding_dimensions == DEFAULT_EMBEDDING_DIMENSIONS


def test_env_example_matches_embedding_defaults() -> None:
    """.env.example 的 Embedding 示例应与运行时默认值一致。"""
    assert (
        _extract_env_example_value("EXCELMANUS_EMBEDDING_MODEL")
        == DEFAULT_EMBEDDING_MODEL
    )
    assert (
        _extract_env_example_value("EXCELMANUS_EMBEDDING_DIMENSIONS")
        == str(DEFAULT_EMBEDDING_DIMENSIONS)
    )


def test_readme_matches_embedding_defaults() -> None:
    """README 的 Embedding 默认值应与运行时默认值一致。"""
    assert (
        _extract_readme_default("EXCELMANUS_EMBEDDING_MODEL")
        == DEFAULT_EMBEDDING_MODEL
    )
    assert (
        _extract_readme_default("EXCELMANUS_EMBEDDING_DIMENSIONS")
        == str(DEFAULT_EMBEDDING_DIMENSIONS)
    )
