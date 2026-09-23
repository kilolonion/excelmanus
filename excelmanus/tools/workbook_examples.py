"""Small executable examples, checked against the actual WorkbookSpec compiler."""
from __future__ import annotations


def workbook_creation_example() -> dict:
    return {
        "sheets": [{
            "name": "明细", "dimensions": {"rows": 4, "cols": 4},
            "value_blocks": [{"start": "A1", "values": [
                ["收据", None, None, None], ["项目", "数量", "单价", "金额"],
                ["示例", 2, 10, None], ["合计", None, None, None],
            ]}],
            "formula_blocks": [{"start": "D3", "formulas": [["=B3*C3"], ["=SUM(D3:D3)"]]}],
            "merged_ranges": ["A1:D1"],
            "styles": {"header": {"font": {"bold": True}, "alignment": {"horizontal": "center"}}},
            "style_regions": [{"range": "A1:D2", "style_id": "header"}],
            "column_widths": [24, 10, 12, 14], "row_heights": {"1": 26},
            "print_layout": {"print_area": "A1:D4", "fit_to_width": 1, "fit_to_height": 1},
        }],
        "uncertainties": [],
    }
