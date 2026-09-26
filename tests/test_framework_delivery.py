"""Framework contracts: receipts, recoverable SDK errors and versioned evidence."""
import asyncio
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from excelmanus.code_mode import CodeModeSession, SdkCallRecord, attach_sdk_calls, render_sdk_source
from excelmanus.engine_core.delivery import DeliveryLedger
from excelmanus.engine_core.execution_facts import publications
from excelmanus.engine_core.tool_result import ToolResult, ToolUiMeta, from_payload, error_result
from excelmanus.tools.context import use_workspace
from excelmanus.tools.registry import ToolDef, ToolRegistry


def committed(path="report.xlsx", version="v1", operation_id="op1", *, requirements=None):
    return from_payload({"status": "success", "file_path": path, "content_version": version,
                         "receipt": {"operation_id": operation_id, "state": "committed", "targets": [
                             {"path": path, "after_version": version, "op": "create", "publish_status": "published"}]},
                         "observation": {"verification_requirements": requirements or {}}})


def test_read_versions_and_uncommitted_intentions_are_not_publications():
    assert publications({"file_path": "input.csv", "content_version": "v1", "status": "success"}) == []
    assert publications({"dry_run": True, "receipt": committed().value["receipt"]}) == []
    failed = {"status": "error", "receipt": committed().value["receipt"]}
    assert publications(failed)[0]["file_path"] == "report.xlsx"
    arbitrary = {"status": "success", "files": 5, "file_path": {"customer": "x"}, "published": 7, "receipt": {"targets": "text"}}
    assert publications(arbitrary) == []
    assert DeliveryLedger().record("mcp_arbitrary_data", {}, from_payload(arbitrary)) == []


def test_multi_artifact_versions_come_from_each_receipt_target():
    from excelmanus.engine_core.session_state import SessionState
    from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
    from excelmanus.workbook_commit import seed_seen_versions
    state = SessionState()
    seed_seen_versions({})
    result = committed("a.xlsx", "va")
    result.value["receipt"]["targets"].append({"path": "b.xlsx", "op": "create", "after_version": "vb", "publish_status": "published"})
    result.ui_meta = ToolUiMeta(files=["a.xlsx", "b.xlsx"], content_version="va")
    ToolDispatcher._remember_tool_versions(SimpleNamespace(_engine=SimpleNamespace(state=state)), result)
    assert state.peek_file_version("a.xlsx") == "va"
    assert state.peek_file_version("b.xlsx") == "vb"


def test_sdk_uses_shared_host_aliases_without_tool_specific_schema_fields():
    from excelmanus.tools.workbook_tools import get_tools
    tool = next(t for t in get_tools() if t.name == "observe_spreadsheet")
    assert "path" not in tool.input_schema["properties"]
    sdk = {}
    exec(render_sdk_source([tool]), sdk)
    sdk["_call_host"] = lambda name, args: {k: v for k, v in args.items() if v is not sdk["_EM_UNSET"]}
    result = sdk["observe_spreadsheet"](path="data.xlsx", cell_range="A1:B5", content_version="v1")
    assert result["file_path"] == "data.xlsx"
    assert result["range"] == "A1:B5"
    assert result["expected_version"] == "v1"


def test_sdk_alias_default_and_transitive_fold_match_host_dispatch():
    from excelmanus.tools.file_tools import get_tools
    tool = next(t for t in get_tools() if t.name == "read_text_file")
    sdk = {}
    exec(render_sdk_source([tool]), sdk)
    sdk["_call_host"] = lambda name, args: {k: v for k, v in args.items() if v is not sdk["_EM_UNSET"]}
    assert sdk["read_text_file"](path="notes.txt", max_rows=20)["max_lines"] == 20
    assert sdk["read_text_file"](path="notes.txt", limit=20)["max_lines"] == 20
    assert "max_lines" not in sdk["read_text_file"](path="notes.txt")
    with pytest.raises(TypeError, match="同时给出"):
        sdk["read_text_file"](path="notes.txt", max_lines=10, max_rows=20)


