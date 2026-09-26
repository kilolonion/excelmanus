"""The primary agent owns delivery; no auxiliary review may reopen its reply."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from excelmanus.agent.loop import run_tool_loop
from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine
from excelmanus.engine_core.delivery import DeliveryLedger
from excelmanus.engine_types import ToolCallResult
from excelmanus.engine_core.tool_result import ToolResult, from_payload
from excelmanus.events import EventType
from excelmanus.providers.stream_types import StreamDelta
from excelmanus.skillpacks import SkillMatchResult
from excelmanus.tools import ToolRegistry


def _engine() -> AgentEngine:
    return AgentEngine(ExcelManusConfig(
        api_key="test-key", base_url="https://test.example.com/v1",
        model="test-model", max_iterations=8, max_consecutive_failures=3,
        workspace_root=str(Path(__file__).resolve().parent),
        ai_gateway_api_key="vck_test", jev_enabled="enforce",
        jev_verification="enforce",
    ), ToolRegistry())


def _text_deltas(events: list) -> list[str]:
    return [ev.text_delta for ev in events if ev.event_type == EventType.TEXT_DELTA]


@pytest.mark.asyncio
@pytest.mark.parametrize("emit_events", [True, False])
@pytest.mark.parametrize("evidence", ["read_only", "complete", "sampled", "missing", "mismatch"])
async def test_delivery_is_final_without_forced_review(emit_events: bool, evidence: str) -> None:
    engine = _engine()
    initial = []
    if evidence != "read_only":
        engine._state.affected_files = ["a.xlsx"]
        initial = [ToolCallResult("apply_spreadsheet_changes", {"file_path": "a.xlsx"}, "ok", True)]
        if evidence != "missing":
            initial[0].structured = ToolResult(success=True, model_text="ok", value={
                "meta": {"write_verification": {
                    "status": "success" if evidence != "mismatch" else "failed",
                    "sampled": evidence == "sampled",
                    "mismatches": ["A1"] if evidence == "mismatch" else [],
                }},
            })
    events: list = []

    async def live_stream():
        yield StreamDelta(content_delta="已交付；")
        assert _text_deltas(events) == (["已交付；"] if emit_events else [])
        yield StreamDelta(content_delta="未验证事项已说明。")
        yield StreamDelta(finish_reason="stop", usage={"prompt_tokens": 2, "completion_tokens": 2})

    mocked_create = AsyncMock(side_effect=lambda **_kwargs: live_stream())
    engine._client.chat.completions.create = mocked_create
    # Even a legacy integration returning a review request cannot re-enter
    # the loop: the delivery hooks must no longer be called at all.
    with (
        patch("excelmanus.system_one.host.should_check_delivery", Mock(return_value=True)) as check,
        patch("excelmanus.system_one.host.maybe_verify_mutation", AsyncMock(return_value="继续检查")) as verify,
        patch("excelmanus.system_one.evaluate", AsyncMock()) as evaluate,
    ):
        result = await run_tool_loop(
            engine, SkillMatchResult(skills_used=[], route_mode="fallback", system_contexts=[]),
            on_event=events.append if emit_events else None, initial_tool_results=initial,
        )
    check.assert_not_called()
    verify.assert_not_awaited()
    evaluate.assert_not_awaited()
    mocked_create.assert_awaited_once()
    assert result.reply == "已交付；未验证事项已说明。"
    assert result.tool_calls == initial
    assert _text_deltas(events) == (["已交付；", "未验证事项已说明。"] if emit_events else [])
    assert not any(event.event_type == EventType.RETRACT_TEXT for event in events)
    assert not any(
        message.get("_prompt_kind") in {"jev_delivery_check", "jev_delivery_draft"}
        for message in engine._memory.messages
    )


# ── 交付证据门：规则口径可疑 vs 产物缺陷 ────────────────────────────────
# 背景（真实会话 analysis/session.json #105/#110）：agent 把 total（整列求和）
# 用在"断言单个单元格"的意图上，得到 3 条 failures，交付门于是追加一轮返工，
# 用户侧还看到"尚未完成全部交付核验"。规则写错的成本不该转嫁成产物缺陷。

ARTIFACT = "outputs/回归分析.xlsx"
VERSION = "sha256:d8d3ab12123ea33d701a7c3ef1cb002ce62a431224f59f46b3c2daaa80941c6f"


def _published(path: str = ARTIFACT, version: str = VERSION):
    return from_payload({
        "status": "success", "file_path": path, "content_version": version,
        "receipt": {"operation_id": "op1", "state": "committed", "targets": [
            {"path": path, "after_version": version, "op": "create", "publish_status": "published"}]},
        "observation": {"verification_requirements": {}},
    })


def _validated(path: str, rules, *, failures, results=None, version: str = VERSION):
    payload = {"status": "success", "file_path": path, "content_version": version,
               "validation_status": "complete", "valid": not failures, "failures": failures}
    if results is not None:
        payload["rules"] = results
    return from_payload(payload)


def _record(ledger: DeliveryLedger, rules, failures, results=None, path: str = ARTIFACT):
    ledger.record("apply_spreadsheet_changes", {}, _published(path))
    ledger.record("validate_spreadsheet", {"file_path": path, "rules": rules},
                  _validated(path, rules, failures=failures, results=results))
    return ledger.artifacts[path]


def test_total_rule_semantic_misuse_is_not_a_product_defect():
    """真实会话 #105 的旧回执行状：文本单元格用非数值 expected 标记。"""
    rules = [{"kind": "formula_errors"},
             {"column": "C", "expected": 1001.2, "kind": "total", "sheet": "数据", "tolerance": 0.001},
             {"column": "A", "kind": "unique", "sheet": "数据"},
             {"column": "B", "expected": 6.707067669172933, "kind": "total", "sheet": "回归分析", "tolerance": 0.0001}]
    results = [{"rule": 0, "kind": "formula_errors", "sheet": "数据", "checked": 88, "failed": 0},
               {"rule": 1, "kind": "total", "sheet": "数据", "checked": 20, "failed": 0, "skipped": 1},
               {"rule": 2, "kind": "unique", "sheet": "数据", "checked": 21, "failed": 0},
               {"rule": 3, "kind": "total", "sheet": "回归分析", "checked": 23, "failed": 3, "skipped": 13}]
    failures = [
        {"rule": 3, "kind": "total", "sheet": "回归分析", "cell": "B5", "actual": "数值",
         "expected": "数值（非数值文本不计入合计）"},
        {"rule": 3, "kind": "total", "sheet": "回归分析", "cell": "B24",
         "actual": "销售额 = 1.4338 + 6.7071 × 广告投入", "expected": "数值（非数值文本不计入合计）"},
        {"rule": 3, "kind": "total", "sheet": "回归分析", "cell": "B2",
         "actual": 26660.461644200055, "expected": 6.707067669172933},
    ]
    ledger = DeliveryLedger()
    entry = _record(ledger, rules, failures, results)
    assert entry["issues"] == []
    assert {item["rule_suspect"] for item in entry["rule_suspect"]} == {"column_contains_non_numeric_text"}
    assert ledger.pending() == []
    assert ledger.next_feedback() is None  # 不再追加"交付证据尚不完整"的一轮


