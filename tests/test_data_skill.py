"""数据操作 Skill 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from excelmanus.security import SecurityViolationError
from excelmanus.skills import ToolDef
from excelmanus.skills.data_skill import (
    SKILL_DESCRIPTION,
    SKILL_NAME,
    analyze_data,
    filter_data,
    get_tools,
    init_guard,
    read_excel,
    transform_data,
    write_excel,
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
            "姓名": ["张三", "李四", "王五", "赵六"],
            "年龄": [25, 30, 35, 28],
            "城市": ["北京", "上海", "广州", "深圳"],
            "薪资": [10000, 15000, 12000, 18000],
        }
    )
    path = workspace / "test_data.xlsx"
    df.to_excel(path, index=False)
    return path


# ── Skill 元数据测试 ──────────────────────────────────────


class TestSkillMetadata:
    """Skill 模块约定测试。"""

    def test_skill_name_defined(self) -> None:
        """SKILL_NAME 应为非空字符串。"""
        assert isinstance(SKILL_NAME, str)
        assert len(SKILL_NAME) > 0

    def test_skill_description_defined(self) -> None:
        """SKILL_DESCRIPTION 应为非空字符串。"""
        assert isinstance(SKILL_DESCRIPTION, str)
        assert len(SKILL_DESCRIPTION) > 0

    def test_get_tools_returns_five_tools(self) -> None:
        """get_tools() 应返回 5 个 ToolDef 实例。"""
        tools = get_tools()
        assert len(tools) == 5
        assert all(isinstance(t, ToolDef) for t in tools)

    def test_tool_names(self) -> None:
        """工具名称应包含 read_excel/write_excel/analyze_data/filter_data/transform_data。"""
        names = {t.name for t in get_tools()}
        expected = {"read_excel", "write_excel", "analyze_data", "filter_data", "transform_data"}
        assert names == expected

    def test_all_tools_have_input_schema(self) -> None:
        """每个工具应有 input_schema 且包含 type=object。"""
        for tool in get_tools():
            assert tool.input_schema.get("type") == "object"
            assert "properties" in tool.input_schema

    def test_all_tools_have_callable_func(self) -> None:
        """每个工具的 func 应可调用。"""
        for tool in get_tools():
            assert callable(tool.func)



# ── read_excel 测试 ───────────────────────────────────────


class TestReadExcel:
    """read_excel 工具测试。"""

    def test_read_basic(self, sample_excel: Path) -> None:
        """读取 Excel 文件应返回正确的摘要信息。"""
        result = json.loads(read_excel("test_data.xlsx"))
        assert result["shape"]["rows"] == 4
        assert result["shape"]["columns"] == 4
        assert "姓名" in result["columns"]
        assert len(result["preview"]) == 4

    def test_read_with_max_rows(self, sample_excel: Path) -> None:
        """指定 max_rows 应限制读取行数。"""
        result = json.loads(read_excel("test_data.xlsx", max_rows=2))
        assert result["shape"]["rows"] == 2

    def test_read_path_traversal_rejected(self, workspace: Path) -> None:
        """路径穿越应被 FileAccessGuard 拒绝。"""
        with pytest.raises(SecurityViolationError, match="路径"):
            read_excel("../../etc/passwd")

    def test_read_absolute_outside_rejected(self, workspace: Path) -> None:
        """工作目录外的绝对路径应被拒绝。"""
        with pytest.raises(SecurityViolationError, match="路径"):
            read_excel("/tmp/evil.xlsx")


# ── write_excel 测试 ──────────────────────────────────────


class TestWriteExcel:
    """write_excel 工具测试。"""

    def test_write_and_read_back(self, workspace: Path) -> None:
        """写入数据后应能正确读回。"""
        data = [{"名称": "A", "数量": 10}, {"名称": "B", "数量": 20}]
        result = json.loads(write_excel("output.xlsx", data))
        assert result["status"] == "success"
        assert result["rows"] == 2

        # 读回验证
        df = pd.read_excel(workspace / "output.xlsx")
        assert len(df) == 2
        assert list(df.columns) == ["名称", "数量"]

    def test_write_with_sheet_name(self, workspace: Path) -> None:
        """指定 sheet_name 应写入对应工作表。"""
        data = [{"x": 1}]
        write_excel("sheet_test.xlsx", data, sheet_name="自定义")
        df = pd.read_excel(workspace / "sheet_test.xlsx", sheet_name="自定义")
        assert len(df) == 1

    def test_write_path_traversal_rejected(self, workspace: Path) -> None:
        """写入时路径穿越应被拒绝。"""
        with pytest.raises(SecurityViolationError, match="路径"):
            write_excel("../outside.xlsx", [{"a": 1}])


# ── analyze_data 测试 ─────────────────────────────────────


class TestAnalyzeData:
    """analyze_data 工具测试。"""

    def test_analyze_basic(self, sample_excel: Path) -> None:
        """分析应返回形状、列名和数值统计。"""
        result = json.loads(analyze_data("test_data.xlsx"))
        assert result["shape"]["rows"] == 4
        assert "numeric_stats" in result
        assert "年龄" in result["numeric_stats"]
        assert "薪资" in result["numeric_stats"]

    def test_analyze_missing_values(self, workspace: Path) -> None:
        """包含缺失值的数据应在结果中体现。"""
        df = pd.DataFrame({"a": [1, None, 3], "b": ["x", "y", None]})
        path = workspace / "missing.xlsx"
        df.to_excel(path, index=False)

        result = json.loads(analyze_data("missing.xlsx"))
        assert "a" in result["missing_values"] or "b" in result["missing_values"]


# ── filter_data 测试 ──────────────────────────────────────


class TestFilterData:
    """filter_data 工具测试。"""

    def test_filter_eq(self, sample_excel: Path) -> None:
        """eq 运算符应精确匹配。"""
        result = json.loads(filter_data("test_data.xlsx", "城市", "eq", "北京"))
        assert result["filtered_rows"] == 1
        assert result["preview"][0]["城市"] == "北京"

    def test_filter_gt(self, sample_excel: Path) -> None:
        """gt 运算符应过滤大于指定值的行。"""
        result = json.loads(filter_data("test_data.xlsx", "年龄", "gt", 28))
        assert result["filtered_rows"] == 2  # 30, 35

    def test_filter_contains(self, sample_excel: Path) -> None:
        """contains 运算符应支持字符串包含匹配。"""
        result = json.loads(filter_data("test_data.xlsx", "姓名", "contains", "三"))
        assert result["filtered_rows"] == 1

    def test_filter_invalid_column(self, sample_excel: Path) -> None:
        """不存在的列名应返回错误信息。"""
        result = json.loads(filter_data("test_data.xlsx", "不存在", "eq", 1))
        assert "error" in result

    def test_filter_invalid_operator(self, sample_excel: Path) -> None:
        """不支持的运算符应返回错误信息。"""
        result = json.loads(filter_data("test_data.xlsx", "年龄", "invalid", 1))
        assert "error" in result


# ── transform_data 测试 ───────────────────────────────────


class TestTransformData:
    """transform_data 工具测试。"""

    def test_rename_columns(self, sample_excel: Path, workspace: Path) -> None:
        """rename 操作应正确重命名列。"""
        ops = [{"type": "rename", "columns": {"姓名": "名字"}}]
        result = json.loads(transform_data("test_data.xlsx", ops))
        assert result["status"] == "success"

        df = pd.read_excel(workspace / "test_data.xlsx")
        assert "名字" in df.columns
        assert "姓名" not in df.columns

    def test_add_column(self, sample_excel: Path, workspace: Path) -> None:
        """add_column 操作应添加新列。"""
        ops = [{"type": "add_column", "name": "国家", "value": "中国"}]
        result = json.loads(transform_data("test_data.xlsx", ops))
        assert result["status"] == "success"

        df = pd.read_excel(workspace / "test_data.xlsx")
        assert "国家" in df.columns
        assert all(df["国家"] == "中国")

    def test_drop_columns(self, sample_excel: Path, workspace: Path) -> None:
        """drop_columns 操作应删除指定列。"""
        ops = [{"type": "drop_columns", "columns": ["城市"]}]
        result = json.loads(transform_data("test_data.xlsx", ops))
        assert result["status"] == "success"

        df = pd.read_excel(workspace / "test_data.xlsx")
        assert "城市" not in df.columns

    def test_sort(self, sample_excel: Path, workspace: Path) -> None:
        """sort 操作应按指定列排序。"""
        ops = [{"type": "sort", "by": "薪资", "ascending": False}]
        result = json.loads(transform_data("test_data.xlsx", ops))
        assert result["status"] == "success"

        df = pd.read_excel(workspace / "test_data.xlsx")
        assert df.iloc[0]["薪资"] == 18000

    def test_output_to_different_file(self, sample_excel: Path, workspace: Path) -> None:
        """指定 output_path 应写入不同文件。"""
        ops = [{"type": "add_column", "name": "标记", "value": "ok"}]
        transform_data("test_data.xlsx", ops, output_path="output.xlsx")

        assert (workspace / "output.xlsx").exists()
        df = pd.read_excel(workspace / "output.xlsx")
        assert "标记" in df.columns

    def test_multiple_operations(self, sample_excel: Path, workspace: Path) -> None:
        """多个操作应按顺序执行。"""
        ops = [
            {"type": "rename", "columns": {"姓名": "名字"}},
            {"type": "add_column", "name": "备注", "value": "无"},
            {"type": "sort", "by": "年龄", "ascending": True},
        ]
        result = json.loads(transform_data("test_data.xlsx", ops))
        assert result["status"] == "success"
        assert len(result["operations_applied"]) == 3
