"""独立公式模式检测器——在 Phase 2 数据填充后运行。

检测策略：
1. SUM 合计行：total_row 中的数值 ≈ 上方同列数据行之和 → =SUM(...)
2. 列间算术：某列所有数据行的值 = colA op colB → 推断公式模式
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from excelmanus.replica_spec import (
    CellSpec,
    FormulaPattern,
    ReplicaSpec,
    SheetSpec,
)

logger = logging.getLogger(__name__)

# 浮点比较容差（相对误差 0.5% 或绝对误差 0.01）
_REL_TOL = 0.005
_ABS_TOL = 0.01

_COL_RE = re.compile(r"^([A-Z]{1,3})(\d+)$", re.IGNORECASE)


def _parse_addr(addr: str) -> tuple[str, int] | None:
    """解析 Excel 地址为 (列字母, 行号)。"""
    m = _COL_RE.match(addr.strip())
    if not m:
        return None
    return m.group(1).upper(), int(m.group(2))


def _numeric_value(cell: CellSpec) -> float | None:
    """安全提取数值。"""
    if cell.value is None:
        return None
    if cell.value_type not in ("number", "formula"):
        return None
    try:
        return float(cell.value)
    except (ValueError, TypeError):
        return None


def detect_formulas(spec: ReplicaSpec) -> ReplicaSpec:
    """对 spec 中每个 sheet 执行公式模式检测，就地丰富 formula_candidate。

    返回同一 spec 实例（就地修改）。
    """
    for sheet in spec.sheets:
        _detect_sum_totals(sheet)
        _detect_average_totals(sheet)
        _detect_percentage_formulas(sheet)
        _detect_column_arithmetic(sheet)
    return spec


# ── SUM 合计行检测 ──────────────────────────────────────────────


def _detect_sum_totals(sheet: SheetSpec) -> None:
    """检测 total_rows 中是否存在 SUM 公式。"""
    total_rows = set(sheet.semantic_hints.total_rows)
    if not total_rows:
        return

    # 按 (列, 行) 索引 cells
    cell_map: dict[tuple[str, int], CellSpec] = {}
    for c in sheet.cells:
        parsed = _parse_addr(c.address)
        if parsed:
            cell_map[(parsed[0], parsed[1])] = c

    # 收集所有列
    cols: set[str] = set()
    for c in sheet.cells:
        parsed = _parse_addr(c.address)
        if parsed:
            cols.add(parsed[0])

    header_rows = set(sheet.semantic_hints.header_rows)
    detected_patterns: list[FormulaPattern] = []

    for col in sorted(cols):
        # 找该列中 total_row 的 cell
        for tr in sorted(total_rows):
            total_cell = cell_map.get((col, tr))
            if total_cell is None:
                continue
            total_val = _numeric_value(total_cell)
            if total_val is None:
                continue
            # 已有 formula_candidate 且置信度足够 → 跳过
            if total_cell.formula_candidate and total_cell.confidence >= 0.8:
                continue

            # 收集上方数据行（非 header、非 total）的数值
            data_rows: list[int] = []
            data_sum = 0.0
            for row_num in range(1, tr):
                if row_num in header_rows or row_num in total_rows:
                    continue
                dc = cell_map.get((col, row_num))
                if dc is None:
                    continue
                dv = _numeric_value(dc)
                if dv is not None:
                    data_rows.append(row_num)
                    data_sum += dv

            if len(data_rows) < 2:
                continue

            # 比较
            if math.isclose(total_val, data_sum, rel_tol=_REL_TOL, abs_tol=_ABS_TOL):
                first_row = min(data_rows)
                last_row = max(data_rows)
                formula = f"=SUM({col}{first_row}:{col}{last_row})"
                total_cell.formula_candidate = formula
                total_cell.confidence = max(total_cell.confidence, 0.85)
                detected_patterns.append(FormulaPattern(
                    column=col, pattern=f"SUM({col}<data>)", confidence=0.85,
                ))
                logger.debug(
                    "公式检测: %s%d → %s (sum=%.2f, actual=%.2f)",
                    col, tr, formula, data_sum, total_val,
                )

    if detected_patterns:
        sheet.semantic_hints.formula_patterns.extend(detected_patterns)


# ── AVERAGE 合计行检测 ────────────────────────────────────────


def _detect_average_totals(sheet: SheetSpec) -> None:
    """检测 total_rows 中是否存在 AVERAGE 公式。"""
    total_rows = set(sheet.semantic_hints.total_rows)
    if not total_rows:
        return

    cell_map: dict[tuple[str, int], CellSpec] = {}
    for c in sheet.cells:
        parsed = _parse_addr(c.address)
        if parsed:
            cell_map[(parsed[0], parsed[1])] = c

    cols: set[str] = set()
    for c in sheet.cells:
        parsed = _parse_addr(c.address)
        if parsed:
            cols.add(parsed[0])

    header_rows = set(sheet.semantic_hints.header_rows)
    detected_patterns: list[FormulaPattern] = []

    for col in sorted(cols):
        for tr in sorted(total_rows):
            total_cell = cell_map.get((col, tr))
            if total_cell is None:
                continue
            # 已有 formula_candidate → 跳过（SUM 优先）
            if total_cell.formula_candidate:
                continue
            total_val = _numeric_value(total_cell)
            if total_val is None:
                continue

            data_vals: list[float] = []
            data_rows: list[int] = []
            for row_num in range(1, tr):
                if row_num in header_rows or row_num in total_rows:
                    continue
                dc = cell_map.get((col, row_num))
                if dc is None:
                    continue
                dv = _numeric_value(dc)
                if dv is not None:
                    data_vals.append(dv)
                    data_rows.append(row_num)

            if len(data_vals) < 2:
                continue

            avg = sum(data_vals) / len(data_vals)
            if math.isclose(total_val, avg, rel_tol=_REL_TOL, abs_tol=_ABS_TOL):
                first_row = min(data_rows)
                last_row = max(data_rows)
                formula = f"=AVERAGE({col}{first_row}:{col}{last_row})"
                total_cell.formula_candidate = formula
                total_cell.confidence = max(total_cell.confidence, 0.80)
                detected_patterns.append(FormulaPattern(
                    column=col, pattern=f"AVERAGE({col}<data>)", confidence=0.80,
                ))
                logger.debug(
                    "公式检测: %s%d → %s (avg=%.2f, actual=%.2f)",
                    col, tr, formula, avg, total_val,
                )

    if detected_patterns:
        sheet.semantic_hints.formula_patterns.extend(detected_patterns)


# ── 百分比公式检测 ────────────────────────────────────────────


def _detect_percentage_formulas(sheet: SheetSpec) -> None:
    """检测列间百分比关系：colC = colA / colB（结果在 0~1 或 0~100 范围）。"""
    header_rows = set(sheet.semantic_hints.header_rows)
    total_rows = set(sheet.semantic_hints.total_rows)

    cell_map: dict[tuple[str, int], CellSpec] = {}
    cols_with_nums: dict[str, list[int]] = {}
    for c in sheet.cells:
        parsed = _parse_addr(c.address)
        if not parsed:
            continue
        col, row = parsed
        cell_map[(col, row)] = c
        if row not in header_rows and row not in total_rows:
            if _numeric_value(c) is not None:
                cols_with_nums.setdefault(col, []).append(row)

    num_cols = [c for c, rows in cols_with_nums.items() if len(rows) >= 3]
    if len(num_cols) < 2:
        return

    common_rows = set(cols_with_nums[num_cols[0]])
    for c in num_cols[1:]:
        common_rows &= set(cols_with_nums[c])
    common_rows_sorted = sorted(common_rows)
    if len(common_rows_sorted) < 3:
        return

    detected_patterns: list[FormulaPattern] = []

    for target_col in num_cols:
        # 检查 target 列的值是否看起来像百分比（0~1 或 0~100）
        target_vals = [
            _numeric_value(cell_map[(target_col, r)])
            for r in common_rows_sorted
        ]
        if any(v is None for v in target_vals):
            continue
        # 至少一半的值在 0~1 范围或在 0~100 范围
        ratio_01 = sum(1 for v in target_vals if 0 <= v <= 1)
        ratio_100 = sum(1 for v in target_vals if 0 <= v <= 100)
        is_pct_like = (ratio_01 >= len(target_vals) * 0.8) or (ratio_100 >= len(target_vals) * 0.8)
        if not is_pct_like:
            continue

        # 已有 formula_candidate → 跳过
        existing_fc = sum(
            1 for r in common_rows_sorted
            if cell_map.get((target_col, r))
            and cell_map[(target_col, r)].formula_candidate
        )
        if existing_fc > len(common_rows_sorted) * 0.5:
            continue

        for src_a in num_cols:
            if src_a == target_col:
                continue
            for src_b in num_cols:
                if src_b == target_col or src_b == src_a:
                    continue
                match_count = 0
                for r in common_rows_sorted:
                    va = _numeric_value(cell_map[(src_a, r)])
                    vb = _numeric_value(cell_map[(src_b, r)])
                    vt = _numeric_value(cell_map[(target_col, r)])
                    if va is None or vb is None or vt is None or vb == 0:
                        continue
                    expected = va / vb
                    if math.isclose(vt, expected, rel_tol=_REL_TOL, abs_tol=_ABS_TOL):
                        match_count += 1
                    elif math.isclose(vt, expected * 100, rel_tol=_REL_TOL, abs_tol=_ABS_TOL):
                        match_count += 1

                if match_count >= len(common_rows_sorted) * 0.8 and match_count >= 3:
                    confidence = min(0.75, match_count / len(common_rows_sorted))
                    for r in common_rows_sorted:
                        tc = cell_map.get((target_col, r))
                        if tc and not tc.formula_candidate:
                            tc.formula_candidate = f"={src_a}{r}/{src_b}{r}"
                            tc.confidence = max(tc.confidence, confidence)
                    detected_patterns.append(FormulaPattern(
                        column=target_col,
                        pattern=f"{target_col}={{row}}={src_a}{{row}}/{src_b}{{row}}",
                        confidence=confidence,
                    ))
                    logger.debug(
                        "公式检测(百分比): %s = %s / %s (匹配 %d/%d 行)",
                        target_col, src_a, src_b,
                        match_count, len(common_rows_sorted),
                    )
                    break
            else:
                continue
            break

    if detected_patterns:
        sheet.semantic_hints.formula_patterns.extend(detected_patterns)


# ── 列间算术检测 ─────────────────────────────────────────────


_OPS: list[tuple[str, Any]] = [
    ("+", lambda a, b: a + b),
    ("-", lambda a, b: a - b),
    ("*", lambda a, b: a * b),
    ("/", lambda a, b: a / b if b != 0 else None),
]


def _detect_column_arithmetic(sheet: SheetSpec) -> None:
    """检测列间算术关系：colC = colA op colB。"""
    header_rows = set(sheet.semantic_hints.header_rows)
    total_rows = set(sheet.semantic_hints.total_rows)

    # 按 (列, 行) 索引
    cell_map: dict[tuple[str, int], CellSpec] = {}
    cols_with_nums: dict[str, list[int]] = {}  # 列 → 有数值的数据行号列表
    for c in sheet.cells:
        parsed = _parse_addr(c.address)
        if not parsed:
            continue
        col, row = parsed
        cell_map[(col, row)] = c
        if row not in header_rows and row not in total_rows:
            if _numeric_value(c) is not None:
                cols_with_nums.setdefault(col, []).append(row)

    # 至少 3 个数值列才有意义
    num_cols = [c for c, rows in cols_with_nums.items() if len(rows) >= 3]
    if len(num_cols) < 3:
        return

    # 取所有数值列共同的数据行
    common_rows = set(cols_with_nums[num_cols[0]])
    for c in num_cols[1:]:
        common_rows &= set(cols_with_nums[c])
    common_rows_sorted = sorted(common_rows)
    if len(common_rows_sorted) < 3:
        return

    detected_patterns: list[FormulaPattern] = []

    # 尝试每对 (colA, colB) 对每个 target colC
    for target_col in num_cols:
        # 已有大量 formula_candidate → 跳过
        existing_fc = sum(
            1 for r in common_rows_sorted
            if cell_map.get((target_col, r))
            and cell_map[(target_col, r)].formula_candidate
        )
        if existing_fc > len(common_rows_sorted) * 0.5:
            continue

        for src_a in num_cols:
            if src_a == target_col:
                continue
            for src_b in num_cols:
                if src_b == target_col or src_b <= src_a:
                    continue
                for op_sym, op_fn in _OPS:
                    match_count = 0
                    for r in common_rows_sorted:
                        va = _numeric_value(cell_map[(src_a, r)])
                        vb = _numeric_value(cell_map[(src_b, r)])
                        vt = _numeric_value(cell_map[(target_col, r)])
                        if va is None or vb is None or vt is None:
                            continue
                        expected = op_fn(va, vb)
                        if expected is None:
                            continue
                        if math.isclose(vt, expected, rel_tol=_REL_TOL, abs_tol=_ABS_TOL):
                            match_count += 1
                    # 需要 ≥80% 的公共行匹配
                    if match_count >= len(common_rows_sorted) * 0.8 and match_count >= 3:
                        pattern_str = f"{target_col}={{row}}={src_a}{{row}}{op_sym}{src_b}{{row}}"
                        confidence = min(0.75, match_count / len(common_rows_sorted))
                        # 填充 formula_candidate
                        for r in common_rows_sorted:
                            tc = cell_map.get((target_col, r))
                            if tc and not tc.formula_candidate:
                                tc.formula_candidate = f"={src_a}{r}{op_sym}{src_b}{r}"
                                tc.confidence = max(tc.confidence, confidence)
                        detected_patterns.append(FormulaPattern(
                            column=target_col,
                            pattern=pattern_str,
                            confidence=confidence,
                        ))
                        logger.debug(
                            "公式检测: %s = %s %s %s (匹配 %d/%d 行)",
                            target_col, src_a, op_sym, src_b,
                            match_count, len(common_rows_sorted),
                        )
                        # 对该 target_col 只取第一个匹配的运算
                        break
                else:
                    continue
                break
            else:
                continue
            break

    if detected_patterns:
        sheet.semantic_hints.formula_patterns.extend(detected_patterns)
