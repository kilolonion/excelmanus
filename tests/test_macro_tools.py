"""Macro 工具单元测试：vlookup_write + computed_column + CowWriter。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest

from excelmanus.security.cow_writer import CowWriter
from excelmanus.security import FileAccessGuard
from excelmanus.tools import macro_tools


# ── fixtures ─────────────────────────────────────────────


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """创建临时工作区并初始化 guard。"""
    macro_tools.init_guard(str(tmp_path))
    return tmp_path


@pytest.fixture()
def sample_excel(workspace: Path) -> Path:
    """创建包含两个 sheet 的样本 Excel 文件。"""
    fp = workspace / "test.xlsx"
    src_df = pd.DataFrame({
        "产品ID": ["A001", "A001", "A002", "A003", "A003", "A003"],
        "金额": [100, 200, 300, 400, 500, 600],
        "数量": [1, 2, 3, 4, 5, 6],
    })
    tgt_df = pd.DataFrame({
        "产品ID": ["A001", "A002", "A003", "A004"],
        "产品名": ["苹果", "香蕉", "橙子", "葡萄"],
    })
    with pd.ExcelWriter(fp, engine="openpyxl") as w:
        src_df.to_excel(w, sheet_name="销售明细", index=False)
        tgt_df.to_excel(w, sheet_name="产品目录", index=False)
    return fp


# ── CowWriter 测试 ───────────────────────────────────────


class TestCowWriter:
    def test_resolve_normal_path(self, workspace: Path) -> None:
        guard = FileAccessGuard(str(workspace))
        writer = CowWriter(guard)
        (workspace / "a.xlsx").touch()
        resolved = writer.resolve("a.xlsx")
        assert resolved == workspace / "a.xlsx"
        assert writer.cow_mapping == {}

    def test_resolve_bench_protected_triggers_cow(self, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXCELMANUS_BENCH_PROTECTED_DIRS", "bench/external")
        protected_dir = workspace / "bench" / "external"
        protected_dir.mkdir(parents=True)
        src = protected_dir / "data.xlsx"
        src.write_bytes(b"fake excel")

        guard = FileAccessGuard(str(workspace))
        writer = CowWriter(guard)
        resolved = writer.resolve("bench/external/data.xlsx")

        assert resolved != src
        assert resolved.parent == workspace / "outputs"
        assert len(writer.cow_mapping) == 1

    def test_atomic_save_dataframe(self, workspace: Path, sample_excel: Path) -> None:
        guard = FileAccessGuard(str(workspace))
        writer = CowWriter(guard)
        df = pd.DataFrame({"X": [1, 2, 3]})
        writer.atomic_save_dataframe(df, sample_excel, "新表")
        # 验证新表写入成功且原有 sheet 保留
        xls = pd.ExcelFile(sample_excel)
        assert "新表" in xls.sheet_names
        assert "销售明细" in xls.sheet_names
        assert "产品目录" in xls.sheet_names


# ── vlookup_write 测试 ───────────────────────────────────


class TestVlookupWrite:
    def test_basic_first_match(self, sample_excel: Path) -> None:
        result = json.loads(macro_tools.vlookup_write(
            file_path=str(sample_excel),
            source_sheet="销售明细",
            source_key="产品ID",
            source_values="金额",
            target_sheet="产品目录",
            target_key="产品ID",
            output_columns="首笔金额",
        ))
        assert result["status"] == "success"
        assert result["details"]["matched_rows"] >= 3
        # 验证写回的数据
        df = pd.read_excel(sample_excel, sheet_name="产品目录")
        assert "首笔金额" in df.columns
        assert df.loc[df["产品ID"] == "A001", "首笔金额"].values[0] == 100

    def test_sum_aggregation(self, sample_excel: Path) -> None:
        result = json.loads(macro_tools.vlookup_write(
            file_path=str(sample_excel),
            source_sheet="销售明细",
            source_key="产品ID",
            source_values="金额",
            target_sheet="产品目录",
            target_key="产品ID",
            output_columns="总金额",
            agg_func="sum",
        ))
        assert result["status"] == "success"
        df = pd.read_excel(sample_excel, sheet_name="产品目录")
        assert df.loc[df["产品ID"] == "A001", "总金额"].values[0] == 300  # 100+200
        assert df.loc[df["产品ID"] == "A003", "总金额"].values[0] == 1500  # 400+500+600

    def test_multiple_value_columns(self, sample_excel: Path) -> None:
        result = json.loads(macro_tools.vlookup_write(
            file_path=str(sample_excel),
            source_sheet="销售明细",
            source_key="产品ID",
            source_values=["金额", "数量"],
            target_sheet="产品目录",
            target_key="产品ID",
            output_columns=["总销售额", "总数量"],
            agg_func="sum",
        ))
        assert result["status"] == "success"
        df = pd.read_excel(sample_excel, sheet_name="产品目录")
        assert "总销售额" in df.columns
        assert "总数量" in df.columns

    def test_unmatched_key_returns_nan(self, sample_excel: Path) -> None:
        result = json.loads(macro_tools.vlookup_write(
            file_path=str(sample_excel),
            source_sheet="销售明细",
            source_key="产品ID",
            source_values="金额",
            target_sheet="产品目录",
            target_key="产品ID",
        ))
        assert result["status"] == "success"
        # A004 在源表中不存在，应为 NaN
        assert result["details"]["null_counts"]["金额"] >= 1

    def test_invalid_column_returns_error(self, sample_excel: Path) -> None:
        result = json.loads(macro_tools.vlookup_write(
            file_path=str(sample_excel),
            source_sheet="销售明细",
            source_key="不存在的列",
            source_values="金额",
            target_sheet="产品目录",
            target_key="产品ID",
        ))
        assert result["status"] == "error"
        assert "available_columns" in result.get("details", {})

    def test_invalid_agg_func_returns_error(self, sample_excel: Path) -> None:
        result = json.loads(macro_tools.vlookup_write(
            file_path=str(sample_excel),
            source_sheet="销售明细",
            source_key="产品ID",
            source_values="金额",
            target_sheet="产品目录",
            target_key="产品ID",
            agg_func="invalid",
        ))
        assert result["status"] == "error"

    def test_preserves_other_sheets(self, sample_excel: Path) -> None:
        macro_tools.vlookup_write(
            file_path=str(sample_excel),
            source_sheet="销售明细",
            source_key="产品ID",
            source_values="金额",
            target_sheet="产品目录",
            target_key="产品ID",
        )
        xls = pd.ExcelFile(sample_excel)
        assert "销售明细" in xls.sheet_names


# ── computed_column 测试 ─────────────────────────────────


class TestComputedColumn:
    def test_simple_arithmetic(self, sample_excel: Path) -> None:
        result = json.loads(macro_tools.computed_column(
            file_path=str(sample_excel),
            sheet_name="销售明细",
            column_name="利润估算",
            expression="col('金额') * 0.3",
        ))
        assert result["status"] == "success"
        df = pd.read_excel(sample_excel, sheet_name="销售明细")
        assert "利润估算" in df.columns
        assert df["利润估算"].iloc[0] == pytest.approx(30.0)

    def test_column_reference(self, sample_excel: Path) -> None:
        result = json.loads(macro_tools.computed_column(
            file_path=str(sample_excel),
            sheet_name="销售明细",
            column_name="单价",
            expression="col('金额') / col('数量')",
        ))
        assert result["status"] == "success"
        df = pd.read_excel(sample_excel, sheet_name="销售明细")
        assert df["单价"].iloc[0] == pytest.approx(100.0)

    def test_where_expression(self, sample_excel: Path) -> None:
        result = json.loads(macro_tools.computed_column(
            file_path=str(sample_excel),
            sheet_name="销售明细",
            column_name="等级",
            expression="where(col('金额') > 300, '大单', '小单')",
        ))
        assert result["status"] == "success"
        df = pd.read_excel(sample_excel, sheet_name="销售明细")
        assert df.loc[df["金额"] == 100, "等级"].values[0] == "小单"
        assert df.loc[df["金额"] == 400, "等级"].values[0] == "大单"

    def test_invalid_column_returns_error(self, sample_excel: Path) -> None:
        result = json.loads(macro_tools.computed_column(
            file_path=str(sample_excel),
            sheet_name="销售明细",
            column_name="test",
            expression="col('不存在') * 2",
        ))
        assert result["status"] == "error"
        assert "不存在" in result["message"]

    def test_forbidden_import_returns_error(self, sample_excel: Path) -> None:
        result = json.loads(macro_tools.computed_column(
            file_path=str(sample_excel),
            sheet_name="销售明细",
            column_name="test",
            expression="__import__('os').listdir('.')",
        ))
        assert result["status"] == "error"

    def test_forbidden_attribute_returns_error(self, sample_excel: Path) -> None:
        result = json.loads(macro_tools.computed_column(
            file_path=str(sample_excel),
            sheet_name="销售明细",
            column_name="test",
            expression="col('金额').__class__",
        ))
        assert result["status"] == "error"
        assert "__class__" in result["message"]

    def test_output_type_number(self, sample_excel: Path) -> None:
        result = json.loads(macro_tools.computed_column(
            file_path=str(sample_excel),
            sheet_name="销售明细",
            column_name="金额x2",
            expression="col('金额') * 2",
            output_type="number",
        ))
        assert result["status"] == "success"

    def test_preserves_other_sheets(self, sample_excel: Path) -> None:
        macro_tools.computed_column(
            file_path=str(sample_excel),
            sheet_name="销售明细",
            column_name="test_col",
            expression="col('金额') + 1",
        )
        xls = pd.ExcelFile(sample_excel)
        assert "产品目录" in xls.sheet_names


# ── get_tools 注册测试 ───────────────────────────────────


class TestGetTools:
    def test_tool_count(self) -> None:
        tools = macro_tools.get_tools()
        assert len(tools) == 2

    def test_tool_names(self) -> None:
        names = {t.name for t in macro_tools.get_tools()}
        assert names == {"vlookup_write", "computed_column"}