def test_caught_sdk_failure_fails_outer_result_without_losing_commits(tmp_path):
    session = CodeModeSession(None, "root", tmp_path)
    session._record(SdkCallRecord("apply_spreadsheet_changes", True, request_id="1", publications=publications(committed().value)))
    session._record(SdkCallRecord("apply_spreadsheet_changes", False, request_id="2", error_code="INVALID_ARGS", message="bad chart"))
    result = attach_sdk_calls(from_payload({"status": "success", "return_code": 0, "published": []}), session)
    assert not result.success
    assert result.error.code == "SDK_SUBCALL_FAILED"
    assert result.value["process_status"] == "success"
    assert result.value["sdk_calls"]["unresolved"][0]["request_id"] == "2"
    assert result.ui_meta.files == ["report.xlsx"]
    assert publications(result.value)[0]["file_path"] == "report.xlsx"


@pytest.mark.asyncio
async def test_host_recorded_sdk_retry_recovers_only_the_failed_call(tmp_path, monkeypatch):
    dispatcher = AsyncMock()
    dispatcher.call_registry_tool.side_effect = [error_result("bad operation", code="INVALID_ARGS"), committed()]
    definition = ToolDef("build_report", "test", {"type": "object", "properties": {"file_path": {"type": "string"}, "mode": {"type": "string"}}, "required": ["file_path"]}, lambda **args: None)
    session = CodeModeSession(dispatcher, "root", tmp_path / "bridge", tool_defs=[definition], call_timeout=5)
    monkeypatch.setenv("EXCELMANUS_CODE_MODE_BRIDGE", str(session.bridge_dir))
    monkeypatch.setenv("EXCELMANUS_CODE_MODE_TIMEOUT", "5")
    sdk = {}
    exec(render_sdk_source([definition]), sdk)
    session.start()
    def program():
        try:
            sdk["build_report"]("report.xlsx", mode="bad")
        except sdk["HostToolError"] as exc:
            return exc.retry(mode="fixed")
    try:
        result = await asyncio.to_thread(program)
        assert result["content_version"] == "v1"
    finally:
        session.stop()
        await session.wait_settlement()
    summary = session.summary()
    assert summary["failed"] == summary["recovered"] == 1
    assert summary["unresolved"] == []
    assert attach_sdk_calls(from_payload({"status": "success", "return_code": 0}), session).success


def test_retry_cannot_replay_a_partially_committed_call(tmp_path):
    session = CodeModeSession(None, "root", tmp_path)
    session._record(SdkCallRecord("build_report", False, request_id="1", arguments={"file_path": "report.xlsx"}, publications=publications(committed().value)))
    response = tmp_path / "2.resp.json"
    session._handle_request({"id": 2, "tool": "build_report", "arguments": {"file_path": "report.xlsx"}, "retry_of": "1"}, response)
    assert json.loads(response.read_text())["error"]["code"] == "SDK_RETRY_UNSAFE"


@pytest.mark.parametrize("code", ["TIMEOUT", "CANCELLED", "DISPATCH_ERROR", "EXTERNAL_COMMIT_UNKNOWN"])
def test_retry_cannot_replay_uncertain_or_cancelled_outcomes(tmp_path, code):
    session = CodeModeSession(None, "root", tmp_path)
    session._record(SdkCallRecord("build_report", False, request_id="1", error_code=code,
                                  arguments={"file_path": "report.xlsx"}))
    response = tmp_path / "2.resp.json"
    session._handle_request({"id": 2, "tool": "build_report", "arguments": {"file_path": "report.xlsx"}, "retry_of": "1"}, response)
    assert json.loads(response.read_text())["error"]["code"] == "SDK_RETRY_UNSAFE"


def test_ledger_invalidates_old_checks_and_survives_snapshot():
    from excelmanus.engine_core.session_state import SessionState
    state = SessionState()
    ledger = state.delivery
    req = {"formula_count": 1}
    ledger.record("apply_spreadsheet_changes", {}, committed(requirements=req))
    calc = committed(version="v2", operation_id="op2")
    calc.value["formula_recalculation"] = {"status": "recalculated", "errors": []}
    ledger.record("calculate_spreadsheet", {}, calc)
    args = {"rules": [{"kind": "formula_errors"}]}
    validation = from_payload({"status": "success", "file_path": "report.xlsx", "content_version": "v2", "validation_status": "complete", "valid": True})
    ledger.record("validate_spreadsheet", args, validation)
    assert not ledger.pending()
    ledger.record("apply_spreadsheet_changes", {}, committed(version="v3", operation_id="op3", requirements=req))
    ledger.record("validate_spreadsheet", args, validation)
    # v3 上的校验是 complete+valid，所以没有产物缺陷；但 v2 的重算/校验证据随
    # v3 写入过期，重算与"需重新校验"的义务仍在（过期不是缺陷，见
    # tests/test_delivery_stale_validation_regression.py）。
    missing = set(ledger.pending()[0]["missing"])
    assert {"calculation", "formula_errors", "validation_stale"} <= missing
    assert "validation_failed_or_partial" not in missing
    assert ledger.pending()[0]["issues"] == []
    restored = SessionState.from_dict(state.to_dict())
    assert restored.delivery.pending() == ledger.pending()
    # Parent SDK wrap-up must not rewind a file after a newer native check.
    ledger.record("run_code", {}, from_payload({"status": "success", "sdk_calls": {"writes": publications(calc.value)}}))
    assert ledger.artifacts["report.xlsx"]["content_version"] == "v3"


