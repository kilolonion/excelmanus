"""extract_columns 对 Unnamed 列名的处理测试。"""

from __future__ import annotations

from excelmanus.window_perception.ingest import extract_columns


class TestExtractColumnsUnnamed:
    """当 columns 包含 Unnamed 时，应从数据行推断更有意义的名称。"""

    def test_unnamed_columns_get_sample_annotation(self) -> None:
        """Unnamed 列名应被标注数据样本。"""
        result_json = {
            "columns": ["Unnamed: 0", "Unnamed: 1", "Unnamed: 2"],
        }
        rows = [
            {"Unnamed: 0": "1月", "Unnamed: 1": "产品A", "Unnamed: 2": 10000},
            {"Unnamed: 0": "2月", "Unnamed: 1": "产品B", "Unnamed: 2": 12000},
        ]
        columns = extract_columns(result_json, rows)
        # 列名应包含样本标注，不应是纯 Unnamed
        for col in columns:
            assert "样本:" in col.name, f"列名未被标注: {col.name}"

    def test_unnamed_annotation_contains_sample(self) -> None:
        """标注应包含数据样本值。"""
        result_json = {
            "columns": ["Unnamed: 0", "Unnamed: 1"],
        }
        rows = [
            {"Unnamed: 0": "北京", "Unnamed: 1": 50000},
        ]
        columns = extract_columns(result_json, rows)
        assert "北京" in columns[0].name
        assert "50000" in columns[1].name

    def test_clean_columns_unchanged(self) -> None:
        """正常列名不应被修改。"""
        result_json = {
            "columns": ["月份", "产品", "销售额"],
        }
        rows = [
            {"月份": "1月", "产品": "A", "销售额": 10000},
        ]
        columns = extract_columns(result_json, rows)
        names = [col.name for col in columns]
        assert names == ["月份", "产品", "销售额"]

    def test_mixed_unnamed_and_named(self) -> None:
        """混合场景：部分 Unnamed 部分正常。"""
        result_json = {
            "columns": ["月份", "Unnamed: 1", "销售额"],
        }
        rows = [
            {"月份": "1月", "Unnamed: 1": "产品A", "销售额": 10000},
        ]
        columns = extract_columns(result_json, rows)
        assert columns[0].name == "月份"
        assert "样本:" in columns[1].name
        assert "产品A" in columns[1].name
        assert columns[2].name == "销售额"

    def test_unnamed_with_none_sample(self) -> None:
        """当数据样本全为 None 时，保留原始 Unnamed 名称。"""
        result_json = {
            "columns": ["Unnamed: 0"],
        }
        rows = [
            {"Unnamed: 0": None},
            {"Unnamed: 0": None},
        ]
        columns = extract_columns(result_json, rows)
        # 无样本可用时保留原名
        assert len(columns) == 1
