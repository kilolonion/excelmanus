"""可视化 Skill 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from excelmanus.security import SecurityViolationError
from excelmanus.skills import ToolDef
from excelmanus.skills.chart_skill import (
    SKILL_DESCRIPTION,
    SKILL_NAME,
    SUPPORTED_CHART_TYPES,
    create_chart,
    get_tools,
    init_guard,
)


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """创建临时工作目录并初始化 FileAccessGuard。"""
    init_guard(str(tmp_path))
    return tmp_path


@pytest.fixture
def sample_excel(workspace: Path) -> Path:
    """创建包含示例数据的 Excel 文件。"""
    df = pd.DataFrame(
        {
            "月份": ["1月", "2月", "3月", "4月", "5月"],
            "销售额": [12000, 15000, 9000, 18000, 21000],
            "成本": [8000, 10000, 7000, 12000, 14000],
        }
    )
    path = workspace / "sales.xlsx"
    df.to_excel(path, index=False)
    return path


# ── Skill 元数据测试 ──────────────────────────────────────


class TestSkillMetadata:
    """Skill 模块约定测试。"""

    def test_skill_name_defined(self) -> None:
        assert isinstance(SKILL_NAME, str)
        assert len(SKILL_NAME) > 0

    def test_skill_description_defined(self) -> None:
        assert isinstance(SKILL_DESCRIPTION, str)
        assert len(SKILL_DESCRIPTION) > 0

    def test_get_tools_returns_one_tool(self) -> None:
        """get_tools() 应返回 1 个 ToolDef（create_chart）。"""
        tools = get_tools()
        assert len(tools) == 1
        assert all(isinstance(t, ToolDef) for t in tools)

    def test_tool_name_is_create_chart(self) -> None:
        names = {t.name for t in get_tools()}
        assert names == {"create_chart"}

    def test_tool_has_input_schema(self) -> None:
        tool = get_tools()[0]
        assert tool.input_schema.get("type") == "object"
        assert "properties" in tool.input_schema
        assert set(tool.input_schema["required"]) == {
            "file_path", "chart_type", "x_column", "y_column", "output_path"
        }

    def test_tool_func_callable(self) -> None:
        assert callable(get_tools()[0].func)


# ── create_chart 测试 ─────────────────────────────────────


class TestCreateChart:
    """create_chart 工具测试。"""

    @pytest.mark.parametrize("chart_type", ["bar", "line", "pie", "scatter", "radar"])
    def test_all_chart_types(self, sample_excel: Path, workspace: Path, chart_type: str) -> None:
        """五种图表类型均应成功生成图片文件。"""
        output = f"{chart_type}_chart.png"
        result = json.loads(
            create_chart("sales.xlsx", chart_type, "月份", "销售额", output)
        )
        assert result["status"] == "success"
        assert result["chart_type"] == chart_type
        assert (workspace / output).exists()
        # 文件大小应大于 0
        assert (workspace / output).stat().st_size > 0

    def test_unsupported_chart_type(self, sample_excel: Path) -> None:
        """不支持的图表类型应返回错误。"""
        result = json.loads(
            create_chart("sales.xlsx", "histogram", "月份", "销售额", "out.png")
        )
        assert result["status"] == "error"
        assert "不支持" in result["message"]

    def test_invalid_x_column(self, sample_excel: Path) -> None:
        """不存在的 x_column 应返回错误。"""
        result = json.loads(
            create_chart("sales.xlsx", "bar", "不存在", "销售额", "out.png")
        )
        assert result["status"] == "error"
        assert "不存在" in result["message"]

    def test_invalid_y_column(self, sample_excel: Path) -> None:
        """不存在的 y_column 应返回错误。"""
        result = json.loads(
            create_chart("sales.xlsx", "bar", "月份", "不存在列", "out.png")
        )
        assert result["status"] == "error"
        assert "不存在" in result["message"]

    def test_custom_title(self, sample_excel: Path, workspace: Path) -> None:
        """自定义标题应正常工作。"""
        result = json.loads(
            create_chart("sales.xlsx", "bar", "月份", "销售额", "titled.png", title="月度销售报告")
        )
        assert result["status"] == "success"
        assert (workspace / "titled.png").exists()

    def test_with_sheet_name(self, workspace: Path) -> None:
        """指定 sheet_name 应从对应工作表读取数据。"""
        df = pd.DataFrame({"类别": ["A", "B"], "值": [10, 20]})
        path = workspace / "multi_sheet.xlsx"
        with pd.ExcelWriter(path) as writer:
            df.to_excel(writer, sheet_name="数据表", index=False)

        result = json.loads(
            create_chart("multi_sheet.xlsx", "pie", "类别", "值", "pie.png", sheet_name="数据表")
        )
        assert result["status"] == "success"

    def test_radar_empty_data_returns_error(self, workspace: Path) -> None:
        """雷达图在无有效数据时应返回错误而非抛异常。"""
        path = workspace / "empty.xlsx"
        pd.DataFrame({"类别": [], "值": []}).to_excel(path, index=False)

        result = json.loads(
            create_chart("empty.xlsx", "radar", "类别", "值", "empty_radar.png")
        )
        assert result["status"] == "error"
        assert "没有可绘图的数据" in result["message"]
        assert not (workspace / "empty_radar.png").exists()

    def test_radar_requires_three_rows(self, workspace: Path) -> None:
        """雷达图至少需要 3 条数据。"""
        path = workspace / "two_rows.xlsx"
        pd.DataFrame({"类别": ["A", "B"], "值": [1, 2]}).to_excel(path, index=False)

        result = json.loads(
            create_chart("two_rows.xlsx", "radar", "类别", "值", "two_rows_radar.png")
        )
        assert result["status"] == "error"
        assert "至少需要 3 条有效数据" in result["message"]
        assert not (workspace / "two_rows_radar.png").exists()

    def test_path_traversal_input_rejected(self, workspace: Path) -> None:
        """输入文件路径穿越应被拒绝。"""
        with pytest.raises(SecurityViolationError, match="路径"):
            create_chart("../../etc/data.xlsx", "bar", "x", "y", "out.png")

    def test_path_traversal_output_rejected(self, sample_excel: Path) -> None:
        """输出文件路径穿越应被拒绝。"""
        with pytest.raises(SecurityViolationError, match="路径"):
            create_chart("sales.xlsx", "bar", "月份", "销售额", "../../../tmp/evil.png")