def _calc_result(version, operation_id, errors):
    # 与真实 calculate_spreadsheet 回执同形：无 observation，formula_recalculation 是唯一事实。
    return from_payload({"status": "success", "file_path": "report.xlsx", "content_version": version,
                         "receipt": {"operation_id": operation_id, "state": "committed", "targets": [
                             {"path": "report.xlsx", "after_version": version, "op": "update", "publish_status": "published"}]},
                         "formula_recalculation": {"status": "recalculated", "errors": errors}})


def test_successful_recalculation_satisfies_formula_error_evidence():
    # 一次成功的全簿重算（0 错误）就是完整公式错误检查；不再要求等价的 validate 往返。
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {}, committed(requirements={"formula_count": 1}))
    ledger.record("calculate_spreadsheet", {}, _calc_result("v2", "op2", []))
    assert ledger.pending() == []
    # 重算发现公式错误时不得记账（allow_formula_errors 容错发布也不算证据）。
    ledger.record("apply_spreadsheet_changes", {}, committed(version="v3", operation_id="op3", requirements={"formula_count": 1}))
    ledger.record("calculate_spreadsheet", {}, _calc_result("v4", "op4", [{"cell": "A2", "error": "#REF!"}]))
    assert {"calculation", "formula_errors"} <= set(ledger.pending()[0]["missing"])


def test_value_preserving_write_carries_formula_evidence():
    # 计算输入未变的写入（纯格式等）不作废数据级证据，不再强制重算往返。
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {}, committed(requirements={"formula_count": 1}))
    ledger.record("calculate_spreadsheet", {}, _calc_result("v2", "op2", []))
    assert ledger.pending() == []
    untouched = committed(version="v3", operation_id="op3", requirements={"formula_count": 1})
    untouched.value["observation"]["calculation_inputs"] = "unchanged"
    untouched.value["observation"]["formula_cache"] = "preserved_unchanged_calculation_inputs"
    ledger.record("apply_spreadsheet_changes", {}, untouched)
    assert ledger.pending() == []
    # 计算输入变了 → 证据随版本作废，重算义务回来。
    changed = committed(version="v4", operation_id="op4", requirements={"formula_count": 1})
    changed.value["observation"]["calculation_inputs"] = "changed"
    changed.value["observation"]["formula_cache"] = "invalidated_by_serialization; calculate explicitly when needed"
    ledger.record("apply_spreadsheet_changes", {}, changed)
    assert {"calculation", "formula_errors"} <= set(ledger.pending()[0]["missing"])


def test_caches_dropped_write_does_not_carry_formula_evidence():
    # 计算输入未变但缓存没抄回来（源文件本就无缓存）：公式证据不能延续。
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {}, committed(requirements={"formula_count": 1}))
    ledger.record("calculate_spreadsheet", {}, _calc_result("v2", "op2", []))
    lost = committed(version="v3", operation_id="op3", requirements={"formula_count": 1})
    lost.value["observation"]["calculation_inputs"] = "unchanged"
    lost.value["observation"]["formula_cache"] = "invalidated_by_serialization; calculate explicitly when needed"
    ledger.record("apply_spreadsheet_changes", {}, lost)
    assert {"calculation", "formula_errors"} <= set(ledger.pending()[0]["missing"])


def test_observed_external_change_invalidates_evidence_without_fabricating_commit():
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {}, committed(requirements={"formula_count": 1}))
    before = list(ledger.commits)
    ledger.artifacts["report.xlsx"]["checks"] = {"calculation": True, "formula_errors": True}
    assert ledger.pending() == []
    ledger.begin_turn()
    observation = from_payload({"status": "success", "file_path": "./report.xlsx", "content_version": "external-v2"})
    assert ledger.record("observe_spreadsheet", {}, observation) == []
    assert ledger.commits == before
    assert "calculation" in ledger.pending()[0]["missing"]


