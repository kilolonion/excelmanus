"""read_excel / filter_data 增强功能测试。

覆盖：
- 自动 tail 预览（>20行时附加最后5行）
- range 精确读取
- CSV/TSV 支持
- offset 分页
- sample_rows 等距采样
- summary include 维度
- filter_data 新运算符（in/not_in/between/isnull/notnull/startswith/endswith）
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

from excelmanus.tools import data_tools


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture()
def sample_excel(tmp_path: Path) -> Path:
    """创建一个 30 行的 Excel 文件，用于测试 tail/offset/sample 等。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "数据"
    ws.append(["ID", "姓名", "城市", "金额", "备注"])
    cities = ["北京", "上海", "广州", "深圳", "杭州"]
    for i in range(1, 31):
        ws.append([
            i,
            f"用户{i}",
            cities[i % len(cities)],
            i * 100,
            None if i % 5 == 0 else f"备注{i}",
        ])
    fp = tmp_path / "sample.xlsx"
    wb.save(fp)
    return fp


@pytest.fixture()
def small_excel(tmp_path: Path) -> Path:
    """创建一个 5 行的小 Excel 文件，不触发 tail 预览。"""
    wb = Workbook()
    ws = wb.active
    ws.append(["名称", "值"])
    for i in range(1, 6):
        ws.append([f"项目{i}", i * 10])
    fp = tmp_path / "small.xlsx"
    wb.save(fp)
    return fp


@pytest.fixture()
def sample_csv(tmp_path: Path) -> Path:
    """创建一个 CSV 文件。"""
    fp = tmp_path / "data.csv"
    with open(fp, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "名称", "分数"])
        for i in range(1, 26):
            writer.writerow([i, f"学生{i}", 60 + i])
    return fp


@pytest.fixture()
def sample_tsv(tmp_path: Path) -> Path:
    """创建一个 TSV 文件。"""
    fp = tmp_path / "data.tsv"
    with open(fp, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["编号", "产品", "价格"])
        for i in range(1, 11):
            writer.writerow([i, f"产品{i}", i * 9.9])
    return fp


@pytest.fixture(autouse=True)
def _init_guard(tmp_path: Path) -> None:
    """初始化 data_tools 的文件访问 guard。"""
    data_tools.init_guard(str(tmp_path))


# ── 自动 tail 预览 ──────────────────────────────────────


class TestTailPreview:
    def test_tail_preview_shown_for_large_table(self, sample_excel: Path):
        """30 行表格应出现 tail_preview。"""
        result = json.loads(data_tools.read_excel(str(sample_excel)))
        assert "tail_preview" in result
        assert "tail_note" in result
        assert len(result["tail_preview"]) == 5
        # tail 应包含最后几行数据
        last_ids = [row["ID"] for row in result["tail_preview"]]
        assert 30 in last_ids

    def test_no_tail_preview_for_small_table(self, small_excel: Path):
        """5 行表格不应出现 tail_preview。"""
        result = json.loads(data_tools.read_excel(str(small_excel)))
        assert "tail_preview" not in result
        assert "tail_note" not in result


# ── range 精确读取 ───────────────────────────────────────


class TestRangeRead:
    def test_range_read_basic(self, sample_excel: Path):
        """读取指定范围返回正确数据。"""
        result = json.loads(data_tools.read_excel(str(sample_excel), range="A1:E3"))
        assert result["rows_count"] == 3
        assert result["columns_count"] == 5
        assert result["range"] == "A1:E3"
        # 第一行应是表头
        assert result["data"][0][0] == "ID"

    def test_range_read_middle(self, sample_excel: Path):
        """读取中间区域。"""
        result = json.loads(data_tools.read_excel(str(sample_excel), range="A15:C17"))
        assert result["rows_count"] == 3

    def test_range_not_supported_for_csv(self, sample_csv: Path):
        """CSV 文件不支持 range 参数。"""
        result = json.loads(data_tools.read_excel(str(sample_csv), range="A1:C5"))
        assert "error" in result


# ── CSV/TSV 支持 ─────────────────────────────────────────


class TestCSVSupport:
    def test_read_csv(self, sample_csv: Path):
        """读取 CSV 文件返回正确摘要。"""
        result = json.loads(data_tools.read_excel(str(sample_csv)))
        assert result["file"] == "data.csv"
        assert result["shape"]["rows"] == 25
        assert "ID" in result["columns"]
        assert len(result["preview"]) == 10
        # 25 行应有 tail_preview
        assert "tail_preview" in result

    def test_read_tsv(self, sample_tsv: Path):
        """读取 TSV 文件返回正确摘要。"""
        result = json.loads(data_tools.read_excel(str(sample_tsv)))
        assert result["file"] == "data.tsv"
        assert result["shape"]["rows"] == 10
        assert "编号" in result["columns"]

    def test_filter_csv(self, sample_csv: Path):
        """filter_data 应支持 CSV。"""
        result = json.loads(data_tools.filter_data(
            str(sample_csv), column="分数", operator="gt", value=80,
        ))
        assert result["filtered_rows"] > 0

    def test_csv_total_rows(self, sample_csv: Path):
        """CSV max_rows 限制时应报告总行数。"""
        result = json.loads(data_tools.read_excel(str(sample_csv), max_rows=5))
        assert result["shape"]["rows"] == 5
        assert result.get("total_rows_in_sheet") == 25


