"""CSV 与 XLSX 全面对齐测试。

覆盖 7 个修复点：
- Gap 0: read_excel range 参数对 CSV 静默降级
- Gap 1: search_excel_values CSV 搜索
- Gap 2: write_excel CSV 写入
- Gap 3: transform_data CSV 输出
- Gap 4: inspect_excel_files CSV 文件发现
- Gap 5: _read_csv_df header 自动检测
- Gap 6: _scan_csv_snapshot header 检测
- Gap 7: read_excel include 维度 CSV 提示
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import pytest


# ── 测试基础设施 ──────────────────────────────────────────


@pytest.fixture()
def csv_dir(tmp_path: Path) -> Path:
    """创建包含测试 CSV 文件的临时目录。"""
    return tmp_path


def _write_csv(path: Path, rows: list[list[str]], sep: str = ",") -> Path:
    """写入 CSV 文件。"""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=sep)
        for row in rows:
            writer.writerow(row)
    return path


def _make_standard_csv(csv_dir: Path, name: str = "test.csv") -> Path:
    """创建标准测试 CSV（有 header + 数据行）。"""
    return _write_csv(
        csv_dir / name,
        [
            ["姓名", "年龄", "城市", "金额"],
            ["张三", "25", "北京", "1000"],
            ["李四", "30", "上海", "2000"],
            ["王五", "28", "广州", "3000"],
            ["赵六", "35", "深圳", "4000"],
            ["钱七", "22", "杭州", "5000"],
        ],
    )


def _make_tsv(csv_dir: Path) -> Path:
    """创建 TSV 文件。"""
    return _write_csv(
        csv_dir / "test.tsv",
        [
            ["product", "price", "quantity"],
            ["Apple", "5.0", "100"],
            ["Banana", "3.0", "200"],
        ],
        sep="\t",
    )


def _make_csv_with_title_row(csv_dir: Path) -> Path:
    """创建带标题行（非 header）的 CSV。"""
    return _write_csv(
        csv_dir / "title.csv",
        [
            ["2024年度销售汇总报表", "", "", ""],
            ["生成时间：2024-01-01", "", "", ""],
            ["产品", "销量", "金额", "利润"],
            ["产品A", "100", "5000", "1000"],
            ["产品B", "200", "8000", "2000"],
        ],
    )


# ── 导入被测模块 ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_guard(csv_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """统一 patch FileAccessGuard，使其以 csv_dir 为工作目录。"""
    from excelmanus.tools import data_tools

    class _FakeGuard:
        def __init__(self):
            self.workspace_root = csv_dir

        def resolve_and_validate(self, path: str) -> Path:
            p = Path(path)
            if p.is_absolute():
                return p
            return csv_dir / path

    monkeypatch.setattr(data_tools, "_get_guard", lambda: _FakeGuard())


# ══════════════════════════════════════════════════════════
# Gap 0: read_excel range 参数对 CSV 静默降级
# ══════════════════════════════════════════════════════════


class TestReadExcelRangeCsvFallback:
    """range 参数对 CSV 静默降级到 offset+max_rows 模式。"""

    def test_range_ignored_for_csv(self, csv_dir: Path):
        from excelmanus.tools.data_tools import read_excel

        fp = _make_standard_csv(csv_dir)
        result = json.loads(read_excel(str(fp), range="A1:D3"))
        assert "error" not in result
        assert result["shape"]["rows"] > 0

    def test_range_with_offset_and_max_rows(self, csv_dir: Path):
        from excelmanus.tools.data_tools import read_excel

        fp = _make_standard_csv(csv_dir)
        result = json.loads(read_excel(str(fp), range="A1:D10", max_rows=2, offset=1))
        assert "error" not in result
        assert result["shape"]["rows"] == 2


# ══════════════════════════════════════════════════════════
# Gap 1: search_excel_values CSV 搜索
# ══════════════════════════════════════════════════════════


class TestSearchExcelValuesCsv:
    """search_excel_values 对 CSV 文件的搜索支持。"""

    def test_basic_search(self, csv_dir: Path):
        from excelmanus.tools.data_tools import search_excel_values

        fp = _make_standard_csv(csv_dir)
        result = json.loads(search_excel_values(file_path=str(fp), query="张三"))
        assert result["total_matches"] >= 1
        assert result["sheets_searched"] == 1
        match = result["matches"][0]
        assert match["sheet"] == "Sheet1"
        assert match["value"] == "张三"
        assert "context" in match

    def test_contains_mode(self, csv_dir: Path):
        from excelmanus.tools.data_tools import search_excel_values

        fp = _make_standard_csv(csv_dir)
        result = json.loads(
            search_excel_values(file_path=str(fp), query="京", match_mode="contains")
        )
        assert result["total_matches"] >= 1

    def test_exact_mode(self, csv_dir: Path):
        from excelmanus.tools.data_tools import search_excel_values

        fp = _make_standard_csv(csv_dir)
        result = json.loads(
            search_excel_values(file_path=str(fp), query="北京", match_mode="exact")
        )
        assert result["total_matches"] == 1

    def test_column_filter(self, csv_dir: Path):
        from excelmanus.tools.data_tools import search_excel_values

        fp = _make_standard_csv(csv_dir)
        # 搜索 "25" 但限定在 "城市" 列 → 应该 0 结果
        result = json.loads(
            search_excel_values(file_path=str(fp), query="25", columns=["城市"])
        )
        assert result["total_matches"] == 0

    def test_cell_ref_format(self, csv_dir: Path):
        from excelmanus.tools.data_tools import search_excel_values

        fp = _make_standard_csv(csv_dir)
        result = json.loads(search_excel_values(file_path=str(fp), query="张三"))
        match = result["matches"][0]
        # 张三在第一数据行(row2)第一列(A)
        assert match["cell_ref"] == "A2"

    def test_no_crash_empty_csv(self, csv_dir: Path):
        from excelmanus.tools.data_tools import search_excel_values

        fp = _write_csv(csv_dir / "empty.csv", [["col1", "col2"]])
        result = json.loads(search_excel_values(file_path=str(fp), query="anything"))
        assert result["total_matches"] == 0


# ══════════════════════════════════════════════════════════
# Gap 2: write_excel CSV 写入
# ══════════════════════════════════════════════════════════


class TestWriteExcelCsv:
    """write_excel 对 CSV 文件的写入支持。"""

    def test_write_csv(self, csv_dir: Path):
        from excelmanus.tools.data_tools import write_excel

        fp = csv_dir / "output.csv"
        data = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        result = json.loads(write_excel(str(fp), data))
        assert result["status"] == "success"
        assert fp.exists()
        # 验证写入的是 CSV 格式
        df = pd.read_csv(fp)
        assert len(df) == 2
        assert list(df.columns) == ["name", "age"]

    def test_write_tsv(self, csv_dir: Path):
        from excelmanus.tools.data_tools import write_excel

        fp = csv_dir / "output.tsv"
        data = [{"x": 1, "y": 2}]
        result = json.loads(write_excel(str(fp), data))
        assert result["status"] == "success"
        # 验证 TSV 分隔
        df = pd.read_csv(fp, sep="\t")
        assert len(df) == 1

    def test_overwrite_csv(self, csv_dir: Path):
        from excelmanus.tools.data_tools import write_excel

        fp = _make_standard_csv(csv_dir, "overwrite.csv")
        data = [{"a": 1}]
        result = json.loads(write_excel(str(fp), data))
        assert result["status"] == "success"
        df = pd.read_csv(fp)
        assert list(df.columns) == ["a"]


# ══════════════════════════════════════════════════════════
# Gap 3: transform_data CSV 输出
# ══════════════════════════════════════════════════════════


class TestTransformDataCsv:
    """transform_data 对 CSV 文件的读写支持。"""

    def test_transform_csv_inplace(self, csv_dir: Path):
        from excelmanus.tools.data_tools import transform_data

        fp = _make_standard_csv(csv_dir, "transform.csv")
        ops = [{"type": "rename", "columns": {"姓名": "name"}}]
        result = json.loads(transform_data(str(fp), ops))
        assert result["status"] == "success"
        # 验证写回的仍然是 CSV
        df = pd.read_csv(fp)
        assert "name" in df.columns
        assert "姓名" not in df.columns

    def test_transform_csv_to_csv_output(self, csv_dir: Path):
        from excelmanus.tools.data_tools import transform_data

        fp = _make_standard_csv(csv_dir, "src.csv")
        out = csv_dir / "dst.csv"
        ops = [{"type": "sort", "by": "金额", "ascending": False}]
        result = json.loads(transform_data(str(fp), ops, output_path=str(out)))
        assert result["status"] == "success"
        assert out.exists()
        df = pd.read_csv(out)
        assert len(df) == 5

    def test_transform_tsv_inplace(self, csv_dir: Path):
        from excelmanus.tools.data_tools import transform_data

        fp = _make_tsv(csv_dir)
        ops = [{"type": "drop_columns", "columns": ["quantity"]}]
        result = json.loads(transform_data(str(fp), ops))
        assert result["status"] == "success"
        df = pd.read_csv(fp, sep="\t")
        assert "quantity" not in df.columns


# ══════════════════════════════════════════════════════════
# Gap 4: inspect_excel_files CSV 文件发现
# ══════════════════════════════════════════════════════════


class TestInspectExcelFilesCsv:
    """inspect_excel_files 发现并预览 CSV 文件。"""

    def test_discover_csv(self, csv_dir: Path):
        from excelmanus.tools.data_tools import inspect_excel_files

        _make_standard_csv(csv_dir)
        result = json.loads(inspect_excel_files(str(csv_dir)))
        assert result["excel_files_found"] >= 1
        csv_file = next(
            (f for f in result["files"] if f["file"].endswith(".csv")), None
        )
        assert csv_file is not None
        assert len(csv_file["sheets"]) == 1
        sheet = csv_file["sheets"][0]
        assert sheet["name"] == "Sheet1"
        assert "header" in sheet
        assert "preview" in sheet

    def test_discover_tsv(self, csv_dir: Path):
        from excelmanus.tools.data_tools import inspect_excel_files

        _make_tsv(csv_dir)
        result = json.loads(inspect_excel_files(str(csv_dir)))
        tsv_file = next(
            (f for f in result["files"] if f["file"].endswith(".tsv")), None
        )
        assert tsv_file is not None

    def test_csv_header_detection(self, csv_dir: Path):
        from excelmanus.tools.data_tools import inspect_excel_files

        _make_csv_with_title_row(csv_dir)
        result = json.loads(inspect_excel_files(str(csv_dir)))
        csv_file = next(
            (f for f in result["files"] if f["file"] == "title.csv"), None
        )
        assert csv_file is not None
        sheet = csv_file["sheets"][0]
        # header_row_hint 应 > 0（跳过标题行）
        assert sheet["header_row_hint"] >= 1


# ══════════════════════════════════════════════════════════
# Gap 5: _read_csv_df header 自动检测
# ══════════════════════════════════════════════════════════


class TestReadCsvDfHeaderDetection:
    """_read_csv_df 的 header 自动检测。"""

    def test_standard_csv_header_row_0(self, csv_dir: Path):
        from excelmanus.tools.data_tools import _read_csv_df

        fp = _make_standard_csv(csv_dir)
        df, header = _read_csv_df(fp)
        assert header == 0
        assert "姓名" in df.columns

    def test_csv_with_title_row_detects_header(self, csv_dir: Path):
        from excelmanus.tools.data_tools import _read_csv_df

        fp = _make_csv_with_title_row(csv_dir)
        df, header = _read_csv_df(fp)
        # 应检测到 header 在 row 2（0-indexed），跳过标题行
        assert header >= 1
        # 列名应该包含业务列
        col_names = [str(c) for c in df.columns]
        assert any("产品" in c for c in col_names) or any("销量" in c for c in col_names)

    def test_explicit_header_row_overrides(self, csv_dir: Path):
        from excelmanus.tools.data_tools import _read_csv_df

        fp = _make_csv_with_title_row(csv_dir)
        df, header = _read_csv_df(fp, header_row=2)
        assert header == 2
        assert "产品" in df.columns

    def test_detect_header_row_csv_function(self, csv_dir: Path):
        from excelmanus.tools.data_tools import _detect_header_row_csv

        fp = _make_csv_with_title_row(csv_dir)
        detected = _detect_header_row_csv(fp)
        assert detected is not None
        assert detected >= 1


# ══════════════════════════════════════════════════════════
# Gap 6: scan_excel_snapshot CSV header 检测
# ══════════════════════════════════════════════════════════


class TestScanCsvSnapshot:
    """scan_excel_snapshot CSV 路径的 header 检测集成。"""

    def test_csv_snapshot_basic(self, csv_dir: Path):
        from excelmanus.tools.data_tools import scan_excel_snapshot

        fp = _make_standard_csv(csv_dir)
        result = json.loads(scan_excel_snapshot(str(fp)))
        assert "error" not in result
        assert result["sheet_count"] == 1
        sheet = result["sheets"][0]
        assert sheet["name"] == "Sheet1"
        assert sheet["has_formulas"] is False
        assert sheet["has_merged_cells"] is False

    def test_csv_snapshot_header_row(self, csv_dir: Path):
        from excelmanus.tools.data_tools import scan_excel_snapshot

        fp = _make_csv_with_title_row(csv_dir)
        result = json.loads(scan_excel_snapshot(str(fp)))
        sheet = result["sheets"][0]
        assert sheet["header_row"] >= 1

    def test_csv_snapshot_quality_signals(self, csv_dir: Path):
        from excelmanus.tools.data_tools import scan_excel_snapshot

        fp = _make_standard_csv(csv_dir)
        result = json.loads(scan_excel_snapshot(str(fp)))
        assert "quality_signals" in result


# ══════════════════════════════════════════════════════════
# Gap 7: read_excel include 维度 CSV 提示
# ══════════════════════════════════════════════════════════


class TestReadExcelIncludeCsvHints:
    """CSV 文件请求 openpyxl-only 维度时返回提示。"""

    def test_csv_unsupported_dimensions_notice(self, csv_dir: Path):
        from excelmanus.tools.data_tools import read_excel

        fp = _make_standard_csv(csv_dir)
        result = json.loads(read_excel(str(fp), include=["styles", "charts"]))
        assert "csv_unsupported_dimensions" in result
        assert "styles" in result["csv_unsupported_dimensions"]
        assert "charts" in result["csv_unsupported_dimensions"]

    def test_csv_supported_dimensions_still_work(self, csv_dir: Path):
        from excelmanus.tools.data_tools import read_excel

        fp = _make_standard_csv(csv_dir)
        result = json.loads(read_excel(str(fp), include=["summary", "categorical_summary"]))
        assert "data_summary" in result
        assert "categorical_summary" in result
        assert "csv_unsupported_dimensions" not in result

    def test_csv_mixed_dimensions(self, csv_dir: Path):
        from excelmanus.tools.data_tools import read_excel

        fp = _make_standard_csv(csv_dir)
        result = json.loads(
            read_excel(str(fp), include=["summary", "styles", "formulas"])
        )
        assert "data_summary" in result
        assert "csv_unsupported_dimensions" in result
        assert "styles" in result["csv_unsupported_dimensions"]
        assert "formulas" in result["csv_unsupported_dimensions"]


# ══════════════════════════════════════════════════════════
# 回归测试：确保 Excel 路径未受影响
# ══════════════════════════════════════════════════════════


class TestExcelNotBroken:
    """确保 CSV 改动不影响 Excel 文件处理。"""

    def test_read_excel_xlsx(self, csv_dir: Path):
        from excelmanus.tools.data_tools import read_excel

        fp = csv_dir / "test.xlsx"
        df = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
        df.to_excel(fp, index=False)
        result = json.loads(read_excel(str(fp)))
        assert result["shape"]["rows"] == 2

    def test_write_excel_xlsx(self, csv_dir: Path):
        from excelmanus.tools.data_tools import write_excel

        fp = csv_dir / "output.xlsx"
        data = [{"x": 1}]
        result = json.loads(write_excel(str(fp), data))
        assert result["status"] == "success"
        df = pd.read_excel(fp)
        assert len(df) == 1

    def test_search_xlsx(self, csv_dir: Path):
        from excelmanus.tools.data_tools import search_excel_values

        fp = csv_dir / "search.xlsx"
        df = pd.DataFrame({"name": ["Alice", "Bob"], "age": [30, 25]})
        df.to_excel(fp, index=False)
        result = json.loads(search_excel_values(file_path=str(fp), query="Alice"))
        assert result["total_matches"] >= 1