def test_ledger_does_not_accept_export_cropped_image_or_unrelated_validation():
    ledger = DeliveryLedger()
    req = {"drawings": [{"id": "D:chart:0", "sheet": "D"}]}
    ledger.record("apply_spreadsheet_changes", {}, committed(requirements=req))
    value = {"status": "success", "file_path": "report.xlsx", "content_version": "v1", "sheet": "D",
             "visual_coverage": {"whole_region": True, "complete_objects": []}}
    preview = from_payload(value, ui_meta=ToolUiMeta(image={"attachment": {"attachmentId": "a"}}))
    ledger.record("preview_spreadsheet", {}, preview)
    assert "visual_preview" in ledger.pending()[0]["missing"]
    preview.value["visual_coverage"]["complete_objects"] = ["D:chart:0"]
    ledger.record("render_spreadsheet", {}, preview)
    assert ledger.pending()
    ledger.record("preview_spreadsheet", {}, preview, vision=False)
    assert ledger.pending()
    ledger.record("preview_spreadsheet", {}, preview)
    assert not ledger.pending()
    failed = from_payload({"status": "success", "file_path": "report.xlsx", "content_version": "v1", "validation_status": "complete", "valid": False, "failures": [{"kind": "total"}]})
    ledger.record("validate_spreadsheet", {"rules": [{"kind": "total", "expected": 5}]}, failed)
    passed = from_payload({**failed.value, "valid": True, "failures": []})
    ledger.record("validate_spreadsheet", {"rules": [{"kind": "unique"}]}, passed)
    assert ledger.pending()[0]["issues"]


def test_completion_feedback_is_evidence_based_and_bounded():
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {}, committed(requirements={"formula_count": 1}))
    assert ledger.next_feedback()
    assert ledger.next_feedback() is None
    ledger.record("apply_spreadsheet_changes", {}, committed(version="v2", operation_id="op2", requirements={"formula_count": 1}))
    assert ledger.next_feedback()
    ledger.record("apply_spreadsheet_changes", {}, committed(version="v3", operation_id="op3", requirements={"formula_count": 1}))
    assert ledger.next_feedback() is None


def test_failed_write_is_not_cleared_by_unrelated_success_or_dry_run():
    ledger = DeliveryLedger()
    args = {"file_path": "report.xlsx", "operations": [{"kind": "chart"}]}
    ledger.record("apply_spreadsheet_changes", args, error_result("bad chart"), mutating=True)
    assert ledger.pending()[0]["missing"] == ["mutation_failed"]
    ledger.record("apply_spreadsheet_changes", {**args, "dry_run": True}, from_payload({"status": "success", "committed": False}))
    assert ledger.pending()
    ledger.record("apply_spreadsheet_changes", {"file_path": "report.xlsx", "operations": [{"kind": "format"}]}, committed())
    assert ledger.pending()[0]["missing"] == ["mutation_failed"]
    ledger.record("apply_spreadsheet_changes", args, committed(version="v2", operation_id="op2"))
    assert ledger.pending() == []


def test_continuation_retains_pending_evidence_but_new_task_has_new_scope():
    ledger = DeliveryLedger()
    ledger.record("apply_spreadsheet_changes", {}, committed(requirements={"formula_count": 1}))
    pending = ledger.pending()
    ledger.begin_turn()
    assert ledger.pending() == pending
    ledger.start_task("new-task")
    assert ledger.pending() == []
    assert "report.xlsx" in ledger.artifacts  # historical facts are retained


def test_text_reply_requests_evidence_before_finalizing():
    from excelmanus.agent.loop import _handle_text_reply
    from excelmanus.engine_core.session_state import SessionState
    from unittest.mock import Mock
    state = SessionState()
    state.delivery.record("apply_spreadsheet_changes", {}, committed(requirements={"formula_count": 1}))
    engine = SimpleNamespace(_state=state, _memory=Mock())
    action, result = _handle_text_reply(engine, message=SimpleNamespace(content="已完成", tool_calls=None, role="assistant"), iteration=1,
                                       all_tool_results=[], total_prompt_tokens=0, total_completion_tokens=0,
                                       _finalize_result=lambda **kw: kw)
    assert action == "continue" and result is None
    assert engine._memory.add_user_message.call_args.kwargs["prompt_kind"] == "delivery_evidence"


