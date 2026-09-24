"""P3-a 并行批同因折叠：分组键、指针格式、作用域。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from excelmanus.agent.loop import (
    _build_dedup_pointer,
    _dedup_group_key,
    plan_parallel_dedup,
)
from excelmanus.config import ExcelManusConfig
from excelmanus.memory import ConversationMemory


def _fail_result(text: str, *, tool: str = "analyze_spreadsheet", args: dict | None = None):
    return SimpleNamespace(
        tool_name=tool,
        arguments=args or {},
        result=text,
        success=False,
        error="err",
        error_kind="permanent",
        defer_tool_result=False,
        pending_approval=False,
        pending_question=False,
        structured=None,
    )


def _ok_result():
    return SimpleNamespace(
        tool_name="observe_spreadsheet",
        arguments={},
        result='{"status":"ok"}',
        success=True,
        error=None,
        error_kind=None,
        defer_tool_result=False,
        pending_approval=False,
        pending_question=False,
        structured=None,
    )


def _sheet_required(args: dict, sheets: list[str] | None = None) -> str:
    sheets = sheets if sheets is not None else ["订单", "产品目录", "区域目标"]
    return json.dumps({
        "status": "error",
        "error_code": "SHEET_REQUIRED",
        "message": f"工作簿有 {len(sheets)} 张表，必须显式指定 sheet。",
        "failure_class": "invalid_args",
        "remediation": f"工作簿有多张表，补上 sheet 后再试。 可用工作表：{', '.join(sheets)}",
        "available_sheets": sheets,
    }, ensure_ascii=False)


def _tc(cid: str):
    return SimpleNamespace(id=cid)


class TestSameCauseGrouping:
    def test_double_sheet_required_folds_second(self):
        args1 = {"file_path": "a.xlsx", "mode": "aggregate", "group_by": "状态"}
        args2 = {"file_path": "a.xlsx", "mode": "distinct", "column": "状态"}
        items = [
            (_tc("call_1"), _fail_result(_sheet_required(args1), args=args1)),
            (_tc("call_2"), _fail_result(_sheet_required(args2), args=args2)),
        ]
        plan = plan_parallel_dedup(items)
        assert set(plan) == {1}
        pointer = json.loads(plan[1])
        assert pointer["error_code"] == "SHEET_REQUIRED"
        assert pointer["dedup"] == "same-batch"
        assert pointer["ref_tool_call_id"] == "call_1"
        assert "可用工作表" in pointer["remediation"]
        assert pointer["available_sheets"] == ["订单", "产品目录", "区域目标"]
        assert "spill" not in plan[1]
        assert len(plan[1]) <= 400

    def test_different_code_no_merge(self):
        items = [
            (_tc("c1"), _fail_result(_sheet_required({}))),
            (_tc("c2"), _fail_result(json.dumps({"status": "error", "error_code": "SHEET_NOT_FOUND"}))),
        ]
        assert plan_parallel_dedup(items) == {}

    def test_different_files_no_merge(self):
        items = [
            (_tc("c1"), _fail_result(_sheet_required({}), args={"file_path": "a.xlsx"})),
            (_tc("c2"), _fail_result(_sheet_required({}), args={"file_path": "b.xlsx"})),
        ]
        assert plan_parallel_dedup(items) == {}

    def test_success_never_folds(self):
        ok = _ok_result()
        items = [(_tc("c1"), ok), (_tc("c2"), _fail_result(_sheet_required({})))]
        assert plan_parallel_dedup(items) == {}

    def test_single_failure_no_plan(self):
        items = [(_tc("c1"), _fail_result(_sheet_required({})))]
        assert plan_parallel_dedup(items) == {}

    def test_triple_folds_two(self):
        items = [
            (_tc(f"c{i}"), _fail_result(_sheet_required({}), args={"file_path": "a.xlsx", "k": i}))
            for i in range(3)
        ]
        plan = plan_parallel_dedup(items)
        assert set(plan) == {1, 2}
        assert "第2/3条" in plan[1]
        assert "第3/3条" in plan[2]

    def test_unparseable_result_excluded(self):
        items = [
            (_tc("c1"), _fail_result("plain text failure")),
            (_tc("c2"), _fail_result("another plain failure")),
        ]
        assert plan_parallel_dedup(items) == {}

    def test_defer_and_pending_excluded(self):
        r1 = _fail_result(_sheet_required({}), args={"file_path": "a.xlsx"})
        r2 = _fail_result(_sheet_required({}), args={"file_path": "a.xlsx"})
        r2.defer_tool_result = True
        items = [(_tc("c1"), r1), (_tc("c2"), r2)]
        assert plan_parallel_dedup(items) == {}

    def test_identical_args_hint(self):
        args = {"file_path": "a.xlsx", "mode": "aggregate"}
        items = [
            (_tc("c1"), _fail_result(_sheet_required(args), args=args)),
            (_tc("c2"), _fail_result(_sheet_required(args), args=args)),
        ]
        plan = plan_parallel_dedup(items)
        assert json.loads(plan[1])["args_hint"] == "同参"

    def test_group_key_none_for_success(self):
        assert _dedup_group_key(_ok_result()) is None

    def test_pointer_no_spill_namespace(self):
        p = _build_dedup_pointer(
            code="SHEET_REQUIRED", failure_class="invalid_args", group="g",
            ref_tool_call_id="call_1", first_tool="analyze_spreadsheet",
            position="第2/2条", remediation="补上 sheet 后再试。",
            sheets=["订单"], args_hint="同参",
        )
        assert '"spill"' not in p
        assert not p.startswith("spill:")
        parsed = json.loads(p)
        assert parsed["available_sheets"] == ["订单"]

    def test_memory_keeps_full_result_and_projects_pointer(self):
        memory = ConversationMemory(ExcelManusConfig(
            api_key="test", base_url="https://example.test/v1", model="test",
        ))
        full = _sheet_required({"file_path": "a.xlsx"})
        pointer = _build_dedup_pointer(
            code="SHEET_REQUIRED", failure_class="invalid_args", group="g",
            ref_tool_call_id="call_1", first_tool="analyze_spreadsheet",
            position="第2/2条", remediation="补上 sheet 后再试。",
            sheets=["订单"], args_hint="同参",
        )
        memory.add_tool_result("call_2", full, projection_content=pointer)

        assert memory._messages[-1]["content"] == full
        assert memory.get_messages()[-1]["content"] == full
        projected = memory.project_for_request(system_prompts=[])
        assert projected[-1]["content"] == pointer
        assert memory._messages[-1]["content"] == full