def test_total_rule_semantic_misuse_is_detected_from_new_receipt_shape():
    """校验工具新版回执：文本单元格只进 excluded_non_numeric，failure 只剩合计不符。"""
    rules = [{"column": "B", "expected": 6.707067669172933, "kind": "total", "sheet": "回归分析",
              "tolerance": 0.0001}]
    results = [{"rule": 0, "kind": "total", "sheet": "回归分析", "checked": 23, "failed": 1, "skipped": 13,
                "semantics": "column_total", "column": "B", "excluded_non_numeric": 2,
                "excluded_non_numeric_cells": ["B5", "B24"]}]
    failures = [{"rule": 0, "kind": "total", "sheet": "回归分析", "cell": "B2",
                 "actual": 26660.461644200055, "expected": 6.707067669172933, "reason": "total_mismatch"}]
    ledger = DeliveryLedger()
    entry = _record(ledger, rules, failures, results)
    assert entry["issues"] == []
    assert ledger.pending() == []
    assert ledger.next_feedback() is None


def test_explicit_expected_matches_cell_marker_is_honored():
    """工具若显式标记 expected 等于列内某个单元格值（单点断言误用 total），交付门必须采信。"""
    rules = [{"column": "B", "expected": 6.707067669172933, "kind": "total", "sheet": "回归分析",
              "tolerance": 0.0001}]
    results = [{"rule": 0, "kind": "total", "sheet": "回归分析", "checked": 23, "failed": 1}]
    failures = [{"rule": 0, "kind": "total", "sheet": "回归分析", "cell": "B2",
                 "actual": 26660.461644200055, "expected": 6.707067669172933,
                 "reason": "total_mismatch", "expected_matches_cell": ["B7"]}]
    ledger = DeliveryLedger()
    entry = _record(ledger, rules, failures, results)
    assert entry["issues"] == []
    assert entry["rule_suspect"][0]["rule_suspect"] == "expected_matches_cell"
    assert ledger.pending() == []


