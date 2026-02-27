"""analyze_sheet_mapping 工具测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from excelmanus.tools import data_tools


@pytest.fixture(autouse=True)
def _init_guard(tmp_path: Path) -> None:
    data_tools.init_guard(str(tmp_path))


@pytest.fixture()
def mapping_excel(tmp_path: Path) -> Path:
    """构造一个产品映射歧义场景。"""
    wb = Workbook()

    ws_sales = wb.active
    ws_sales.title = "销售明细"
    ws_sales.append(["2025 销售数据", None, None])
    ws_sales.append(["生成时间", None, None])
    ws_sales.append(["产品", "实付金额(元)", "城市"])
    ws_sales.append(["笔记本电脑", 1000, "北京"])
    ws_sales.append(["智能手机", 800, "上海"])
    ws_sales.append(["交换机", 500, "广州"])

    ws_catalog = wb.create_sheet("产品目录")
    ws_catalog.append(["产品目录", None, None])
    ws_catalog.append(["产品名称", "类别", "品牌"])
    ws_catalog.append(["联想 笔记本电脑 标准版", "笔记本电脑", "联想"])
    ws_catalog.append(["华为 智能手机 标准版", "智能手机", "华为"])
    ws_catalog.append(["小米 路由器 标准版", "路由器", "小米"])

    fp = tmp_path / "mapping.xlsx"
    wb.save(fp)
    return fp


class TestAnalyzeSheetMapping:
    def test_choose_best_candidate(self, mapping_excel: Path) -> None:
        result = json.loads(data_tools.analyze_sheet_mapping(
            file_path=str(mapping_excel),
            left_sheet="销售明细",
            left_key="产品",
            right_sheet="产品目录",
            right_key_candidates=["产品名称", "类别"],
            left_header_row=2,
            right_header_row=1,
        ))
        assert "error" not in result
        assert result["best_candidate"] == "类别"
        assert result["best_left_coverage"] > 0

    def test_missing_candidates_returns_error(self, mapping_excel: Path) -> None:
        result = json.loads(data_tools.analyze_sheet_mapping(
            file_path=str(mapping_excel),
            left_sheet="销售明细",
            left_key="产品",
            right_sheet="产品目录",
            left_header_row=2,
            right_header_row=1,
        ))
        assert "error" in result
