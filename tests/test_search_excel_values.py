"""search_excel_values 工具单元测试。"""

import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from excelmanus.tools.data_tools import init_guard, search_excel_values


@pytest.fixture(autouse=True)
def _set_guard(tmp_path: Path):
    """每个测试前将 FileAccessGuard 指向 tmp_path。"""
    init_guard(str(tmp_path))


@pytest.fixture
def sample_xlsx(tmp_path: Path) -> Path:
    """创建一个包含多 Sheet 的测试 Excel 文件。"""
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "订单"
    ws1.append(["订单号", "客户", "金额"])
    ws1.append(["ORD-001", "张三丰", 100])
    ws1.append(["ORD-002", "李四光", 200])
    ws1.append(["ORD-003", "张三丰", 300])
    ws1.append(["ORD-004", "王五", 400])

    ws2 = wb.create_sheet("客户")
    ws2.append(["姓名", "城市", "电话"])
    ws2.append(["张三丰", "北京", "13800138000"])
    ws2.append(["李四光", "上海", "13900139000"])
    ws2.append(["王五", "深圳", "13700137000"])

    fp = tmp_path / "search_test.xlsx"
    wb.save(fp)
    return fp


class TestSearchExcelValuesContains:
    """contains 模式测试。"""

    def test_basic_search(self, sample_xlsx: Path) -> None:
        result = search_excel_values(file_path=str(sample_xlsx), query="张三丰")
        data = json.loads(result)
        assert data["total_matches"] >= 2  # 订单表2次 + 客户表1次
        assert len(data["matches"]) >= 2

    def test_cross_sheet_search(self, sample_xlsx: Path) -> None:
        data = json.loads(search_excel_values(file_path=str(sample_xlsx), query="张三丰"))
        sheets_found = {m["sheet"] for m in data["matches"]}
        assert "订单" in sheets_found
        assert "客户" in sheets_found

    def test_match_has_cell_ref(self, sample_xlsx: Path) -> None:
        data = json.loads(search_excel_values(file_path=str(sample_xlsx), query="ORD-001"))
        assert data["total_matches"] >= 1
        match = data["matches"][0]
        assert "cell_ref" in match
        assert "column" in match
        assert "row" in match

    def test_match_has_context(self, sample_xlsx: Path) -> None:
        data = json.loads(search_excel_values(file_path=str(sample_xlsx), query="ORD-001"))
        match = data["matches"][0]
        assert "context" in match
        assert isinstance(match["context"], dict)

    def test_summary_by_sheet(self, sample_xlsx: Path) -> None:
        data = json.loads(search_excel_values(file_path=str(sample_xlsx), query="张三丰"))
        assert "summary_by_sheet" in data
        assert len(data["summary_by_sheet"]) >= 1


class TestSearchExcelValuesExact:
    """exact 模式测试。"""

    def test_exact_match(self, sample_xlsx: Path) -> None:
        data = json.loads(search_excel_values(
            file_path=str(sample_xlsx), query="张三丰", match_mode="exact",
        ))
        assert data["total_matches"] >= 1
        for m in data["matches"]:
            assert m["value"] == "张三丰"

    def test_exact_no_partial(self, sample_xlsx: Path) -> None:
        data = json.loads(search_excel_values(
            file_path=str(sample_xlsx), query="张三", match_mode="exact",
        ))
        # "张三" 不等于 "张三丰"，应该没有精确匹配
        assert data["total_matches"] == 0


class TestSearchExcelValuesRegex:
    """regex 模式测试。"""

    def test_regex_pattern(self, sample_xlsx: Path) -> None:
        data = json.loads(search_excel_values(
            file_path=str(sample_xlsx), query=r"ORD-\d{3}", match_mode="regex",
        ))
        assert data["total_matches"] == 4  # ORD-001 到 ORD-004

    def test_invalid_regex(self, sample_xlsx: Path) -> None:
        data = json.loads(search_excel_values(
            file_path=str(sample_xlsx), query="[invalid", match_mode="regex",
        ))
        assert "error" in data


class TestSearchExcelValuesFilters:
    """过滤参数测试。"""

    def test_filter_by_sheets(self, sample_xlsx: Path) -> None:
        data = json.loads(search_excel_values(
            file_path=str(sample_xlsx), query="张三丰", sheets=["订单"],
        ))
        for m in data["matches"]:
            assert m["sheet"] == "订单"

    def test_filter_by_columns(self, sample_xlsx: Path) -> None:
        data = json.loads(search_excel_values(
            file_path=str(sample_xlsx), query="张三丰", columns=["客户"],
        ))
        for m in data["matches"]:
            assert m["column"] == "客户"

    def test_max_results(self, sample_xlsx: Path) -> None:
        data = json.loads(search_excel_values(
            file_path=str(sample_xlsx), query="ORD", max_results=2,
        ))
        assert len(data["matches"]) <= 2
        assert data.get("truncated") is True or data["total_matches"] <= 2

    def test_case_insensitive_default(self, sample_xlsx: Path) -> None:
        data = json.loads(search_excel_values(
            file_path=str(sample_xlsx), query="ord-001",
        ))
        assert data["total_matches"] >= 1

    def test_case_sensitive(self, sample_xlsx: Path) -> None:
        data = json.loads(search_excel_values(
            file_path=str(sample_xlsx), query="ord-001", case_sensitive=True,
        ))
        assert data["total_matches"] == 0


class TestSearchExcelValuesEdgeCases:
    """边界情况测试。"""

    def test_file_not_found(self, tmp_path: Path) -> None:
        result = search_excel_values(
            file_path=str(tmp_path / "nonexistent.xlsx"), query="test",
        )
        data = json.loads(result)
        assert "error" in data

    def test_no_matches(self, sample_xlsx: Path) -> None:
        data = json.loads(search_excel_values(
            file_path=str(sample_xlsx), query="不存在的值xyz",
        ))
        assert data["total_matches"] == 0
        assert len(data["matches"]) == 0

    def test_empty_query(self, sample_xlsx: Path) -> None:
        data = json.loads(search_excel_values(
            file_path=str(sample_xlsx), query="",
        ))
        # 空 query 应该返回错误或空结果
        assert data.get("total_matches", 0) == 0 or "error" in data
