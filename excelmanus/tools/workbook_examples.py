"""Small executable examples, checked against the actual WorkbookSpec compiler."""
from __future__ import annotations


def workbook_creation_example() -> dict:
    return {
        "version": "2",
        "sheets": [{
            "name": "明细", "dimensions": {"rows": 4, "cols": 4},
            "value_blocks": [{"start": "A1", "values": [
                ["收据", None, None, None], ["项目", "数量", "单价", "金额"],
                ["示例", 2, 10, None], ["合计", None, None, None],
            ]}],
            "formula_blocks": [{"start": "D3", "formulas": [["=B3*C3"], ["=SUM(D3:D3)"]]}],
            "merged_ranges": [{"range": "A1:D1"}],
            "styles": {"header": {"font": {"bold": True}, "alignment": {"horizontal": "center"}}},
            "style_regions": [{"range": "A1:D2", "style_id": "header"}],
            "column_widths": [24, 10, 12, 14], "row_heights": {"1": 26},
            "print_layout": {"print_area": "A1:D4", "fit_to_width": 1, "fit_to_height": 1},
        }],
        "uncertainties": [],
    }


def report_creation_example() -> dict:
    """Small complete report: numeric data, formula KPIs and cross-sheet chart."""
    return {
        "version": "2", "purpose": "data", "uncertainties": [],
        "sheets": [
            {"name": "数据", "dimensions": {"rows": 3, "cols": 3},
             "value_blocks": [{"start": "A1", "values": [["月份", "2024", "2025"], ["1月", 100, 120], ["2月", 110, 132]]}],
             "objects": {"charts": [{"chart_type": "line", "data_range": "B1:C3", "categories_range": "A2:A3",
                                      "target_sheet": "看板", "target_cell": "A6", "title": "月度趋势", "width": 16, "height": 9}]}},
            {"name": "看板", "dimensions": {"rows": 28, "cols": 6},
             "value_blocks": [{"start": "A1", "values": [["年度销售与同比", None, None, None], ["2024", "2025", "同比", "增量"]]}],
             "formula_blocks": [{"start": "A3", "formulas": [["=SUM(数据!B2:B3)", "=SUM(数据!C2:C3)", '=IF(A3=0,"",B3/A3-1)', "=B3-A3"]]}],
             "merged_ranges": [{"range": "A1:F1"}], "column_widths": [15, 15, 15, 15, 15, 15],
             "styles": {"header": {"font": {"bold": True}, "fill": {"color": "DDEBF7"}}, "percent": {"number_format": "0.0%"}},
             "style_regions": [{"range": "A1:F2", "style_id": "header"}, {"range": "C3", "style_id": "percent"}],
             "print_layout": {"print_area": "A1:F28", "fit_to_width": 1, "fit_to_height": 1}},
        ],
    }


def visual_receipt_creation_example() -> dict:
    """Schema-valid skeleton for an image-backed receipt replica.

    The attachment id, pixel box, and edge arrays are deliberately marked as
    placeholders.  A caller must replace them with measurements from the
    actual image before execution; the example exists to show the complete
    shape and the formula/verification route without making up evidence.
    """
    spec = workbook_creation_example()
    spec["purpose"] = "visual_replica"
    sheet = spec["sheets"][0]
    sheet.pop("column_widths", None)
    sheet.pop("row_heights", None)
    sheet["layout_reference"] = {
        "attachment_id": "<observed-attachment-id>",
        "table_bbox_px": [0, 0, 100, 100],
        "column_edges": [0, 0.5, 0.75, 0.875, 1],
        "row_edges": [0, 0.25, 0.5, 0.75, 1],
        "target_width_px": 100,
    }
    return spec
