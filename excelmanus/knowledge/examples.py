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
