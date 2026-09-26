"""CSV 观测的推断类型：数值列不再被报成纯文本列，既有字段保持不变。

背景：CSV 没有存储类型，``_observe_csv`` 过去把每个字段都报成
``{"t": "s", "v": "2.5", ...}``，模型因此写下 "All values are strings in CSV
(t='s')"，无法一眼判断哪些列能直接求和。本测试锁定修复后的契约：

- 单元格新增 ``inferred_type``（number/text/date），``t``/``v``/``raw_value``
  仍是原始文本（不改 t 的既有语义，避免破坏下游 openpyxl 适配器）；
- 区域新增 ``type_summary``：每列 header/inferred_type/numeric_ratio/空值计数，
  让模型一眼看出「这列是数值列」；
- 空字段不伪造单元格（保持 cells 稀疏语义），只在列级摘要里计数为 empty；
- CSV 的 geometry 面明确为 unsupported 并给出原因，与工具描述一致。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.tools.context import use_workspace
from excelmanus.tools.workbook_tools import get_tools, observe_spreadsheet
from excelmanus.workbook.observation import _csv_inferred_type, observation_schema

CSV_TEXT = (
    "广告投入(万元),销售额(万元),备注,日期\n"
    "2.5,18.3,,2024-01-05\n"
    "3.0,22.1,环比上升,2024-02-05\n"
    "3.5,24.8,,2024-03-05\n"
    "4.0,28.5,,\n"
)


@pytest.fixture
def csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "广告与销售数据.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")
    with use_workspace(tmp_path):
        yield path


def _observe(csv_file: Path, *, facets=("data", "geometry"), address: str = "A1:D5", **kwargs):
    result = observe_spreadsheet(
        file_path=csv_file.name,
        mode="range",
        range=address,
        facets=list(facets),
        **kwargs,
    )
    assert result.success, result.model_text
    return result.value


# ── 单元格级 inferred_type ─────────────────────────────────────


def test_numeric_cells_are_typed_number_without_rewriting_literals(csv_file: Path) -> None:
    region = _observe(csv_file)["regions"][0]
    numeric = region["cells"]["2,1"]
    assert numeric["inferred_type"] == "number"
    # 既有字段逐字不变：t 仍是字面文本，原值可回溯、可保前导零
    assert {key: numeric[key] for key in ("t", "v", "raw_value", "cached", "value_source")} == {
        "t": "s",
        "v": "2.5",
        "raw_value": "2.5",
        "cached": "yes",
        "value_source": "literal",
    }
    assert region["cells"]["3,2"]["inferred_type"] == "number"
    assert region["cells"]["1,1"]["inferred_type"] == "text"  # 表头
    assert region["cells"]["3,3"]["inferred_type"] == "text"  # 文本单元格
    assert region["cells"]["2,4"]["inferred_type"] == "date"


def test_all_legacy_cell_fields_are_still_present(csv_file: Path) -> None:
    region = _observe(csv_file)["regions"][0]
    for payload in region["cells"].values():
        assert set(payload) >= {
            "t",
            "v",
            "raw_value",
            "cached",
            "value_source",
        }


# ── 列级 type_summary ─────────────────────────────────────────


def test_type_summary_marks_numeric_columns(csv_file: Path) -> None:
    summary = _observe(csv_file)["regions"][0]["type_summary"]
    assert summary["applies_to"] == "A1:D5"
    assert summary["rows_observed"] == 5
    assert summary["header_row"] == 1
    columns = {column["column"]: column for column in summary["columns"]}
    assert columns["A"]["inferred_type"] == "number"
    assert columns["A"]["numeric_ratio"] == 1.0
    assert columns["A"]["header"] == "广告投入(万元)"
    assert columns["B"]["inferred_type"] == "number"
    assert columns["B"]["header"] == "销售额(万元)"
    assert columns["C"]["inferred_type"] == "text"
    assert columns["D"]["inferred_type"] == "date"
    assert "CSV" in summary["note"]


def test_blank_fields_stay_sparse_and_are_counted_as_empty(csv_file: Path) -> None:
    region = _observe(csv_file)["regions"][0]
    # 空字段不伪造单元格：cells 仍是稀疏的既有语义
    assert "2,3" not in region["cells"]
    assert "4,3" not in region["cells"]
    summary = region["type_summary"]
    columns = {column["column"]: column for column in summary["columns"]}
    assert columns["C"]["empty"] == 3
    assert columns["C"]["non_empty"] == 2
    assert columns["C"]["type_counts"]["empty"] == 3
    assert columns["D"]["empty"] == 1
    assert summary["empty_cells"] == 4


def test_default_window_does_not_invent_padding_columns(csv_file: Path) -> None:
    result = observe_spreadsheet(file_path=csv_file.name, facets=["data"])
    assert result.success, result.model_text
    region = result.value["regions"][0]
    summary = region["type_summary"]
    assert summary["applies_to"] == "A1:L20"  # 默认窗口仍然按契约展开
    assert [column["column"] for column in summary["columns"]] == ["A", "B", "C", "D"]
    assert summary["empty_cells"] == 4


def test_type_summary_is_absent_when_data_not_requested(csv_file: Path) -> None:
    region = _observe(csv_file, facets=("geometry",))["regions"][0]
    assert region["cells"] == {}
    assert "type_summary" not in region
    assert region["coverage"]["data"]["status"] == "not_requested"


def test_coverage_carries_column_digest(csv_file: Path) -> None:
    # coverage 在结果 spill 时原样保留：即使 region 明细被裁剪，类型线索也不丢
    value = _observe(csv_file)
    data = value["coverage"]["facets"]["data"]
    assert data["inferred_types"] is True
    digest = {column["column"]: column for column in data["columns"]}
    assert digest["A"]["inferred_type"] == "number"
    assert digest["A"]["numeric_ratio"] == 1.0
    assert digest["A"]["header"] == "广告投入(万元)"
    assert digest["D"]["inferred_type"] == "date"


def test_mixed_column_reports_numeric_ratio(tmp_path: Path) -> None:
    path = tmp_path / "mixed.csv"
    path.write_text("code,value\nA001,1\nA002,2\nA003,x\n", encoding="utf-8")
    with use_workspace(tmp_path):
        result = observe_spreadsheet(
            file_path=path.name, mode="range", range="A1:B4", facets=["data"]
        )
    assert result.success, result.model_text
    columns = {
        column["column"]: column
        for column in result.value["regions"][0]["type_summary"]["columns"]
    }
    # 混合列不硬报成 number，但 numeric_ratio 让模型知道一半可用
    assert columns["B"]["inferred_type"] == "mixed"
    assert columns["B"]["numeric_ratio"] == 0.5


def test_search_mode_also_states_csv_has_no_geometry(csv_file: Path) -> None:
    result = observe_spreadsheet(
        file_path=csv_file.name, mode="search", query="2.5", facets=["data", "geometry"]
    )
    assert result.success, result.model_text
    geometry = result.value["coverage"]["facets"]["geometry"]
    assert geometry["status"] == "unsupported"
    assert "CSV" in geometry["reason"]


# ── geometry 契约一致（CSV 无几何信息） ────────────────────────


def test_csv_geometry_is_explicitly_unsupported(csv_file: Path) -> None:
    value = _observe(csv_file, facets=("data", "geometry"))
    region = value["regions"][0]
    assert "geometry" not in region  # 不给假几何
    assert region["coverage"]["geometry"]["status"] == "unsupported"
    assert "CSV" in region["coverage"]["geometry"]["reason"]
    top = value["coverage"]["facets"]["geometry"]
    assert top["status"] == "unsupported"
    assert "CSV" in top["reason"]


def test_geometry_only_request_keeps_old_statuses(csv_file: Path) -> None:
    value = _observe(csv_file, facets=("geometry",))
    region = value["regions"][0]
    assert region["coverage"]["geometry"]["status"] == "unsupported"
    assert region["coverage"]["data"]["status"] == "not_requested"
    assert value["coverage"]["facets"]["data"]["status"] == "not_requested"


# ── 推断规则矩阵（与 analyze 的 numeric 判定保持一致） ─────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2.5", "number"),
        ("18.3", "number"),
        ("12.0", "number"),
        ("-3", "number"),
        ("+2.5", "number"),
        ("1,234.5", "number"),
        ("1e5", "number"),
        ("13800138000", "number"),  # 与 analyze 的 numeric 判定一致
        ("007", "number"),  # v/raw_value 仍保留 "007"，前导零不丢
        ("2024-01-05", "date"),
        ("2024/1/5", "date"),
        ("3/4/2024", "date"),
        ("2024-01-05 08:30:00", "date"),
        ("2024-02-30", "text"),  # 非法日历日
        ("2024-13-01", "text"),
        ("nan", "text"),
        ("inf", "text"),
        ("-", "text"),
        ("12%", "text"),
        ("¥1,000", "text"),
        ("2.5万", "text"),
        ("TRUE", "text"),
        ("广告投入(万元)", "text"),
        ("", "empty"),
        ("   ", "empty"),
    ],
)
def test_csv_inferred_type_matrix(raw: str, expected: str) -> None:
    assert _csv_inferred_type(raw) == expected


# ── 只读与契约可发现性 ────────────────────────────────────────


def test_csv_observation_is_read_only(csv_file: Path) -> None:
    before = csv_file.read_bytes()
    value = _observe(csv_file)
    assert csv_file.read_bytes() == before
    assert value["provenance"]["calculation"] == "not_recalculated"
    assert value["provenance"]["adapter"] == "csv"


def test_schema_documents_inferred_type_and_type_summary() -> None:
    region = observation_schema()["properties"]["regions"]["items"]["properties"]
    cell = region["cells"]["additionalProperties"]["properties"]
    assert cell["inferred_type"]["enum"] == ["number", "text", "date", "empty"]
    assert "type_summary" in region
    summary = region["type_summary"]["properties"]
    assert {"applies_to", "header_row", "empty_cells", "columns", "note"} <= set(summary)


def test_tool_descriptions_state_csv_has_no_geometry() -> None:
    from excelmanus.tools.policy import TOOL_SHORT_DESCRIPTIONS

    assert "CSV" in TOOL_SHORT_DESCRIPTIONS["observe_spreadsheet"]
    description = next(
        tool.description for tool in get_tools() if tool.name == "observe_spreadsheet"
    )
    assert "CSV" in description and "geometry" in description
