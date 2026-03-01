"""测试 compact tool results 优化：null-stripped records + null_info + compact separators。"""

from __future__ import annotations

import json
import math
from datetime import date, datetime

import pandas as pd
import pytest

from excelmanus.tools.data_tools import (
    _build_null_info,
    _df_to_compact_records,
    _trim_trailing_nulls_generic,
)


# ── _df_to_compact_records 基础测试 ──────────────────────────


class TestDfToCompactRecords:
    """测试 null-stripped compact records 转换。"""

    def test_dense_data_no_nulls(self):
        """密集数据（无 null）应保留所有键。"""
        df = pd.DataFrame({"A": [1, 2], "B": ["x", "y"]})
        records = _df_to_compact_records(df)
        assert len(records) == 2
        assert records[0] == {"A": 1, "B": "x"}
        assert records[1] == {"A": 2, "B": "y"}

    def test_sparse_data_strips_nulls(self):
        """稀疏数据应去除 null/NaN 键。"""
        df = pd.DataFrame({
            "姓名": ["张三", "李四"],
            "年龄": [25, None],
            "部门": ["技术", None],
        })
        records = _df_to_compact_records(df)
        assert records[0] == {"姓名": "张三", "年龄": 25.0, "部门": "技术"}
        assert records[1] == {"姓名": "李四"}
        assert "年龄" not in records[1]
        assert "部门" not in records[1]

    def test_all_null_row_becomes_empty_dict(self):
        """全空行应变为空字典，保留行位。"""
        df = pd.DataFrame({"A": [None], "B": [None]})
        records = _df_to_compact_records(df)
        assert len(records) == 1
        assert records[0] == {}

    def test_preserves_false_and_zero(self):
        """布尔 False 和数值 0 不应被剥离。"""
        df = pd.DataFrame({"flag": [False, True], "count": [0, 1]})
        records = _df_to_compact_records(df)
        assert records[0] == {"flag": False, "count": 0}
        assert records[1] == {"flag": True, "count": 1}

    def test_preserves_empty_string(self):
        """空字符串不应被剥离（pd.notna('') == True）。"""
        df = pd.DataFrame({"A": ["", "hello"]})
        records = _df_to_compact_records(df)
        assert records[0] == {"A": ""}
        assert records[1] == {"A": "hello"}

    def test_nan_stripped(self):
        """float NaN 应被剥离。"""
        df = pd.DataFrame({"A": [1.0, float("nan")], "B": [float("nan"), 2.0]})
        records = _df_to_compact_records(df)
        assert records[0] == {"A": 1.0}
        assert records[1] == {"B": 2.0}

    def test_datetime_serialized(self):
        """datetime 类型应序列化为 ISO 格式字符串。"""
        df = pd.DataFrame({
            "dt": [datetime(2024, 1, 15, 10, 30)],
            "d": [date(2024, 6, 1)],
        })
        records = _df_to_compact_records(df)
        assert records[0]["dt"] == "2024-01-15T10:30:00"
        assert records[0]["d"] == "2024-06-01"

    def test_column_names_as_strings(self):
        """数值列名应转为字符串键。"""
        df = pd.DataFrame({0: [10], 1: [20]})
        records = _df_to_compact_records(df)
        assert records[0] == {"0": 10, "1": 20}

    def test_token_savings_quantification(self):
        """量化验证：稀疏数据的 token 节省应 > 50%。"""
        data = []
        for i in range(10):
            data.append({
                "姓名": f"员工{i+1}",
                "年龄": 25 + i if i % 2 == 0 else None,
                "部门": "技术" if i < 5 else None,
                "电话": None,
                "工资": 10000 if i % 2 == 0 else None,
                "状态": "在职" if i < 7 else None,
                "编号": f"A{i+1:03d}",
            })
        df = pd.DataFrame(data)

        # 当前格式
        old = json.loads(df.to_json(orient="records", force_ascii=False))
        old_str = json.dumps(old, ensure_ascii=False, indent=2)

        # 新格式
        new = _df_to_compact_records(df)
        new_str = json.dumps(new, ensure_ascii=False, separators=(",", ":"))

        savings = 1 - len(new_str) / len(old_str)
        assert savings > 0.50, f"Expected >50% savings, got {savings:.0%}"


# ── _build_null_info 测试 ────────────────────────────────────