def test_genuine_total_mismatch_still_fails_the_delivery_gate():
    """纯数值列的合计错误是产物缺陷，绝不能被 rule_suspect 吞掉。"""
    rules = [{"column": "C", "expected": 1001.2, "kind": "total", "sheet": "数据", "tolerance": 0.001}]
    results = [{"rule": 0, "kind": "total", "sheet": "数据", "checked": 20, "failed": 1, "skipped": 1}]
    failures = [{"rule": 0, "kind": "total", "sheet": "数据", "cell": "C2", "actual": 950.0,
                 "expected": 1001.2, "reason": "total_mismatch"}]
    ledger = DeliveryLedger()
    entry = _record(ledger, rules, failures, results)
    assert entry["rule_suspect"] == []
    row = ledger.pending()[0]
    assert "validation_failed_or_partial" in row["missing"]
    assert row["issues"] == failures
    assert "rule_suspect" not in row
    feedback = ledger.next_feedback()
    assert feedback and "rule_suspect" not in feedback


def test_formula_error_and_unique_conflict_are_never_rule_suspect():
    """公式错误与唯一性冲突永远算产物缺陷；口径分类不能放宽它们。"""
    rules = [{"kind": "unique", "sheet": "数据", "column": "A"}, {"kind": "formula_errors", "sheet": "数据"}]
    results = [{"rule": 0, "kind": "unique", "sheet": "数据", "checked": 20, "failed": 1},
               {"rule": 1, "kind": "formula_errors", "sheet": "数据", "checked": 88, "failed": 1}]
    failures = [{"rule": 0, "kind": "unique", "sheet": "数据", "cell": "A5", "actual": ["甲"], "expected": "unique"},
                {"rule": 1, "kind": "formula_errors", "sheet": "数据", "cell": "B3", "actual": "#REF!",
                 "expected": "no formula error"}]
    ledger = DeliveryLedger()
    entry = _record(ledger, rules, failures, results)
    assert entry["rule_suspect"] == []
    assert entry["issues"] == failures
    assert "validation_failed_or_partial" in ledger.pending()[0]["missing"]


