"""model_identity 归一化与匹配逻辑测试。"""

from __future__ import annotations

import pytest

from excelmanus.model_identity import (
    has_token_prefix,
    longest_prefix_match,
    matches_token_sequence,
    model_match_candidates,
    normalize_lookup_table,
    normalize_model_tokens,
    strip_namespace,
    token_sequence_pattern,
)


class TestNormalizeModelTokens:
    @pytest.mark.parametrize(("raw", "expected"), [
        ("gpt-4o", "gpt-4o"),          # 数字→字母边界不拆分
        ("o1", "o-1"),
        ("kimi-k2.5", "kimi-k-2-5"),
        ("qwen3.8-max", "qwen-3-8-max"),
        ("llama3.1:8b", "llama-3-1-8b"),
        ("claude-haiku-4-5@20251001", "claude-haiku-4-5-20251001"),
        ("GPT_5.3 Codex", "gpt-5-3-codex"),
        ("  grok--4  ", "grok-4"),
        ("claude-opus-4.7", "claude-opus-4-7"),
    ])
    def test_normalize(self, raw: str, expected: str) -> None:
        assert normalize_model_tokens(raw) == expected


class TestStripNamespace:
    @pytest.mark.parametrize(("raw", "expected"), [
        ("us.anthropic.claude-sonnet-5", "claude-sonnet-5"),
        ("amazon.nova-pro-v1:0", "nova-pro-v1:0"),
        ("xai.grok-4", "grok-4"),
        ("meta.llama3-1-70b-instruct-v1:0", "llama3-1-70b-instruct-v1:0"),
        # 点号后是数字（版本号）不剥
        ("gpt-4.1", "gpt-4.1"),
        ("qwen2.5-vl", "qwen2.5-vl"),
        ("gemini-2.5-flash", "gemini-2.5-flash"),
        ("minimax-m2.5", "minimax-m2.5"),
        ("llama3.1:8b", "llama3.1:8b"),
        ("claude-sonnet-5", "claude-sonnet-5"),
    ])
    def test_strip(self, raw: str, expected: str) -> None:
        assert strip_namespace(raw) == expected


class TestModelMatchCandidates:
    def test_provider_tail_and_namespace(self) -> None:
        assert model_match_candidates("openai-codex/gpt-6-astra") == (
            "openai-codex/gpt-6-astra",
            "gpt-6-astra",
        )
        assert model_match_candidates("us.anthropic.claude-sonnet-5") == (
            "us-anthropic-claude-sonnet-5",
            "claude-sonnet-5",
        )
        assert model_match_candidates("anthropic/claude-sonnet-5:thinking") == (
            "anthropic/claude-sonnet-5-thinking",
            "claude-sonnet-5-thinking",
        )

    def test_dedup_and_empty(self) -> None:
        assert model_match_candidates("gpt-5") == ("gpt-5",)
        assert model_match_candidates("") == ()


class TestLongestPrefixMatch:
    def setup_method(self) -> None:
        self.table = normalize_lookup_table({
            "gpt-4.1": 1047576,
            "gpt-4": 128000,
            "kimi-k2": 262144,
            "claude-sonnet-5": 1000000,
        })

    def test_dash_boundary(self) -> None:
        # gpt-4.10 归一化为 gpt-4-10，不能误命中 gpt-4-1
        assert longest_prefix_match("gpt-4.10-x", self.table) == ("gpt-4", 128000)
        assert longest_prefix_match("gpt-4.1-mini", self.table) == ("gpt-4.1", 1047576)

    def test_longest_wins(self) -> None:
        assert longest_prefix_match("gpt-4.1", self.table) == ("gpt-4.1", 1047576)

    def test_multi_candidate_via_slash_tail(self) -> None:
        assert longest_prefix_match("anthropic/claude-sonnet-5", self.table) == (
            "claude-sonnet-5", 1000000,
        )

    def test_no_match(self) -> None:
        assert longest_prefix_match("totally-unknown", self.table) is None

    def test_version_guard(self) -> None:
        # kimi-k2.6 → 前缀后紧跟版本号段，不算命中
        assert longest_prefix_match(
            "kimi-k2.6", self.table, version_guard=True,
        ) is None
        assert longest_prefix_match(
            "kimi-k2.6", self.table,
        ) == ("kimi-k2", 262144)
        # 非数字后缀与长数字段仍命中
        assert longest_prefix_match(
            "kimi-k2-thinking", self.table, version_guard=True,
        ) == ("kimi-k2", 262144)
        assert longest_prefix_match(
            "kimi-k2-0905", self.table, version_guard=True,
        ) == ("kimi-k2", 262144)


class TestNormalizeLookupTable:
    def test_conflict_raises(self) -> None:
        with pytest.raises(ValueError):
            normalize_lookup_table({"gpt-4.1": 1, "gpt-4-1": 2})

    def test_same_value_keeps_first_key(self) -> None:
        table = normalize_lookup_table({"gpt-4.1": 1, "gpt-4-1": 1})
        assert table == {"gpt-4-1": ("gpt-4.1", 1)}


class TestTokenSequencePattern:
    def test_o1_boundary(self) -> None:
        pattern = token_sequence_pattern(["o1"])
        # gpt-4o 归一化后无独立 o-1 段
        assert not matches_token_sequence("gpt-4o", pattern)
        # gpt-4o1-x 归一化为 gpt-4o-1-x，token 为 4o + 1，仍不含 o-1 序列
        assert not matches_token_sequence("gpt-4o1-x", pattern)
        assert matches_token_sequence("o1-mini", pattern)
        assert matches_token_sequence("openai/o1-pro", pattern)

    def test_multi_keyword(self) -> None:
        pattern = token_sequence_pattern(["claude-opus", "gemini"])
        assert matches_token_sequence("us.anthropic.claude-opus-5", pattern)
        assert matches_token_sequence("gemini-3.8-flash", pattern)
        assert not matches_token_sequence("claude-sonnet-4.6", pattern)


class TestHasTokenPrefix:
    def test_prefix_with_boundary(self) -> None:
        assert has_token_prefix("claude-sonnet-5", "claude")
        assert has_token_prefix("anthropic/claude-sonnet-5", "claude")
        assert not has_token_prefix("gpt-4o", ("o1", "o3", "gpt-5"))
        assert has_token_prefix("openai/gpt-5.6-terra", ("gpt-5", "gpt-6"))
        assert has_token_prefix("o3-mini", ("o1", "o3"))
        # 无 - 边界不算前缀
        assert not has_token_prefix("claudeX-sonnet", "claude")