# ── offset 分页 ──────────────────────────────────────────


class TestOffset:
    def test_offset_basic(self, sample_excel: Path):
        """offset 跳过前 N 行。"""
        result = json.loads(data_tools.read_excel(str(sample_excel), offset=25, max_rows=5))
        assert result["shape"]["rows"] == 5
        # 第一行预览应是 ID=26 附近
        first_id = result["preview"][0]["ID"]
        assert first_id == 26

    def test_offset_without_max_rows(self, sample_excel: Path):
        """offset 不配合 max_rows 时读取剩余全部。"""
        result = json.loads(data_tools.read_excel(str(sample_excel), offset=28))
        assert result["shape"]["rows"] == 2  # 30 - 28 = 2

    def test_offset_csv(self, sample_csv: Path):
        """CSV 也支持 offset。"""
        result = json.loads(data_tools.read_excel(str(sample_csv), offset=20, max_rows=5))
        assert result["shape"]["rows"] == 5


# ── sample_rows 等距采样 ────────────────────────────────


class TestSampleRows:
    def test_sample_rows(self, sample_excel: Path):
        """sample_rows 返回等距采样数据。"""
        result = json.loads(data_tools.read_excel(str(sample_excel), sample_rows=5))
        assert "sample_preview" in result
        assert "sample_note" in result
        assert len(result["sample_preview"]) == 5

    def test_sample_rows_not_triggered_for_small_data(self, small_excel: Path):
        """当数据行数 <= sample_rows 时不产生采样。"""
        result = json.loads(data_tools.read_excel(str(small_excel), sample_rows=10))
        assert "sample_preview" not in result


# ── summary include 维度 ─────────────────────────────────


class TestSummaryDimension:
    def test_summary_basic(self, sample_excel: Path):
        """include=["summary"] 返回每列数据概要。"""
        result = json.loads(data_tools.read_excel(
            str(sample_excel), include=["summary"],
        ))
        assert "data_summary" in result
        summary = result["data_summary"]
        # 应包含所有列
        assert "ID" in summary
        assert "金额" in summary
        # 数值列有 min/max/mean
        assert "min" in summary["金额"]
        assert "max" in summary["金额"]
        assert "mean" in summary["金额"]
        # 分类列有 top_values
        assert "top_values" in summary["城市"]
        # 有空值的列 null_rate > 0
        assert summary["备注"]["null_rate"] > 0

    def test_summary_csv(self, sample_csv: Path):
        """CSV 也支持 summary 维度。"""
        result = json.loads(data_tools.read_excel(
            str(sample_csv), include=["summary"],
        ))
        assert "data_summary" in result


# ── filter_data 新运算符 ─────────────────────────────────


class TestFilterOperators:
    def test_in_operator(self, sample_excel: Path):
        """in 运算符：值在列表中。"""
        result = json.loads(data_tools.filter_data(
            str(sample_excel), column="城市", operator="in", value=["北京", "上海"],
        ))
        assert result["filtered_rows"] > 0
        for row in result["data"]:
            assert row["城市"] in ["北京", "上海"]

    def test_not_in_operator(self, sample_excel: Path):
        """not_in 运算符：值不在列表中。"""
        result = json.loads(data_tools.filter_data(
            str(sample_excel), column="城市", operator="not_in", value=["北京", "上海"],
        ))
        assert result["filtered_rows"] > 0
        for row in result["data"]:
            assert row["城市"] not in ["北京", "上海"]

    def test_between_operator(self, sample_excel: Path):
        """between 运算符：值在范围内。"""
        result = json.loads(data_tools.filter_data(
            str(sample_excel), column="金额", operator="between", value=[500, 1500],
        ))
        assert result["filtered_rows"] > 0
        for row in result["data"]:
            assert 500 <= row["金额"] <= 1500

    def test_isnull_operator(self, sample_excel: Path):
        """isnull 运算符：值为空。"""
        result = json.loads(data_tools.filter_data(
            str(sample_excel), column="备注", operator="isnull", value=None,
        ))
        assert result["filtered_rows"] > 0

    def test_notnull_operator(self, sample_excel: Path):
        """notnull 运算符：值非空。"""
        result = json.loads(data_tools.filter_data(
            str(sample_excel), column="备注", operator="notnull", value=None,
        ))
        assert result["filtered_rows"] > 0

    def test_startswith_operator(self, sample_excel: Path):
        """startswith 运算符。"""
        result = json.loads(data_tools.filter_data(
            str(sample_excel), column="姓名", operator="startswith", value="用户1",
        ))
        assert result["filtered_rows"] > 0
        for row in result["data"]:
            assert str(row["姓名"]).startswith("用户1")

    def test_endswith_operator(self, sample_excel: Path):
        """endswith 运算符。"""
        result = json.loads(data_tools.filter_data(
            str(sample_excel), column="姓名", operator="endswith", value="0",
        ))
        assert result["filtered_rows"] > 0
        for row in result["data"]:
            assert str(row["姓名"]).endswith("0")
