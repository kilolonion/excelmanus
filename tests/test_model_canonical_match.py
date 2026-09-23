"""Jev 智能匹配：Model ID → 已知规范模型名的置信度绑定测试。

覆盖 match_canonical_model 评分规则、canonical_match_enabled 开关、
profile_canonical 门控、infer_model_family 与上下文窗口 canonical 回退。
"""

from __future__ import annotations

import pytest

from excelmanus.config import (
    CANONICAL_MATCH_THRESHOLD,
    _infer_context_tokens_for_model,
    canonical_match_enabled,
    infer_model_family,
    match_canonical_model,
    profile_canonical,
)
from excelmanus.settings_runtime import using_values


class TestMatchCanonicalModel:
    """match_canonical_model 置信度匹配。"""

    @pytest.mark.parametrize("model", [
        "gpt-5.6-sol",
        "gpt-5-6-sol",          # 分隔符变体：点/横线等价
        "GPT-5.6-SOL",          # 大小写不敏感
        "gpt_5_6_sol",          # 下划线分隔符
    ])
    def test_separator_variants_hit_sol(self, model: str) -> None:
        hit = match_canonical_model(model)
        assert hit is not None
        assert hit.canonical == "gpt-5.6-sol"
        assert hit.confidence >= CANONICAL_MATCH_THRESHOLD

    def test_exact_match_confidence(self) -> None:
        hit = match_canonical_model("gpt-5.6-sol")
        assert hit is not None
        assert hit.canonical == "gpt-5.6-sol"
        assert hit.confidence == pytest.approx(1.0)
        assert hit.reason == "exact"

    @pytest.mark.parametrize("model", [
        "openai/gpt-5-6-sol",
        "openai-gpt-5.6-sol",
        "azure/gpt-5.6-sol",
    ])
    def test_vendor_prefix_stripped(self, model: str) -> None:
        hit = match_canonical_model(model)
        assert hit is not None
        assert hit.canonical == "gpt-5.6-sol"
        assert hit.confidence >= CANONICAL_MATCH_THRESHOLD

    @pytest.mark.parametrize("model", [
        "xiaomi/mimo-v2.5",
        "mimo/mimo-v2.5",
    ])
    def test_mimo_vendor_prefix_stripped(self, model: str) -> None:
        hit = match_canonical_model(model)
        assert hit is not None
        assert hit.canonical == "mimo-v2.5"
        assert hit.confidence >= CANONICAL_MATCH_THRESHOLD

    def test_bedrock_namespace(self) -> None:
        hit = match_canonical_model("us.anthropic.claude-sonnet-4-5")
        assert hit is not None
        assert hit.canonical == "claude-sonnet-4.5"
        assert hit.confidence >= CANONICAL_MATCH_THRESHOLD

    def test_cosmetic_suffix_still_binds(self) -> None:
        """键 + 修饰性/日期后缀（如 -preview、-20260301）置信度仍达标。"""
        hit = match_canonical_model("deepseek-v3.2-exp")
        assert hit is not None
        # deepseek-v3.2-exp 本身是表内键，应精确命中
        assert hit.canonical == "deepseek-v3.2-exp"

    def test_date_suffix_binds_to_base(self) -> None:
        hit = match_canonical_model("gemini-3-flash-20260101")
        assert hit is not None
        assert hit.canonical == "gemini-3-flash"
        assert hit.confidence >= CANONICAL_MATCH_THRESHOLD

    def test_non_cosmetic_suffix_below_threshold(self) -> None:
        """键 + 非修饰性后缀（如 -custom）只能得到低置信度建议。"""
        hit = match_canonical_model("gpt-5.6-sol-finetune")
        if hit is not None:
            assert hit.confidence < CANONICAL_MATCH_THRESHOLD

    @pytest.mark.parametrize("model", [
        "",
        "totally-unknown-model-xyz",
        "my-local-llm",
    ])
    def test_no_match(self, model: str) -> None:
        hit = match_canonical_model(model)
        assert hit is None or hit.confidence < CANONICAL_MATCH_THRESHOLD


class TestInferModelFamily:
    @pytest.mark.parametrize(("canonical", "expected"), [
        ("gpt-5.6-sol", "gpt"),
        ("o4-mini", "gpt"),
        ("claude-sonnet-4.5", "claude"),
        ("gemini-2.5-pro", "gemini"),
        ("deepseek-v3.2", "deepseek"),
        ("qwen3-max", "qwen"),
        ("glm-4.6", "glm"),
        ("kimi-k2.5", "moonshot"),
        ("grok-4", "grok"),
        ("minimax-m3", "minimax"),
        ("mimo-v2.5", "mimo"),
        ("unknown-vendor-model", ""),
    ])
    def test_family(self, canonical: str, expected: str) -> None:
        assert infer_model_family(canonical) == expected


class TestCanonicalMatchSwitch:
    """EXCELMANUS_MODEL_CANONICAL_MATCH 开关语义。"""

    def test_default_enabled(self) -> None:
        with using_values({}):
            assert canonical_match_enabled() is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off"])
    def test_disabled_values(self, raw: str) -> None:
        with using_values({"EXCELMANUS_MODEL_CANONICAL_MATCH": raw}):
            assert canonical_match_enabled() is False

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on"])
    def test_enabled_values(self, raw: str) -> None:
        with using_values({"EXCELMANUS_MODEL_CANONICAL_MATCH": raw}):
            assert canonical_match_enabled() is True

    def test_profile_canonical_gated_by_switch(self) -> None:
        row = {"canonical_model": "gpt-5.6-sol"}
        with using_values({"EXCELMANUS_MODEL_CANONICAL_MATCH": "true"}):
            assert profile_canonical(row) == "gpt-5.6-sol"
        with using_values({"EXCELMANUS_MODEL_CANONICAL_MATCH": "false"}):
            assert profile_canonical(row) == ""

    def test_profile_canonical_empty_row(self) -> None:
        with using_values({"EXCELMANUS_MODEL_CANONICAL_MATCH": "true"}):
            assert profile_canonical({}) == ""
            assert profile_canonical({"canonical_model": "  "}) == ""