def test_spilled_observation_keeps_decision_cells_and_lossless_source(tmp_path):
    from excelmanus.engine_core.spill import SpillStore, expose_spreadsheet_value
    payload = {"status": "success", "schema_version": "workbook/2", "file_path": "report.xlsx", "content_version": "v1",
               "sheets": [{"name": str(i), "geometry": "x" * 1500} for i in range(20)],
               "regions": [{"sheet": "D", "rect": {"r0": 1, "c0": 1, "r1": 2, "c1": 2},
                            "cells": {"1,1": {"v": "年份", "t": "s"}, "2,1": {"v": 2025, "t": "n"},
                                      "2,2": {"v": None, "f": "=A2*2", "cached": "no"}}}],
               "coverage": {"scope": "requested region"}}
    result = from_payload(payload)
    output = expose_spreadsheet_value(result, store=SpillStore(tmp_path))
    projected = json.loads(output.model_text)
    assert projected["data_preview"]["regions"][0]["cells"]["2,1"]["v"] == 2025
    assert projected["data_preview"]["regions"][0]["cells"]["2,2"]["cached"] == "no"
    assert output.value == payload
    assert len(output.model_text) < 8000


def test_skill_resources_are_lazy_and_explicitly_retrievable():
    from excelmanus.skillpacks.models import Skillpack
    skill = Skillpack("report", "report", "Use the report workflow.", "system", ".",
                      resource_contents={"references/report.md": "SUPPORTING_BODY" * 10000})
    body = skill.render_context()
    assert "SUPPORTING_BODY" not in body
    assert "resource:report/references/report.md" in body
    assert "SUPPORTING_BODY" in skill.render_context(include_resources=True)


def test_workflow_is_schema_checked_and_permission_scoped(tmp_path):
    from excelmanus.knowledge.workflows import workflow_detail
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    source = {t.name: t for t in registry.get_all_tools()}
    workflow = workflow_detail("spreadsheet-report", source, "python")
    assert workflow["example"]["validation"]["schema"] == "passed"
    assert len(workflow["steps"]) == 4
    source.pop("apply_spreadsheet_changes")
    assert workflow_detail("spreadsheet-report", source)["status"] == "unavailable"


def test_receipt_visual_workflow_is_schema_checked_and_uses_bounded_route(tmp_path):
    from excelmanus.knowledge.workflows import workflow_detail

    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    source = {t.name: t for t in registry.get_all_tools()}
    workflow = workflow_detail("把收款收据还原成可编辑 Excel", source, "python")
    assert workflow["workflow"] == "receipt-visual-replica"
    assert workflow["example"]["validation"]["schema"] == "passed"
    assert [step["tool"] for step in workflow["steps"]] == [
        "apply_spreadsheet_changes", "calculate_spreadsheet",
        "validate_spreadsheet", "preview_spreadsheet",
    ]
    assert "run_code" not in workflow["example"]["tools"]


def test_workflow_matching_does_not_use_single_character_substrings(tmp_path):
    from excelmanus.knowledge.workflows import workflow_detail

    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    source = {t.name: t for t in registry.get_all_tools()}
    assert workflow_detail("图", source)["status"] == "not_found"


def test_report_recipe_runs_through_complete_versioned_visual_delivery(tmp_path, monkeypatch):
    from excelmanus.runtime_capabilities import office_executable
    from excelmanus.knowledge.examples import steps_for
    import shutil
    if not office_executable() or not shutil.which("pdfinfo") or not shutil.which("pdftoppm"):
        pytest.skip("LibreOffice and Poppler required")
    monkeypatch.setenv("EXCELMANUS_FORMULA_RECALC", "auto")
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    ledger, results = DeliveryLedger(), {}
    with use_workspace(tmp_path):
        for step in steps_for("report-workflow"):
            name = step["call"]["name"]
            args = dict(step["call"]["arguments"])
            for field, binding in step.get("bindings", {}).items():
                args[field] = results[binding["step"]].value[binding["field"]]
            result = registry.call_tool(name, args)
            assert result.success, result.model_text
            ledger.record(name, args, result)
            results[step["id"]] = result
    preview = results["preview"].value
    assert preview["requested_surface"] == "auto" and preview["surface"] == "print"
    assert preview["measured"]["page_count"] == 1
    assert preview["visual_coverage"]["complete_objects"] == ["看板:chart:0"]
    assert ledger.pending() == []


