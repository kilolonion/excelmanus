"""ExcelManus 内置表格工作流技能的行为契约。"""

from __future__ import annotations

import re
from pathlib import Path

from jsonschema import Draft202012Validator

from excelmanus.config import ExcelManusConfig
from excelmanus.skillpacks.loader import SkillpackLoader
from excelmanus.tools import ToolRegistry
from excelmanus.tools.workbook_tools import get_tools


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "excelmanus" / "skillpacks" / "system" / "spreadsheet_workflow"


def _loader() -> SkillpackLoader:
    config = ExcelManusConfig(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        workspace_root=str(ROOT),
        skills_system_dir=str(ROOT / "excelmanus" / "skillpacks" / "system"),
        skills_discovery_enabled=False,
    )
    return SkillpackLoader(config, ToolRegistry())


def test_workflow_skill_loads_all_declared_resources() -> None:
    skill = _loader().load_all()["spreadsheet_workflow"]

    assert skill.version == "1.0.0"
    assert set(skill.resources) == {
        "references/tool_matrix.md",
        "references/recipes.md",
        "references/quality_security.md",
    }
    assert set(skill.resources) == set(skill.resource_contents)
    assert "apply_spreadsheet_changes" in skill.instructions
    assert "expected_version" in skill.instructions


def test_receipt_visual_replica_skill_is_discoverable_and_optional() -> None:
    skills = _loader().load_all()
    skill = skills["receipt_visual_replica"]

    assert "read_image" in skill.instructions
    assert "analyze_layout=true" in skill.instructions
    assert "apply_spreadsheet_changes" in skill.instructions
    assert "calculate_spreadsheet" in skill.instructions
    assert "validate_spreadsheet" in skill.instructions
    assert "preview_spreadsheet" in skill.instructions
    # The skill describes possible routes rather than forcing one execution path.
    assert "可以" in skill.instructions
    assert "可选" in skill.instructions


def test_documented_v2_shapes_validate_against_registered_schemas() -> None:
    tools = {tool.name: tool for tool in get_tools()}

    calls = {
        "observe_spreadsheet": {
            "file_path": "outputs/book.xlsx",
            "sheet": "汇总",
            "mode": "range",
            "range": "A1:B12",
            "facets": ["data", "objects", "geometry"],
        },
        "analyze_spreadsheet": {
            "file_path": "outputs/book.xlsx",
            "sheet": "明细",
            "mode": "aggregate",
            "group_by": ["部门"],
            "aggregations": {"金额": "sum"},
            "sort_by": "金额_sum",
            "ascending": False,
            "max_rows": 20,
        },
        "apply_spreadsheet_changes": {
            "file_path": "outputs/book.xlsx",
            "expected_version": "sha256:test",
            "operations": [
                {
                    "kind": "write",
                    "sheet": "汇总",
                    "start_cell": "H2",
                    "values": [["已处理"]],
                },
                {
                    "kind": "chart",
                    "sheet": "汇总",
                    "chart_type": "bar",
                    "data_range": "A1:B12",
                    "categories_range": "A2:A12",
                    "target_cell": "E2",
                },
            ],
        },
    }

    for name, arguments in calls.items():
        errors = list(Draft202012Validator(tools[name].input_schema).iter_errors(arguments))
        assert not errors, f"{name} example drifted: {errors}"


def test_csv_only_workspace_bootstrap_route_is_documented() -> None:
    """只有 CSV 的工作区里写工具仍可用：技能层必须说明可直接新建，不能自称唯一入口。"""
    skill = _loader().load_all()["spreadsheet_workflow"]
    instructions = skill.instructions

    assert "唯一的工作簿意图提交入口" not in instructions
    assert "apply_spreadsheet_changes" in instructions
    assert "convert_spreadsheet" in instructions
    assert "outputs/" in instructions
    assert "workbook_spec" in instructions
    assert "openpyxl/pandas 直存" in instructions
    # 新契约：新建工作簿不依赖已有 xlsx，convert 只用于真正的格式转换。
    assert "新建不依赖已有 xlsx" in instructions
    assert "不是新建工作簿的前置步骤" in instructions
    # 仍被门控的是依赖已有工作簿的 trace 工具；convert 产物工作表名是 input。
    assert "trace_spreadsheet_formulas" in instructions
    assert "仍被目录门控" in instructions
    assert "工作表名是 `input`" in instructions

    chart = (ROOT / "excelmanus/skillpacks/system/chart_basic/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "只有 CSV" in chart
    assert "apply_spreadsheet_changes" in chart
    assert "convert_spreadsheet" in chart
    assert "workbook_spec" in chart
    assert "新建不依赖已有 xlsx" in chart
    assert "不是新建工作簿的前置步骤" in chart
    assert "trace_spreadsheet_formulas" in chart


def test_legacy_analysis_and_chart_code_no_longer_reads_workbooks_directly() -> None:
    paths = [
        ROOT / "excelmanus/skillpacks/system/run_code_templates/references/analysis_patterns.md",
        ROOT / "excelmanus/skillpacks/system/chart_basic/references/chart_and_table_templates.md",
    ]
    fenced = "\n".join(
        block
        for path in paths
        for block in re.findall(r"```python\n(.*?)```", path.read_text(encoding="utf-8"), re.S)
    )

    assert "from em import" in fenced
    assert "pd.read_excel" not in fenced
    assert "openpyxl.load_workbook" not in fenced
    assert ".to_excel(" not in fenced
