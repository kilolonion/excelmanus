"""Semantic comparison of serialized workbook values and styles."""
from __future__ import annotations
import datetime
from typing import Any

def _is_formula(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("=")

def _date_semantic_equal(a: Any, b: Any) -> bool:
    """date/datetime 语义等价：混比时要求 datetime 侧为午夜，避免吞掉时间差异。"""
    if isinstance(a, datetime.datetime) and isinstance(b, datetime.datetime):
        return a == b
    a_date = a.date() if isinstance(a, datetime.datetime) else a
    b_date = b.date() if isinstance(b, datetime.datetime) else b
    if a_date != b_date:
        return False
    for value in (a, b):
        if isinstance(value, datetime.datetime) and value.time() != datetime.time(0, 0):
            return False
    return True


def _str_date_equal(text: str, dt_value: Any) -> bool:
    """ISO 日期/时间字符串 vs 读回的日期值（序列化边界会把 date 降级为字符串）。"""
    s = text.strip()
    try:
        parsed: Any = (
            datetime.datetime.fromisoformat(s)
            if "T" in s or " " in s
            else datetime.date.fromisoformat(s)
        )
    except ValueError:
        return False
    return _date_semantic_equal(parsed, dt_value)


def _values_equal(expected: Any, actual: Any) -> bool:
    if expected is actual:
        return True
    if expected is None or actual is None:
        return expected is None and actual is None
    if _is_formula(expected) or _is_formula(actual):
        return str(expected).strip() == str(actual).strip()
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return float(expected) == float(actual)
    date_types = (datetime.date, datetime.datetime)
    if isinstance(expected, date_types) and isinstance(actual, date_types):
        return _date_semantic_equal(expected, actual)
    if isinstance(expected, str) and isinstance(actual, date_types):
        return _str_date_equal(expected, actual)
    if isinstance(actual, str) and isinstance(expected, date_types):
        return _str_date_equal(actual, expected)
    return expected == actual

