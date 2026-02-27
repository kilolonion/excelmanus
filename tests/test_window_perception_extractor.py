"""window_perception.extractor 单元测试。"""

from excelmanus.window_perception.extractor import extract_shape


def test_extract_shape_fallbacks_to_first_sheet_shape() -> None:
    payload = {
        "sheets": [
            {"name": "Sheet1", "rows": 23, "columns": 5},
        ]
    }

    assert extract_shape(payload) == (23, 5)