class TestCanonicalContextWindow:
    """绑定 canonical 后上下文窗口按规范名推断。"""

    def test_canonical_lookup(self) -> None:
        assert _infer_context_tokens_for_model("gpt-5.6-sol") == 1_050_000

    def test_separator_variant_same_window(self) -> None:
        """归一化保证分隔符变体命中同一窗口。"""
        assert _infer_context_tokens_for_model("gpt-5-6-sol") == 1_050_000

    def test_unknown_model_falls_back_default(self) -> None:
        from excelmanus.config import _DEFAULT_CONTEXT_TOKENS

        assert _infer_context_tokens_for_model("unknown-xyz-123") == _DEFAULT_CONTEXT_TOKENS


class TestContextBudgetCanonical:
    """ContextBudget 优先使用 canonical_model 推断窗口。"""

    def test_update_for_model_prefers_canonical(self) -> None:
        from excelmanus.context_budget import ContextBudget

        budget = ContextBudget(model="my-proxy-model")
        assert budget.max_tokens == 256_000  # 未知模型回退默认

        budget.update_for_model("my-proxy-model", canonical_model="gpt-5.6-sol")
        assert budget.max_tokens == 1_050_000

    def test_init_with_canonical(self) -> None:
        from excelmanus.context_budget import ContextBudget

        budget = ContextBudget(
            model="my-proxy-model", canonical_model="gpt-5.6-sol",
        )
        assert budget.max_tokens == 1_050_000


class TestProfileStoreCanonical:
    """config_store 读写 canonical_model 列。"""

    def test_add_and_read_canonical(self, tmp_path) -> None:
        from excelmanus.database import Database
        from excelmanus.stores.config_store import GlobalConfigStore

        db = Database(str(tmp_path / "t.db"))
        store = GlobalConfigStore(db)
        try:
            assert store.add_profile(
                name="sol", model="gpt-5-6-sol", api_key="k",
                base_url="https://api.example.com",
                canonical_model="gpt-5.6-sol",
            )
            row = store.get_profile("sol")
            assert row is not None
            assert row["canonical_model"] == "gpt-5.6-sol"
            rows = store.list_profiles()
            assert rows[0]["canonical_model"] == "gpt-5.6-sol"
        finally:
            db.close()

    def test_update_canonical(self, tmp_path) -> None:
        from excelmanus.database import Database
        from excelmanus.stores.config_store import GlobalConfigStore

        db = Database(str(tmp_path / "t.db"))
        store = GlobalConfigStore(db)
        try:
            store.add_profile(
                name="m", model="x", api_key="k", base_url="https://a.com",
            )
            assert store.get_profile("m")["canonical_model"] == ""
            store.update_profile("m", canonical_model="gpt-5")
            assert store.get_profile("m")["canonical_model"] == "gpt-5"
            # None 表示不更新该列
            store.update_profile("m", description="d")
            assert store.get_profile("m")["canonical_model"] == "gpt-5"
        finally:
            db.close()


class TestBackfillCanonical:
    """开启开关时对存量档案回填规范模型名。"""

    def _make_store(self, tmp_path):
        from excelmanus.database import Database
        from excelmanus.stores.config_store import GlobalConfigStore

        db = Database(str(tmp_path / "t.db"))
        return db, GlobalConfigStore(db)

    def test_backfill_fills_empty_only(self, tmp_path, monkeypatch) -> None:
        import excelmanus.api_app_state as state

        db, store = self._make_store(tmp_path)
        try:
            store.add_profile(name="a", model="gpt-5-6-sol", api_key="k", base_url="https://a.com")
            store.add_profile(name="b", model="totally-unknown", api_key="k", base_url="https://a.com")
            store.add_profile(
                name="c", model="x", api_key="k", base_url="https://a.com",
                canonical_model="preset-value",
            )
            monkeypatch.setattr(state, "get_config_store", lambda: store)
            monkeypatch.setattr(state, "get_config", lambda: None)

            with using_values({"EXCELMANUS_MODEL_CANONICAL_MATCH": "true"}):
                changed = state.backfill_canonical_models()

            assert changed == 1
            assert store.get_profile("a")["canonical_model"] == "gpt-5.6-sol"
            assert store.get_profile("a")["model_family"] == "gpt"
            assert store.get_profile("b")["canonical_model"] == ""
            # 已有绑定不被覆盖
            assert store.get_profile("c")["canonical_model"] == "preset-value"
        finally:
            db.close()

    def test_backfill_noop_when_disabled(self, tmp_path, monkeypatch) -> None:
        import excelmanus.api_app_state as state

        db, store = self._make_store(tmp_path)
        try:
            store.add_profile(name="a", model="gpt-5-6-sol", api_key="k", base_url="https://a.com")
            monkeypatch.setattr(state, "get_config_store", lambda: store)
            with using_values({"EXCELMANUS_MODEL_CANONICAL_MATCH": "false"}):
                assert state.backfill_canonical_models() == 0
            assert store.get_profile("a")["canonical_model"] == ""
        finally:
            db.close()