def test_mixed_receipt_reports_product_issues_and_clarifies_rule_scope():
    rules = [{"kind": "unique", "sheet": "数据", "column": "A"},
             {"column": "B", "expected": 6.707067669172933, "kind": "total", "sheet": "回归分析"}]
    results = [{"rule": 0, "kind": "unique", "sheet": "数据", "checked": 20, "failed": 1},
               {"rule": 1, "kind": "total", "sheet": "回归分析", "checked": 23, "failed": 3, "skipped": 13}]
    unique_failure = {"rule": 0, "kind": "unique", "sheet": "数据", "cell": "A5", "actual": ["甲"],
                      "expected": "unique"}
    failures = [unique_failure,
                {"rule": 1, "kind": "total", "sheet": "回归分析", "cell": "B5", "actual": "数值",
                 "expected": "数值（非数值文本不计入合计）"},
                {"rule": 1, "kind": "total", "sheet": "回归分析", "cell": "B2",
                 "actual": 26660.461644200055, "expected": 6.707067669172933}]
    ledger = DeliveryLedger()
    _record(ledger, rules, failures, results)
    row = ledger.pending()[0]
    assert "validation_failed_or_partial" in row["missing"]
    assert row["issues"] == [unique_failure]          # 只有真实缺陷进入 issues
    assert row["rule_suspect"] and all(item["rule"] == 1 for item in row["rule_suspect"])
    feedback = ledger.next_feedback()
    assert feedback and "rule_suspect" in feedback and "不是文件缺陷" in feedback
    assert ledger.next_feedback() is None             # 去重与 2 轮上限没有被改坏


def test_rule_suspect_survives_snapshot_round_trip():
    rules = [{"column": "B", "expected": 6.707067669172933, "kind": "total", "sheet": "回归分析"}]
    results = [{"rule": 0, "kind": "total", "sheet": "回归分析", "checked": 23, "failed": 1,
                "excluded_non_numeric": 2}]
    failures = [{"rule": 0, "kind": "total", "sheet": "回归分析", "cell": "B2",
                 "actual": 26660.0, "expected": 6.707067669172933, "reason": "total_mismatch"}]
    ledger = DeliveryLedger()
    _record(ledger, rules, failures, results)
    restored = DeliveryLedger.from_dict(ledger.to_dict())
    assert restored.pending() == ledger.pending() == []
    assert restored.artifacts[ARTIFACT]["rule_suspect"][0]["rule_suspect"] == "column_contains_non_numeric_text"


def test_stale_validation_evidence_remains_a_product_issue():
    """版本失效标记（字符串 issue）不能被当成规则口径问题。"""
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {}, _published())
    rules = [{"kind": "formula_errors"}]
    ledger.record("validate_spreadsheet", {"rules": rules}, _validated(ARTIFACT, rules, failures=[]))
    assert ledger.pending() == []
    ledger.record("apply_spreadsheet_changes", {},
                  from_payload({"status": "success", "file_path": ARTIFACT, "content_version": "sha256:v2",
                                "receipt": {"operation_id": "op2", "state": "committed", "targets": [
                                    {"path": ARTIFACT, "after_version": "sha256:v2", "op": "update",
                                     "publish_status": "published"}]},
                                "observation": {"calculation_inputs": "changed"}}))
    assert "validation_failed_or_partial" in ledger.pending()[0]["missing"]


async def _run_final_replies(engine: AgentEngine, replies: list[str]):
    events: list = []
    queue = list(replies)

    async def live_stream():
        yield StreamDelta(content_delta=queue.pop(0))
        yield StreamDelta(finish_reason="stop", usage={"prompt_tokens": 2, "completion_tokens": 2})

    engine._client.chat.completions.create = AsyncMock(side_effect=lambda **_kwargs: live_stream())
    result = await run_tool_loop(
        engine, SkillMatchResult(skills_used=[], route_mode="fallback", system_contexts=[]),
        on_event=events.append,
    )
    return result, events


