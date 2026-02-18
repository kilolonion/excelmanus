"""WURM adaptive 模式测试。"""

from __future__ import annotations

import json
from unittest.mock import patch

from excelmanus.window_perception.adaptive import AdaptiveModeSelector
from excelmanus.window_perception.manager import WindowPerceptionManager
from excelmanus.window_perception.models import PerceptionBudget


def test_selector_prefix_mapping_and_unknown_fallback() -> None:
    selector = AdaptiveModeSelector()
    assert selector.select_mode(model_id="gpt-5.3", requested_mode="adaptive") == "unified"
    selector.reset()
    assert selector.select_mode(model_id="moonshotai/kimi-k2.5", requested_mode="adaptive") == "anchored"
    selector.reset()
    assert selector.select_mode(model_id="claude-sonnet-4-5-20250929", requested_mode="adaptive") == "anchored"
    selector.reset()
    assert selector.select_mode(model_id="deepseek-ai/DeepSeek-V3.2", requested_mode="adaptive") == "anchored"
    selector.reset()
    assert selector.select_mode(model_id="unknown-model", requested_mode="adaptive") == "anchored"


def test_selector_override_and_longest_prefix() -> None:
    selector = AdaptiveModeSelector(
        model_mode_overrides={
            "gpt": "enriched",
            "gpt-5": "anchored",
            "gpt-5.2": "unified",
        }
    )
    assert selector.select_mode(model_id="gpt-5.2-high", requested_mode="adaptive") == "unified"


def test_selector_downgrade_chain_no_skip() -> None:
    selector = AdaptiveModeSelector(current_mode="unified")
    assert selector.downgrade(reason="repeat_tripwire") == "anchored"
    assert selector.downgrade(reason="repeat_tripwire") == "enriched"
    assert selector.downgrade(reason="repeat_tripwire") == "enriched"


def test_selector_ingest_failure_threshold_and_success_reset() -> None:
    selector = AdaptiveModeSelector(current_mode="unified")
    assert selector.mark_ingest_failure() is False
    selector.mark_ingest_success()
    assert selector.consecutive_ingest_failures == 0
    assert selector.mark_ingest_failure() is False
    assert selector.mark_ingest_failure() is True
    assert selector.current_mode == "anchored"


def test_manager_adaptive_repeat_tripwire_downgrades_one_level() -> None:
    manager = WindowPerceptionManager(
        enabled=True,
        budget=PerceptionBudget(),
    )
    payload = json.dumps(
        {
            "file": "sales.xlsx",
            "sheet": "Q1",
            "shape": {"rows": 20, "columns": 5},
            "columns": ["日期", "产品", "数量", "单价", "金额"],
            "preview": [{"日期": "2024-01-01", "产品": "A", "数量": 1, "单价": 100, "金额": 100}],
        },
        ensure_ascii=False,
    )
    arguments = {"file_path": "sales.xlsx", "sheet_name": "Q1", "range": "A1:E10"}

    first = manager.enrich_tool_result(
        tool_name="read_excel",
        arguments=arguments,
        result_text=payload,
        success=True,
        mode="adaptive",
        model_id="gpt-5.3",
    )
    second = manager.enrich_tool_result(
        tool_name="read_excel",
        arguments=arguments,
        result_text=payload,
        success=True,
        mode="adaptive",
        model_id="gpt-5.3",
    )
    third = manager.enrich_tool_result(
        tool_name="read_excel",
        arguments=arguments,
        result_text=payload,
        success=True,
        mode="adaptive",
        model_id="gpt-5.3",
    )

    assert "首行预览" not in first
    assert "hint=intent[aggregate] repeat read detected" in second
    assert "intent: aggregate" in third
    assert "hint: intent[aggregate] repeat read detected" in third
    assert manager.resolve_effective_mode(requested_mode="adaptive", model_id="gpt-5.3") == "anchored"


def test_manager_adaptive_ingest_failures_downgrade() -> None:
    manager = WindowPerceptionManager(
        enabled=True,
        budget=PerceptionBudget(),
    )
    payload = json.dumps(
        {
            "file": "sales.xlsx",
            "sheet": "Q1",
            "shape": {"rows": 20, "columns": 5},
            "preview": [{"日期": "2024-01-01", "产品": "A", "数量": 1, "单价": 100, "金额": 100}],
        },
        ensure_ascii=False,
    )
    arguments = {"file_path": "sales.xlsx", "sheet_name": "Q1", "range": "A1:E10"}

    original_apply = manager._apply_ingest

    def _raise_apply(*_args, **_kwargs):
        raise RuntimeError("ingest boom")

    manager._apply_ingest = _raise_apply  # type: ignore[assignment]
    try:
        manager.enrich_tool_result(
            tool_name="read_excel",
            arguments=arguments,
            result_text=payload,
            success=True,
            mode="adaptive",
            model_id="gpt-5.3",
        )
        manager.enrich_tool_result(
            tool_name="read_excel",
            arguments=arguments,
            result_text=payload,
            success=True,
            mode="adaptive",
            model_id="gpt-5.3",
        )
    finally:
        manager._apply_ingest = original_apply  # type: ignore[assignment]

    assert manager.resolve_effective_mode(requested_mode="adaptive", model_id="gpt-5.3") == "anchored"


def test_manager_ingest_exception_preserves_payload_without_locals_dependency() -> None:
    manager = WindowPerceptionManager(
        enabled=True,
        budget=PerceptionBudget(),
    )
    payload_text = json.dumps(
        {
            "file": "sales.xlsx",
            "sheet": "Q1",
            "shape": {"rows": 20, "columns": 5},
            "preview": [{"日期": "2024-01-01", "产品": "A", "数量": 1, "单价": 100, "金额": 100}],
        },
        ensure_ascii=False,
    )
    arguments = {"file_path": "sales.xlsx", "sheet_name": "Q1", "range": "A1:E10"}

    original_locate = manager._locate_window_by_identity
    original_fallback = manager._enriched_fallback
    captured: dict[str, object] = {}

    def _raise_locate(*_args, **_kwargs):
        raise RuntimeError("locate boom")

    def _capture_fallback(*, tool_name, arguments, result_text, success, payload):  # type: ignore[no-untyped-def]
        captured["payload"] = payload
        return "fallback"

    manager._locate_window_by_identity = _raise_locate  # type: ignore[assignment]
    manager._enriched_fallback = _capture_fallback  # type: ignore[assignment]
    try:
        with patch("builtins.locals", return_value={}):
            result = manager.ingest_and_confirm(
                tool_name="read_excel",
                arguments=arguments,
                result_text=payload_text,
                success=True,
                mode="anchored",
                requested_mode="anchored",
            )
    finally:
        manager._locate_window_by_identity = original_locate  # type: ignore[assignment]
        manager._enriched_fallback = original_fallback  # type: ignore[assignment]

    assert result == "fallback"
    assert captured["payload"] is not None
