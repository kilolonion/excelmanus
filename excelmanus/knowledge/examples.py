"""Maintained examples validated against the caller's current tool schemas.

Fetching examples never runs tools. Python and JSON are rendered from the same
steps; observed version bindings are explicit and never fabricated as evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry


@dataclass(frozen=True)
class Example:
    id: str
    title: str
    description: str
    tools: tuple[str, ...]


EXAMPLES = (
    Example("observe-range", "读取数据、版式和几何", "查看同一版本的指定范围并保留覆盖说明。", ("observe_spreadsheet",)),
    Example("create-workbook", "创建带公式与样式的工作簿", "使用产品共用的 WorkbookSpec 示例新建文件。", ("apply_spreadsheet_changes",)),
    Example("observe-edit", "先读取版本再修改单元格", "将观察返回的 content_version 绑定到下一次写入。", ("observe_spreadsheet", "apply_spreadsheet_changes")),
    Example("analyze-quality", "检查工作表数据质量", "查询质量分析；不要把采样结果当全表证据。", ("analyze_spreadsheet",)),
    Example("preview-range", "查看工作簿外观", "获得绑定版本的区域预览供视觉判断。", ("preview_spreadsheet",)),
    Example("report-workflow", "汇总同比与图表看板完整闭环", "创建、重算、校验、自动图表预览；版本顺序绑定。", ("apply_spreadsheet_changes", "calculate_spreadsheet", "validate_spreadsheet", "preview_spreadsheet")),
    Example("receipt-visual-replica", "图片收据还原完整闭环", "从已观察图片建立可编辑版式，重算、金额核对并做视觉预览。", ("apply_spreadsheet_changes", "calculate_spreadsheet", "validate_spreadsheet", "preview_spreadsheet")),
    Example("cross-workbook-automation", "跨表匹配与公式写回", "观察来源、确定匹配键、写回公式并校验未匹配项。", ("observe_spreadsheet", "analyze_spreadsheet", "apply_spreadsheet_changes", "calculate_spreadsheet", "validate_spreadsheet")),
    Example("statistical-analysis", "统计分析与回归写回", "用 Python 计算统计结果，创建工作簿、图表和可核验公式。", ("run_code", "apply_spreadsheet_changes", "calculate_spreadsheet", "validate_spreadsheet", "preview_spreadsheet")),
)
EXAMPLE_BY_ID = {example.id: example for example in EXAMPLES}


def _step(id: str, tool: str, **arguments: Any) -> dict:
    return {"id": id, "call": {"name": tool, "arguments": arguments}}


def steps_for(id: str) -> list[dict]:
    from excelmanus.tools.workbook_examples import workbook_creation_example

    if id == "observe-range":
        return [_step("observed", "observe_spreadsheet", file_path="example.xlsx", sheet="明细", mode="range",
                      range="A1:D4", facets=["data", "presentation", "geometry"])]
    if id == "create-workbook":
        return [_step("created", "apply_spreadsheet_changes", file_path="outputs/example.xlsx", create=True,
                      workbook_spec=workbook_creation_example())]
    if id == "observe-edit":
        observed = _step("observed", "observe_spreadsheet", file_path="example.xlsx", sheet="明细", mode="range", range="B3", facets=["data"])
        changed = _step("changed", "apply_spreadsheet_changes", file_path="example.xlsx",
                        expected_version="${observed.content_version}",
                        operations=[{"kind": "write", "sheet": "明细", "start_cell": "B3", "values": [[5]]}])
        changed["bindings"] = {"expected_version": {"step": "observed", "field": "content_version"}}
        return [observed, changed]
    if id == "analyze-quality":
        return [_step("quality", "analyze_spreadsheet", file_path="example.xlsx", sheet="明细", mode="quality")]
    if id == "preview-range":
        return [_step("preview", "preview_spreadsheet", file_path="example.xlsx", sheet="明细", range="A1:D4", surface="workbench")]
    if id == "report-workflow":
        from excelmanus.tools.workbook_examples import report_creation_example
        steps = [_step("created", "apply_spreadsheet_changes", file_path="outputs/report.xlsx", workbook_spec=report_creation_example()),
                 _step("calculated", "calculate_spreadsheet", file_path="outputs/report.xlsx", expected_version="${created.content_version}"),
                 _step("checked", "validate_spreadsheet", file_path="outputs/report.xlsx", expected_version="${calculated.content_version}", rules=[{"kind": "formula_errors"}, {"kind": "unique", "sheet": "数据", "columns": ["月份"]}]),
                 _step("preview", "preview_spreadsheet", file_path="outputs/report.xlsx", expected_version="${calculated.content_version}", sheet="看板", range="A1:F28", surface="auto")]
        for step, previous in zip(steps[1:], ("created", "calculated", "calculated")):
            step["bindings"] = {"expected_version": {"step": previous, "field": "content_version"}}
        return steps
    if id == "receipt-visual-replica":
        from excelmanus.tools.workbook_examples import visual_receipt_creation_example

        spec = visual_receipt_creation_example()
        steps = [
            _step("created", "apply_spreadsheet_changes", file_path="outputs/receipt.xlsx", create=True,
                  workbook_spec=spec),
            _step("calculated", "calculate_spreadsheet", file_path="outputs/receipt.xlsx",
                  expected_version="${created.content_version}"),
            _step("checked", "validate_spreadsheet", file_path="outputs/receipt.xlsx",
                  expected_version="${calculated.content_version}",
                  rules=[{"kind": "formula_errors"}, {"kind": "total", "sheet": "明细",
                                                          "column": "D", "header_row": 2,
                                                          "expected": 20, "tolerance": 0.01}]),
            _step("preview", "preview_spreadsheet", file_path="outputs/receipt.xlsx",
                  expected_version="${calculated.content_version}", sheet="明细",
                  range="A1:D4", surface="workbench"),
        ]
        for step, previous in zip(steps[1:], ("created", "calculated", "calculated")):
            step["bindings"] = {"expected_version": {"step": previous, "field": "content_version"}}
        return steps
    if id == "cross-workbook-automation":
        observed = _step("observed", "observe_spreadsheet", file_path="outputs/orders.xlsx", mode="overview")
        analyzed = _step("analyzed", "analyze_spreadsheet", file_path="outputs/orders.xlsx", mode="quality")
        changed = _step("changed", "apply_spreadsheet_changes", file_path="outputs/orders.xlsx",
                        expected_version="${observed.content_version}",
                        operations=[{"kind": "write", "sheet": "订单", "start_cell": "G2", "values": [["=D2*E2"]]}])
        calculated = _step("calculated", "calculate_spreadsheet", file_path="outputs/orders.xlsx",
                           expected_version="${changed.content_version}")
        checked = _step("checked", "validate_spreadsheet", file_path="outputs/orders.xlsx",
                        expected_version="${calculated.content_version}", rules=[{"kind": "formula_errors"}])
        changed["bindings"] = {"expected_version": {"step": "observed", "field": "content_version"}}
        calculated["bindings"] = {"expected_version": {"step": "changed", "field": "content_version"}}
        checked["bindings"] = {"expected_version": {"step": "calculated", "field": "content_version"}}
        return [observed, analyzed, changed, calculated, checked]
    if id == "statistical-analysis":
        code = _step("computed", "run_code", code="import em\n# 替换实际列名和路径后计算回归结果。\nprint({'status': 'computed'})")
        created = _step("created", "apply_spreadsheet_changes", file_path="outputs/analysis.xlsx",
                        create=True, workbook_spec=workbook_creation_example())
        calculated = _step("calculated", "calculate_spreadsheet", file_path="outputs/analysis.xlsx",
                           expected_version="${created.content_version}")
        checked = _step("checked", "validate_spreadsheet", file_path="outputs/analysis.xlsx",
                        expected_version="${calculated.content_version}", rules=[{"kind": "formula_errors"}])
        preview = _step("preview", "preview_spreadsheet", file_path="outputs/analysis.xlsx",
                        expected_version="${calculated.content_version}", sheet="明细", range="A1:D4", surface="workbench")
        for step, previous in ((calculated, "created"), (checked, "calculated"), (preview, "calculated")):
            step["bindings"] = {"expected_version": {"step": previous, "field": "content_version"}}
        return [code, created, calculated, checked, preview]
    raise KeyError(id)


def example_detail(example: Example, source: dict, language: str = "all") -> dict:
    steps = steps_for(example.id)
    for step in steps:
        tool = source.get(step["call"]["name"])
        if tool is None:
            return {"status": "unavailable", "reason": "示例需要的工具不在当前授权目录。", "tools": list(example.tools)}
        try:
            errors = list(Draft202012Validator(tool.input_schema, registry=Registry()).iter_errors(step["call"]["arguments"]))
        except Exception as exc:
            return {"status": "unavailable", "reason": "当前 schema 无法校验示例。", "error_type": type(exc).__name__}
        if errors:
            return {"status": "unavailable", "reason": "示例与当前 schema 不一致，需要维护示例；不返回失效调用。",
                    "invalid_fields": [".".join(str(part) for part in error.absolute_path) for error in errors]}
    snippets = {}
    if language in {"all", "json"}:
        snippets["json"] = json.dumps({"steps": steps}, ensure_ascii=False, indent=2)
    if language in {"all", "python"}:
        lines = ["import em"]
        for step in steps:
            arguments = []
            for key, value in step["call"]["arguments"].items():
                binding = step.get("bindings", {}).get(key)
                expression = binding["step"] + "[" + repr(binding["field"]) + "]" if binding else repr(value)
                arguments.append(key + "=" + expression)
            lines.append(step["id"] + " = em." + step["call"]["name"] + "(" + ", ".join(arguments) + ")")
        lines.append("print(" + steps[-1]["id"] + ")")
        snippets["python"] = "\n".join(lines)
    return {"id": example.id, "title": example.title, "description": example.description,
            "tools": list(example.tools), "steps": steps, "snippets": snippets,
            "validation": {"schema": "passed", "execution": "not_run"},
            "notice": "路径、表名与业务值为示例；执行前替换为实际目标。bindings 必须来自此前真实成功调用。阅读示例不会执行工具或证明任务成功。"}