def _rule_scope_or_genuine(engine: AgentEngine, *, genuine: bool) -> None:
    rules = [{"column": "C", "expected": 1001.2, "kind": "total", "sheet": "数据", "tolerance": 0.001}]
    results = [{"rule": 0, "kind": "total", "sheet": "数据", "checked": 20, "failed": 1, "skipped": 1}]
    failures = [{"rule": 0, "kind": "total", "sheet": "数据", "cell": "C2",
                 "actual": 950.0 if genuine else "数值",
                 "expected": 1001.2 if genuine else "数值（非数值文本不计入合计）",
                 "reason": "total_mismatch" if genuine else "non_numeric_text"}]
    engine._state.delivery.record("apply_spreadsheet_changes", {}, _published())
    engine._state.delivery.record("validate_spreadsheet", {"file_path": ARTIFACT, "rules": rules},
                                  _validated(ARTIFACT, rules, failures=failures, results=results))


@pytest.mark.asyncio
async def test_rule_scope_issue_adds_no_turn_but_genuine_total_error_still_does():
    """#110 的返工轮只应来自真实产物缺陷，不能来自 agent 自己写错的规则口径。"""
    engine = _engine()
    _rule_scope_or_genuine(engine, genuine=False)
    assert engine._state.delivery.pending() == []
    result, events = await _run_final_replies(engine, ["已完成交付。"])
    assert result.reply == "已完成交付。"
    assert not any(m.get("_prompt_kind") == "delivery_evidence" for m in engine._memory.messages)
    assert not any(event.event_type == EventType.RETRACT_TEXT for event in events)

    engine = _engine()
    _rule_scope_or_genuine(engine, genuine=True)
    assert "validation_failed_or_partial" in engine._state.delivery.pending()[0]["missing"]
    result, _ = await _run_final_replies(engine, ["已完成交付。", "已修正合计并复核。"])
    kinds = [m.get("_prompt_kind") for m in engine._memory.messages]
    assert kinds.count("delivery_evidence") == 1
    assert "已修正合计并复核。" in result.reply
    assert "尚未完成全部交付核验" in result.reply


def test_real_validate_receipts_classify_misuse_and_keep_genuine_errors(tmp_path):
    """工具真回执 → DeliveryLedger 的联调：口径误用不返工，真合计错误仍判失败。"""
    from openpyxl import Workbook
    from excelmanus.tools.context import use_workspace
    from excelmanus.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    (tmp_path / "outputs").mkdir()
    book = Workbook()
    data = book.active
    data.title = "数据"
    data.append(["金额"])
    for value in (100.0, 200.0, 300.0):
        data.append([value])
    analysis = book.create_sheet("回归分析")
    analysis.append(["指标", "数值"])
    analysis.append(["斜率", 6.707067669172933])
    analysis.append(["结论", "销售额 = 1.4338 + 6.7071 × 广告投入"])
    book.save(tmp_path / "outputs" / "报告.xlsx")

    path = "outputs/报告.xlsx"
    misuse_rules = [{"kind": "total", "sheet": "回归分析", "column": "B",
                     "expected": 6.707067669172933, "tolerance": 1e-9}]
    genuine_rules = [{"kind": "total", "sheet": "数据", "column": "A", "expected": 999}]
    with use_workspace(tmp_path):
        misuse = registry.call_tool("validate_spreadsheet", {"file_path": path, "rules": misuse_rules})
        genuine = registry.call_tool("validate_spreadsheet", {"file_path": path, "rules": genuine_rules})
    assert misuse.success and misuse.value["failures"], misuse.model_text
    assert genuine.success and genuine.value["failures"]
    version = genuine.value["content_version"]

    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {}, _published(path, version))
    ledger.record("validate_spreadsheet", {"file_path": path, "rules": genuine_rules}, genuine)
    row = ledger.pending()[0]
    assert "validation_failed_or_partial" in row["missing"]      # 真合计错误不放宽
    assert "rule_suspect" not in row

    suspected = DeliveryLedger()
    suspected.record("apply_spreadsheet_changes", {}, _published(path, version))
    suspected.record("validate_spreadsheet", {"file_path": path, "rules": misuse_rules}, misuse)
    assert suspected.pending() == []                              # 口径误用不再返工
    assert suspected.artifacts[path]["issues"] == []
    assert suspected.artifacts[path]["rule_suspect"]