@pytest.mark.asyncio
async def test_sdk_commits_emit_mutation_before_failed_parent_finishes(tmp_path):
    from excelmanus.agent.session import AgentEngine
    from excelmanus.config import ExcelManusConfig
    from excelmanus.events import EventType

    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    engine = AgentEngine(ExcelManusConfig(api_key="test", base_url="https://invalid.test/v1", model="test",
                                          workspace_root=str(tmp_path), jev_enabled="off", memory_enabled=False,
                                          code_policy_enabled=False), registry)
    engine._full_access_enabled = True
    events = []
    code = '''import em
em.write_text_file(file_path="outputs/report.txt", content="committed")
try:
    em.observe_spreadsheet(file_path="missing.xlsx")
except em.HostToolError:
    pass
'''
    call = SimpleNamespace(id="sdk-parent", function=SimpleNamespace(name="run_code", arguments=json.dumps({"code": code, "require_excel_deps": False})))
    result = await engine._tool_runtime.execute(call, None, events.append, 1)
    assert not result.success, result.result
    assert result.structured.error.code == "SDK_SUBCALL_FAILED"
    assert (tmp_path / "outputs/report.txt").read_text() == "committed"
    mutations = [event for event in events if event.event_type == EventType.MUTATION]
    assert any("./outputs/report.txt" in event.changed_files for event in mutations)
    assert not any("missing.xlsx" in path for event in mutations for path in event.changed_files)
    assert engine._state.affected_files == ["./outputs/report.txt"]
    assert result.structured.ui_meta.files == ["outputs/report.txt"]
    assert len(engine._state.write_operations_log) == 1


@pytest.mark.asyncio
async def test_primary_loop_continues_from_missing_evidence_to_verified_delivery(tmp_path, monkeypatch):
    from excelmanus.runtime_capabilities import office_executable
    from excelmanus.agent.session import AgentEngine
    from excelmanus.agent.loop import run_tool_loop
    from excelmanus.config import ExcelManusConfig
    from excelmanus.workbook_commit import content_version_of_file
    from tests.test_engine import _make_text_response, _make_tool_call_response
    if not office_executable():
        pytest.skip("LibreOffice required")
    monkeypatch.setenv("EXCELMANUS_FORMULA_RECALC", "auto")
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    engine = AgentEngine(ExcelManusConfig(api_key="test", base_url="https://invalid.test/v1", model="test",
                                          workspace_root=str(tmp_path), jev_enabled="off", memory_enabled=False,
                                          max_iterations=12), registry)
    engine._full_access_enabled = True
    create_args = {"file_path": "outputs/report.xlsx", "workbook_spec": {"sheets": [{
        "name": "Data", "dimensions": {"rows": 2, "cols": 1},
        "value_blocks": [{"start": "A1", "values": [[10]]}],
        "formula_blocks": [{"start": "A2", "formulas": [["=A1*2"]]}],
    }], "uncertainties": []}}
    call = SimpleNamespace(id="initial-create", function=SimpleNamespace(name="apply_spreadsheet_changes", arguments=json.dumps(create_args)))
    created = await engine._tool_runtime.execute(call, None, None, 1)
    assert created.success, created.result
    responses = 0
    async def model(**kwargs):
        nonlocal responses
        responses += 1
        if responses == 1:
            return _make_text_response("已生成报表。")
        if responses in (2, 3):
            name = "calculate_spreadsheet" if responses == 2 else "validate_spreadsheet"
            args = {"file_path": "outputs/report.xlsx", "expected_version": content_version_of_file(tmp_path / "outputs/report.xlsx")}
            if responses == 3:
                args["rules"] = [{"kind": "formula_errors"}]
            return _make_tool_call_response([(f"verify-{responses}", name, json.dumps(args))])
        assert responses == 4, "completion correction must terminate after evidence is satisfied"
        return _make_text_response("报表已生成，公式核验通过。")
    engine._client.chat.completions.create = AsyncMock(side_effect=model)
    engine._memory.add_user_message("创建并核验报表")
    result = await run_tool_loop(engine, None, None, initial_tool_results=[created])
    assert result.reply == "报表已生成，公式核验通过。"
    assert responses == 4
    assert engine._state.delivery.pending() == []
    assert sum(m.get("_prompt_kind") == "delivery_evidence" for m in engine._memory.messages) == 1