class TestBuildNullInfo:
    """测试空值摘要生成。"""

    def test_no_nulls_returns_none(self):
        """无显著空值时返回 None。"""
        df = pd.DataFrame({"A": [1, 2, 3], "B": [4, 5, 6]})
        assert _build_null_info(df) is None

    def test_all_null_column(self):
        """完全为空的列应出现在 '完全为空' 中。"""
        df = pd.DataFrame({"A": [1, 2], "B": [None, None]})
        info = _build_null_info(df)
        assert info is not None
        assert "B" in info["完全为空"]

    def test_high_null_rate_column(self):
        """高空值率（≥60%）的列。"""
        df = pd.DataFrame({"A": [1, 2, 3, 4, 5], "B": [None, None, None, 4, None]})
        info = _build_null_info(df)
        assert info is not None
        assert "B" in info.get("高空值率(≥60%)", [])

    def test_moderate_null_rate_excluded(self):
        """中等空值率（<60%）不应包含。"""
        df = pd.DataFrame({"A": [1, 2, 3, 4, 5], "B": [None, None, 3, 4, 5]})
        info = _build_null_info(df)
        # 40% null rate < 60% threshold
        assert info is None

    def test_empty_df(self):
        """空 DataFrame 返回 None。"""
        df = pd.DataFrame()
        assert _build_null_info(df) is None


# ── JSON 输出格式验证 ────────────────────────────────────────


class TestJsonOutputFormat:
    """验证整体 JSON 输出的紧凑性和正确性。"""

    def test_compact_records_are_valid_json(self):
        """compact records 序列化后应为合法 JSON。"""
        df = pd.DataFrame({"A": [1, None], "B": [None, "x"]})
        records = _df_to_compact_records(df)
        serialized = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
        parsed = json.loads(serialized)
        assert isinstance(parsed, list)
        assert len(parsed) == 2

    def test_no_null_in_output(self):
        """compact records 序列化后不应包含 'null' 字符串。"""
        df = pd.DataFrame({
            "A": [1, None, 3],
            "B": [None, "x", None],
            "C": [None, None, None],
        })
        records = _df_to_compact_records(df)
        serialized = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
        assert "null" not in serialized

    def test_compact_separators_no_whitespace(self):
        """compact separators 不应有多余空白。"""
        df = pd.DataFrame({"A": [1], "B": [2]})
        records = _df_to_compact_records(df)
        serialized = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
        assert " " not in serialized  # 无空白
        assert "\n" not in serialized  # 无换行


# ── _trim_trailing_nulls_generic 测试 ────────────────────────


class TestTrimTrailingNulls:
    """验证行尾 null 裁剪（用于 _read_range_direct）。"""

    def test_trailing_nulls_removed(self):
        row = [1, "x", None, None, None]
        assert _trim_trailing_nulls_generic(row) == [1, "x"]

    def test_middle_nulls_preserved(self):
        row = [1, None, "x", None, None]
        assert _trim_trailing_nulls_generic(row) == [1, None, "x"]

    def test_all_nulls(self):
        row = [None, None, None]
        assert _trim_trailing_nulls_generic(row) == []

    def test_no_nulls(self):
        row = [1, 2, 3]
        assert _trim_trailing_nulls_generic(row) == [1, 2, 3]

    def test_empty_row(self):
        assert _trim_trailing_nulls_generic([]) == []


# ── 合并单元格场景验证 ──────────────────────────────────────


class TestMergedCellScenario:
    """验证合并单元格导致的 NaN 在 compact records 中的行为。"""

    def test_merged_cell_nan_stripped(self):
        """合并单元格导致的 NaN（第2、3行缺失部门键）被正确剥离。"""
        df = pd.DataFrame({
            "部门": ["销售部", None, None],
            "姓名": ["张三", "李四", "王五"],
        })
        records = _df_to_compact_records(df)
        assert records[0] == {"部门": "销售部", "姓名": "张三"}
        assert records[1] == {"姓名": "李四"}  # 部门被剥离
        assert records[2] == {"姓名": "王五"}

    def test_columns_field_preserves_all_columns(self):
        """columns 字段应保留完整列名，即使某列全为 null。"""
        df = pd.DataFrame({"A": [1, 2], "B": [None, None], "C": [3, None]})
        columns = [str(c) for c in df.columns]
        assert columns == ["A", "B", "C"]
