"""数据工具：提供 Excel 读写、分析、过滤和转换能力。"""

from __future__ import annotations

import functools
import json
import shutil
from datetime import date, datetime
from typing import Any

import pandas as pd

from excelmanus.logger import get_logger
from excelmanus.security import FileAccessGuard
from excelmanus.tools._helpers import get_worksheet, resolve_sheet_name
from excelmanus.tools.registry import ToolDef

logger = get_logger("tools.data")

# ── 表头识别配置 ──────────────────────────────────────────

_HEADER_MIN_NON_EMPTY = 3
_HEADER_SCAN_ROWS = 30
_HEADER_SCAN_COLS = 200
_HEADER_KEYWORDS = (
    "月份",
    "日期",
    "时间",
    "城市",
    "地区",
    "产品",
    "部门",
    "姓名",
    "工号",
    "编号",
    "金额",
    "数量",
    "状态",
    "营收",
    "利润",
    "成本",
)
_TITLE_HINT_PREFIXES = (
    "生成时间",
    "汇总",
    "分析",
    "报表",
    "仪表盘",
    "机密",
)

# ── Skill 元数据 ──────────────────────────────────────────

SKILL_NAME = "data"
SKILL_DESCRIPTION = "Excel 数据操作工具集：读取、写入、分析、过滤和转换"

# ── 模块级 FileAccessGuard（延迟初始化） ─────────────────

_guard: FileAccessGuard | None = None


def _get_guard() -> FileAccessGuard:
    """获取或创建 FileAccessGuard 单例。"""
    global _guard
    if _guard is None:
        # 默认使用当前工作目录，可通过 init_guard() 覆盖
        _guard = FileAccessGuard(".")
    return _guard


def build_completeness_meta(
    total_available: int,
    returned: int,
    *,
    entity_name: str = "行",
) -> dict[str, Any]:
    """构建数据完整性元数据，供工具统一使用。

    当 returned < total_available 时，附加截断标记和自然语言提示，
    帮助 LLM 正确理解数据范围，避免将预览数据误判为全量数据。
    """
    meta: dict[str, Any] = {
        "total_available": total_available,
        "returned": returned,
    }
    if returned < total_available:
        meta["is_truncated"] = True
        meta["truncation_note"] = (
            f"⚠️ 仅返回 {returned} {entity_name}（共 {total_available} {entity_name}）。"
            f"如需操作全量数据，请注意实际数据范围。"
        )
    return meta


def init_guard(workspace_root: str) -> None:
    """初始化文件访问守卫（供外部配置调用）。

    Args:
        workspace_root: 工作目录根路径。
    """
    global _guard
    _guard = FileAccessGuard(workspace_root)


def _is_date_like(value: Any) -> bool:
    """判断值是否为日期/时间类型。"""
    return isinstance(value, (date, datetime, pd.Timestamp))


def _normalize_cell(value: Any) -> Any:
    """将单元格值规范化为便于表头检测的格式。"""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    return value


def _trim_trailing_nulls_generic(row: list[Any]) -> list[Any]:
    """裁剪尾部空值，减少噪音列影响。"""
    end = len(row)
    while end > 0 and row[end - 1] is None:
        end -= 1
    return row[:end]


def _looks_like_title_row(first_cell: Any) -> bool:
    """判断首单元格是否更像标题而非字段名。"""
    if not isinstance(first_cell, str):
        return False
    text = first_cell.strip()
    if not text:
        return False
    if any(token in text for token in ("──", "——", "年度", "季度")):
        return True
    return any(hint in text for hint in _TITLE_HINT_PREFIXES)


def _header_row_score(
    row_values: list[Any],
    row_idx0: int,
    next_row_values: list[Any] | None = None,
) -> float:
    """计算候选表头行分数。分数越高越可能是表头。"""
    non_empty = [v for v in row_values if v is not None]
    if len(non_empty) < _HEADER_MIN_NON_EMPTY:
        return float("-inf")

    text_values = [str(v).strip() for v in non_empty if isinstance(v, str) and str(v).strip()]
    numeric_count = sum(1 for v in non_empty if isinstance(v, (int, float)))
    date_count = sum(1 for v in non_empty if _is_date_like(v))
    string_count = len(text_values)
    unique_ratio = len(set(map(str, non_empty))) / max(len(non_empty), 1)
    keyword_hits = sum(
        1
        for text in text_values
        if any(k in text for k in _HEADER_KEYWORDS)
    )

    score = 0.0
    score += len(non_empty) * 2.0
    score += string_count * 1.6
    score -= numeric_count * 1.4
    score -= date_count * 1.0
    score += unique_ratio * 2.5
    score += keyword_hits * 2.8
    score -= row_idx0 * 0.03  # 轻微偏好靠前行

    first_cell = row_values[0] if row_values else None
    if _looks_like_title_row(first_cell):
        score -= 6.0

    if next_row_values is not None:
        next_non_empty = [v for v in next_row_values if v is not None]
        if next_non_empty:
            next_numeric_ratio = sum(1 for v in next_non_empty if isinstance(v, (int, float))) / len(next_non_empty)
            # 表头下一行常见“数据占比更高”
            score += next_numeric_ratio * 1.8
    return score


def _guess_header_row_from_rows(rows: list[list[Any]], *, max_scan: int | None = None, skip_rows: set[int] | None = None) -> int | None:
    """基于抽样行猜测 header 行号（0-indexed）。"""
    if not rows:
        return None

    upper = len(rows) if max_scan is None else min(len(rows), max_scan)
    best_row: int | None = None
    best_score = float("-inf")

    for idx in range(upper):
        if skip_rows and idx in skip_rows:
            continue
        row = _trim_trailing_nulls_generic(rows[idx])
        next_row = _trim_trailing_nulls_generic(rows[idx + 1]) if idx + 1 < upper else None
        score = _header_row_score(row, idx, next_row)
        if score > best_score:
            best_score = score
            best_row = idx

    if best_row is None or best_score == float("-inf"):
        return None
    return best_row

def _detect_header_row(
    safe_path: Any,
    sheet_name: str | None,
    max_scan: int = _HEADER_SCAN_ROWS,
    max_scan_columns: int = _HEADER_SCAN_COLS,
) -> int | None:
    """启发式检测 header 行号（0-indexed）。

    策略：
    1. 扫描前 N 行（默认 30）和前 M 列（默认 200）；
    2. 对每一行按“文本占比、关键字、唯一性、数据行特征”打分；
    3. 选择分数最高者作为表头。

    Returns:
        检测到的 header 行号（从0开始），无法确定时返回 None。
    """
    try:
        from openpyxl import load_workbook
        wb = load_workbook(safe_path, read_only=False, data_only=True)
    except Exception:
        return None

    try:
        if sheet_name:
            resolved = resolve_sheet_name(sheet_name, wb.sheetnames)
            if resolved:
                ws = wb[resolved]
            else:
                ws = wb.active
        else:
            ws = wb.active
        if ws is None:
            return None

        # 收集宽合并行（列跨度 > 50% 总列数）
        scan_cols = max(1, min(max_scan_columns, ws.max_column or max_scan_columns))
        wide_merged_rows: set[int] = set()
        for merged_range in ws.merged_cells.ranges:
            col_span = merged_range.max_col - merged_range.min_col + 1
            if col_span > scan_cols * 0.5:
                for r in range(merged_range.min_row, merged_range.max_row + 1):
                    if r <= max_scan:
                        wide_merged_rows.add(r - 1)  # 转为 0-indexed

        rows: list[list[Any]] = []
        for row in ws.iter_rows(
            min_row=1,
            max_row=max_scan,
            min_col=1,
            max_col=scan_cols,
            values_only=True,
        ):
            rows.append([_normalize_cell(c) for c in row])

        if not rows:
            return None

        return _guess_header_row_from_rows(rows, max_scan=max_scan, skip_rows=wide_merged_rows)
    finally:
        wb.close()


def _build_read_kwargs(
    safe_path: Any,
    sheet_name: str | None,
    max_rows: int | None = None,
    header_row: int | None = None,
) -> dict[str, Any]:
    """构建 pd.read_excel 的公共参数，统一处理 header_row。

    Args:
        safe_path: 已校验的文件路径。
        sheet_name: 工作表名称。
        max_rows: 最大读取行数。
        header_row: 列头所在行号（从0开始），默认自动检测。
            不传此参数时工具会启发式检测真正的表头行；
            仅在自动检测不准确时才显式指定。

    Returns:
        可直接传给 pd.read_excel 的关键字参数字典。
    """
    kwargs: dict[str, Any] = {"io": safe_path}
    if sheet_name is not None:
        kwargs["sheet_name"] = sheet_name
    if max_rows is not None:
        kwargs["nrows"] = max_rows
    if header_row is not None:
        kwargs["header"] = header_row
    else:
        # 启发式自动检测 header 行（仅当用户未显式指定时）
        detected = _detect_header_row(safe_path, sheet_name)
        if detected is not None and detected > 0:
            kwargs["header"] = detected
            logger.info("自动检测 header_row=%d (sheet=%s)", detected, sheet_name)
    return kwargs


def _resolve_formula_columns(
    df: pd.DataFrame,
    safe_path: Any,
    sheet_name: str | None,
    header_row: int,
) -> pd.DataFrame:
    """对公式列进行求值：当 data_only 模式读到全 NaN 时，
    尝试解析公式并用已有列数据计算。

    仅处理同行引用的简单算术公式（如 =G4*H4, =I4*(1-J4)）。
    复杂公式（跨行引用、函数调用等）会被静默跳过。
    """
    import re

    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    formula_meta: dict[str, Any] = {
        "resolved_columns": [],
        "unresolved_columns": [],
        "unresolved_details": {},
    }
    if df.empty:
        df.attrs["formula_resolution"] = formula_meta
        return df

    # 找出全 NaN 的列
    nan_cols_idx = [
        i for i, col in enumerate(df.columns)
        if df[col].isna().all()
    ]
    if not nan_cols_idx:
        df.attrs["formula_resolution"] = formula_meta
        return df

    wb = load_workbook(safe_path, data_only=False, read_only=True)
    try:
        ws = get_worksheet(wb, sheet_name)
        if ws is None:
            df.attrs["formula_resolution"] = formula_meta
            return df

        excel_header_row = header_row + 1  # 0-indexed → 1-indexed
        first_data_row = excel_header_row + 1

        # 列字母 → DataFrame 列索引映射
        col_names = list(df.columns)
        letter_to_idx: dict[str, int] = {}
        for i in range(len(col_names)):
            letter_to_idx[get_column_letter(i + 1)] = i

        cell_ref_pattern = re.compile(r'([A-Z]+)(\d+)')
        data_row_str = str(first_data_row)

        resolved_cols: set[str] = set()
        unresolved: dict[str, str] = {}

        for col_idx in nan_cols_idx:
            col_name = str(col_names[col_idx])
            cell = ws.cell(row=first_data_row, column=col_idx + 1)
            formula = cell.value
            if not isinstance(formula, str) or not formula.startswith('='):
                continue

            formula_body = formula[1:]

            # 提取所有单元格引用
            refs = cell_ref_pattern.findall(formula_body)
            if not refs:
                unresolved[col_name] = "不支持的公式结构（无单元格引用）"
                continue

            # 仅处理同行引用
            if not all(row_num == data_row_str for _, row_num in refs):
                unresolved[col_name] = "不支持跨行/跨区引用公式"
                continue

            # 检查所有引用列是否存在
            all_valid = True
            for letter, _ in refs:
                if letter not in letter_to_idx:
                    all_valid = False
                    break
            if not all_valid:
                unresolved[col_name] = "公式引用超出可读列范围"
                continue

            # 构建求值表达式：将单元格引用替换为变量名
            expr = formula_body
            namespace: dict[str, Any] = {}
            # 按字母长度降序替换，避免 A 替换 AA 的子串问题
            sorted_refs = sorted(set(refs), key=lambda r: (-len(r[0]), r[0]))
            for letter, row_num in sorted_refs:
                ref_idx = letter_to_idx[letter]
                var_name = f'_c{ref_idx}'
                namespace[var_name] = df.iloc[:, ref_idx]
                expr = expr.replace(f'{letter}{row_num}', var_name)

            try:
                result = eval(expr, {"__builtins__": {}}, namespace)  # noqa: S307
                if isinstance(result, pd.Series):
                    df.iloc[:, col_idx] = result
                    resolved_cols.add(col_name)
                    unresolved.pop(col_name, None)
                    logger.info(
                        "公式列 '%s' 求值成功 (formula=%s)",
                        col_names[col_idx], formula,
                    )
                else:
                    unresolved[col_name] = "公式求值未返回可用序列"
            except Exception:
                unresolved[col_name] = "公式求值失败"
                logger.debug(
                    "公式列 '%s' 求值失败 (formula=%s), 已跳过",
                    col_names[col_idx], formula,
                )
                continue
    finally:
        wb.close()

    formula_meta["resolved_columns"] = sorted(resolved_cols)
    formula_meta["unresolved_columns"] = sorted(unresolved)
    formula_meta["unresolved_details"] = unresolved
    df.attrs["formula_resolution"] = formula_meta

    return df


def _get_sheet_total_rows(safe_path: Any, sheet_name: str | None) -> int | None:
    """用 openpyxl read_only 模式快速获取 sheet 总行数（不加载全部数据）。"""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(safe_path, read_only=True, data_only=True)
        try:
            if sheet_name and sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
            else:
                ws = wb.active
            return ws.max_row or 0
        finally:
            wb.close()
    except Exception:
        return None


def _read_df(
    safe_path: Any,
    sheet_name: str | None,
    max_rows: int | None = None,
    header_row: int | None = None,
) -> tuple[pd.DataFrame, int]:
    """统一读取 Excel 为 DataFrame，含 header 自动检测 + 公式列求值。

    当自动检测的 header_row 导致超过 50% 列名为 Unnamed 时，
    自动向下尝试最多 5 行寻找更合理的表头。

    Returns:
        (DataFrame, effective_header_row) 元组。
    """
    kwargs = _build_read_kwargs(safe_path, sheet_name, max_rows=max_rows, header_row=header_row)
    effective_header = kwargs.get("header", 0)
    df = pd.read_excel(**kwargs)

    # 仅在自动检测模式下（用户未显式指定 header_row）执行 Unnamed 回退
    if header_row is None:
        unnamed_ratio = (
            sum(1 for c in df.columns if str(c).startswith("Unnamed"))
            / max(len(df.columns), 1)
        )
        if unnamed_ratio > 0.5:
            logger.info(
                "自动检测 header_row=%d 产生 %.0f%% Unnamed 列名，尝试回退",
                effective_header, unnamed_ratio * 100,
            )
            for try_header in range(effective_header + 1, min(effective_header + 6, 30)):
                retry_kwargs = {**kwargs, "header": try_header}
                try:
                    df_retry = pd.read_excel(**retry_kwargs)
                except Exception:
                    break
                if df_retry.empty:
                    break
                retry_unnamed = sum(
                    1 for c in df_retry.columns if str(c).startswith("Unnamed")
                )
                if retry_unnamed / max(len(df_retry.columns), 1) < 0.3:
                    logger.info("回退成功：header_row=%d → %d", effective_header, try_header)
                    df = df_retry
                    effective_header = try_header
                    break

    df = _resolve_formula_columns(df, safe_path, sheet_name, effective_header)
    return df, effective_header


# ── 工具函数 ──────────────────────────────────────────────



def read_excel(
    file_path: str,
    sheet_name: str | None = None,
    max_rows: int | None = None,
    include_style_summary: bool = False,
    header_row: int | None = None,
    include: list[str] | None = None,
    max_style_scan_rows: int = 200,
) -> str:
    """读取 Excel 文件并返回数据摘要，可通过 include 按需附加额外维度。

    Args:
        file_path: Excel 文件路径（相对或绝对）。
        sheet_name: 工作表名称，默认读取第一个。
        max_rows: 最大读取行数，默认全部读取。
        include_style_summary: 是否附带样式概览（已废弃，请用 include=["styles"]）。
        header_row: 列头所在行号（从0开始），默认自动检测。
            当工作表有合并标题行时，需指定真正的列头行号。
        include: 按需请求的额外维度列表。可选值：
            styles — 压缩样式类（Style Classes + cell_style_map + merged_ranges）
            charts — 嵌入图表元信息
            images — 嵌入图片元信息
            freeze_panes — 冻结窗格位置
            conditional_formatting — 条件格式规则
            data_validation — 数据验证规则
            print_settings — 打印设置
            column_widths — 非默认列宽
            formulas — 含公式的单元格
            categorical_summary — 分类列的 value_counts（unique 值 < 阈值的列）
        max_style_scan_rows: styles/formulas 维度扫描的最大行数，默认 200。

    Returns:
        JSON 格式的数据摘要字符串。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    df, effective_header = _read_df(safe_path, sheet_name, max_rows=max_rows, header_row=header_row)

    # 当 max_rows 限制了读取行数时，获取 sheet 实际总行数
    total_rows_in_sheet: int | None = None
    if max_rows is not None:
        total_rows_in_sheet = _get_sheet_total_rows(safe_path, sheet_name)

    # 构建摘要信息
    summary: dict[str, Any] = {
        "file": str(safe_path.name),
        "shape": {"rows": df.shape[0], "columns": df.shape[1]},
    }

    # 数据完整性指示：截断元数据放在 columns/preview 之前，确保即使被引擎层截断也能保留
    if total_rows_in_sheet is not None and total_rows_in_sheet > df.shape[0]:
        completeness = build_completeness_meta(
            total_available=total_rows_in_sheet,
            returned=df.shape[0],
        )
        summary["total_rows_in_sheet"] = total_rows_in_sheet
        summary["is_truncated"] = completeness.get("is_truncated", False)
        summary["truncation_note"] = completeness.get("truncation_note", "")

    summary["columns"] = [str(c) for c in df.columns]
    summary["dtypes"] = {str(col): str(dtype) for col, dtype in df.dtypes.items()}
    summary["preview"] = json.loads(df.head(10).to_json(orient="records", force_ascii=False, date_format="iso"))
    formula_meta = df.attrs.get("formula_resolution")
    if isinstance(formula_meta, dict):
        if formula_meta.get("resolved_columns") or formula_meta.get("unresolved_columns"):
            summary["formula_resolution"] = formula_meta

    # 当 header_row 被自动检测时，告知 LLM 实际使用的行号
    if header_row is None and effective_header != 0:
        summary["detected_header_row"] = effective_header

    # Unnamed 列名警告：提醒 LLM 列名不可靠，建议指定 header_row
    unnamed_cols = [str(c) for c in df.columns if str(c).startswith("Unnamed")]
    if unnamed_cols:
        summary["unnamed_columns_warning"] = (
            f"检测到 {len(unnamed_cols)} 个 Unnamed 列名（共 {len(df.columns)} 列），"
            f"可能是合并标题行导致。建议使用 header_row 参数指定真正的列头行号重新读取。"
        )

    # 向后兼容：include_style_summary=True 映射为 include=["styles"]
    include_set: set[str] = set()
    if include:
        include_set.update(include)
    if include_style_summary and "styles" not in include_set:
        include_set.add("styles")

    # 校验 include 维度
    invalid_dims = include_set - set(INCLUDE_DIMENSIONS)
    if invalid_dims:
        summary["include_warning"] = f"未知的 include 维度已忽略: {sorted(invalid_dims)}"
        include_set -= invalid_dims

    # 分发基于 DataFrame 的 include 维度（不需要 openpyxl）
    if "categorical_summary" in include_set:
        summary["categorical_summary"] = _collect_categorical_summary(df)
        include_set.discard("categorical_summary")

    # 分发 include 维度采集（需要用 openpyxl 打开，非 data_only 以获取公式）
    if include_set:
        from openpyxl import load_workbook

        # styles/charts/images/freeze_panes 等需要非 data_only 模式
        # formulas 也需要非 data_only 模式以读取公式文本
        needs_formulas = "formulas" in include_set
        wb_include = load_workbook(safe_path, data_only=not needs_formulas)
        try:
            ws_include = (
                wb_include[sheet_name]
                if sheet_name and sheet_name in wb_include.sheetnames
                else wb_include.active
            )
            extra = _dispatch_include_dimensions(ws_include, include_set, max_style_scan_rows)
            summary.update(extra)
        finally:
            wb_include.close()

    return json.dumps(summary, ensure_ascii=False, indent=2, default=str)



def write_excel(file_path: str, data: list[dict], sheet_name: str = "Sheet1") -> str:
    """将数据写入 Excel 文件。

    当目标文件已存在时，仅写入/替换指定工作表，保留其他工作表。
    当目标文件不存在时，创建新文件。

    Args:
        file_path: 目标 Excel 文件路径。
        data: 要写入的数据，每个字典代表一行。
        sheet_name: 工作表名称，默认 Sheet1。

    Returns:
        操作结果描述。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    df = pd.DataFrame(data)

    if safe_path.exists() and safe_path.suffix.lower() in (".xlsx", ".xlsm"):
        # 已有文件：使用 append 模式，仅替换指定 sheet，保留其他 sheet
        with pd.ExcelWriter(
            safe_path,
            engine="openpyxl",
            mode="a",
            if_sheet_exists="replace",
        ) as writer:
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    else:
        # 新文件：直接写入
        df.to_excel(safe_path, sheet_name=sheet_name, index=False)

    return json.dumps(
        {"status": "success", "file": str(safe_path.name), "rows": len(df), "columns": len(df.columns)},
        ensure_ascii=False,
    )



def analyze_data(
    file_path: str,
    sheet_name: str | None = None,
    header_row: int | None = None,
) -> str:
    """对 Excel 数据进行基本统计分析。

    Args:
        file_path: Excel 文件路径。
        sheet_name: 工作表名称，默认第一个。
        header_row: 列头所在行号（从0开始），默认自动检测。

    Returns:
        JSON 格式的统计分析结果。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    df, _ = _read_df(safe_path, sheet_name, header_row=header_row)

    # 基本统计信息
    result: dict[str, Any] = {
        "file": str(safe_path.name),
        "shape": {"rows": df.shape[0], "columns": df.shape[1]},
        "columns": [str(c) for c in df.columns],
        "missing_values": {str(col): int(count) for col, count in df.isnull().sum().items() if count > 0},
    }

    # 数值列统计
    numeric_df = df.select_dtypes(include=["number"])
    if not numeric_df.empty:
        stats = numeric_df.describe().to_dict()
        # 将 numpy 类型转为 Python 原生类型
        result["numeric_stats"] = {
            str(col): {k: float(v) for k, v in col_stats.items()}
            for col, col_stats in stats.items()
        }

    return json.dumps(result, ensure_ascii=False, indent=2, default=str)




def filter_data(
    file_path: str,
    column: str | None = None,
    operator: str | None = None,
    value: Any = None,
    sheet_name: str | None = None,
    header_row: int | None = None,
    columns: list[str] | None = None,
    conditions: list[dict[str, Any]] | None = None,
    logic: str = "and",
    max_rows: int | None = None,
    sort_by: str | None = None,
    ascending: bool = True,
    limit: int | None = None,
) -> str:
    """根据条件过滤 Excel 数据行并可选排序，支持单条件和多条件 AND/OR 组合。

    Args:
        file_path: Excel 文件路径。
        column: 要过滤的列名（单条件模式）。
        operator: 比较运算符，支持 eq/ne/gt/ge/lt/le/contains（单条件模式）。
        value: 比较值（单条件模式）。
        sheet_name: 工作表名称，默认第一个。
        header_row: 列头所在行号（从0开始），默认自动检测。
        columns: 只返回指定列（投影），默认返回全部列。
        conditions: 多条件数组，每个元素为 {"column": str, "operator": str, "value": Any}。
        logic: 多条件组合方式，"and"（默认）或 "or"。
        max_rows: 最多返回的数据行数，默认返回全部。
        sort_by: 排序列名，默认不排序。
        ascending: 排序方向，默认升序。
        limit: 排序后限制返回行数，默认返回全部。

    Returns:
        JSON 格式的过滤结果。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    df, _ = _read_df(safe_path, sheet_name, header_row=header_row)

    ops = {
        "eq": lambda s, v: s == v,
        "ne": lambda s, v: s != v,
        "gt": lambda s, v: s > v,
        "ge": lambda s, v: s >= v,
        "lt": lambda s, v: s < v,
        "le": lambda s, v: s <= v,
        "contains": lambda s, v: s.astype(str).str.contains(str(v), na=False),
    }

    # 构建条件列表：兼容单条件和多条件
    if conditions:
        cond_list = conditions
    elif column is not None and operator is not None:
        cond_list = [{"column": column, "operator": operator, "value": value}]
    else:
        return json.dumps(
            {"error": "请提供 column/operator/value 单条件，或 conditions 多条件数组"},
            ensure_ascii=False,
        )

    if logic not in ("and", "or"):
        return json.dumps(
            {"error": f"不支持的逻辑运算符 '{logic}'，支持: and, or"},
            ensure_ascii=False,
        )

    # 逐条件构建 mask
    masks = []
    for cond in cond_list:
        col = cond.get("column")
        op = cond.get("operator")
        val = cond.get("value")
        if col not in df.columns:
            return json.dumps(
                {"error": f"列 '{col}' 不存在，可用列: {[str(c) for c in df.columns]}"},
                ensure_ascii=False,
                default=str,
            )
        if op not in ops:
            return json.dumps(
                {"error": f"不支持的运算符 '{op}'，支持: {list(ops.keys())}"},
                ensure_ascii=False,
            )
        masks.append(ops[op](df[col], val))

    # 组合 mask
    if logic == "and":
        combined_mask = functools.reduce(lambda a, b: a & b, masks)
    else:
        combined_mask = functools.reduce(lambda a, b: a | b, masks)

    filtered = df[combined_mask]

    # 投影：只保留指定列
    if columns:
        valid_cols = [c for c in columns if c in filtered.columns]
        missing_cols = [c for c in columns if c not in filtered.columns]
        filtered = filtered[valid_cols]
    else:
        missing_cols = []

    # 排序
    if sort_by is not None:
        if sort_by not in filtered.columns:
            return json.dumps(
                {"error": f"排序列 '{sort_by}' 不存在，可用列: {[str(c) for c in filtered.columns]}"},
                ensure_ascii=False,
                default=str,
            )
        # 对排序列做数值转换以支持文本型数值的正确排序
        sort_key = _coerce_numeric(filtered[sort_by])
        filtered = filtered.assign(**{"__sort_key__": sort_key}).sort_values(
            by="__sort_key__", ascending=ascending, na_position="last", kind="mergesort"
        ).drop(columns=["__sort_key__"])

    # 排序后限制返回行数（limit）
    total_filtered = len(filtered)
    if limit is not None and limit > 0:
        filtered = filtered.head(limit)

    # max_rows 兜底限制
    if max_rows is not None and max_rows > 0:
        filtered = filtered.head(max_rows)

    result: dict[str, Any] = {
        "file": str(safe_path.name),
        "filters": cond_list,
        "logic": logic,
        "original_rows": len(df),
        "filtered_rows": total_filtered,
        "returned_rows": len(filtered),
        "data": json.loads(filtered.to_json(orient="records", force_ascii=False)),
    }
    if total_filtered > len(filtered):
        result["truncated"] = True
        result["note"] = f"结果已截断，共 {total_filtered} 条匹配，返回前 {len(filtered)} 条"
    if missing_cols:
        result["missing_columns"] = missing_cols

    return json.dumps(result, ensure_ascii=False, indent=2, default=str)




def transform_data(
    file_path: str,
    operations: list[dict[str, Any]],
    sheet_name: str | None = None,
    output_path: str | None = None,
    header_row: int | None = None,
) -> str:
    """对 Excel 数据执行转换操作。

    支持的操作类型：
    - rename: 重命名列，参数 {"columns": {"旧名": "新名"}}
    - add_column: 添加新列，参数 {"name": "列名", "value": 值或表达式}
    - drop_columns: 删除列，参数 {"columns": ["列名1", "列名2"]}
    - sort: 排序，参数 {"by": "列名", "ascending": true/false}

    Args:
        file_path: 源 Excel 文件路径。
        operations: 转换操作列表，每项包含 type 和对应参数。
        sheet_name: 工作表名称，默认第一个。
        output_path: 输出文件路径，默认覆盖源文件。
        header_row: 列头所在行号（从0开始），默认自动检测。

    Returns:
        JSON 格式的转换结果。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    df, _ = _read_df(safe_path, sheet_name, header_row=header_row)

    applied: list[str] = []
    for op in operations:
        op_type = op.get("type", "")

        if op_type == "rename":
            columns_map = op.get("columns", {})
            df = df.rename(columns=columns_map)
            applied.append(f"rename: {columns_map}")

        elif op_type == "add_column":
            col_name = op.get("name", "")
            col_value = op.get("value", None)
            df[col_name] = col_value
            applied.append(f"add_column: {col_name}")

        elif op_type == "drop_columns":
            cols = op.get("columns", [])
            existing = [c for c in cols if c in df.columns]
            df = df.drop(columns=existing)
            applied.append(f"drop_columns: {existing}")

        elif op_type == "sort":
            by = op.get("by", "")
            ascending = op.get("ascending", True)
            if by in df.columns:
                df = df.sort_values(by=by, ascending=ascending)
                applied.append(f"sort: {by} {'asc' if ascending else 'desc'}")

        else:
            applied.append(f"unknown_op: {op_type}")

    def _resolve_target_sheet(default_path: Any, desired_sheet: str | None) -> str:
        if desired_sheet:
            return desired_sheet
        try:
            from openpyxl import load_workbook

            wb = load_workbook(default_path, read_only=True, data_only=True)
            try:
                if wb.sheetnames:
                    return wb.sheetnames[0]
            finally:
                wb.close()
        except Exception:
            pass
        return "Sheet1"

    # 写入输出文件
    if output_path is not None:
        out_safe = guard.resolve_and_validate(output_path)
    else:
        out_safe = safe_path

    target_sheet = _resolve_target_sheet(safe_path, sheet_name)

    source_ext = safe_path.suffix.lower()
    output_ext = out_safe.suffix.lower()
    can_preserve_other_sheets = (
        source_ext in {".xlsx", ".xlsm"}
        and output_ext in {".xlsx", ".xlsm"}
    )

    if can_preserve_other_sheets:
        # 输出文件不存在时，先复制源工作簿，再仅替换目标 sheet。
        if output_path is not None and not out_safe.exists():
            out_safe.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(safe_path, out_safe)

        writer_kwargs: dict[str, Any] = {
            "engine": "openpyxl",
            "mode": "a" if out_safe.exists() else "w",
        }
        if writer_kwargs["mode"] == "a":
            writer_kwargs["if_sheet_exists"] = "replace"
        if output_ext == ".xlsm":
            writer_kwargs["engine_kwargs"] = {"keep_vba": True}

        with pd.ExcelWriter(out_safe, **writer_kwargs) as writer:
            df.to_excel(writer, index=False, sheet_name=target_sheet)
    else:
        df.to_excel(out_safe, index=False, sheet_name=target_sheet)

    result = {
        "status": "success",
        "file": str(out_safe.name),
        "sheet": target_sheet,
        "operations_applied": applied,
        "shape": {"rows": df.shape[0], "columns": df.shape[1]},
    }
    return json.dumps(result, ensure_ascii=False, indent=2)



# inspect_excel_files 可用的 include 维度
_SCAN_FILES_DIMENSIONS = (
    "freeze_panes",
    "charts",
    "images",
    "conditional_formatting",
    "column_widths",
)


def inspect_excel_files(
    directory: str = ".",
    max_files: int = 20,
    preview_rows: int = 3,
    max_columns: int = 15,
    include: list[str] | None = None,
) -> str:
    """批量扫描目录下所有 Excel 文件，返回轻量级概览，可按需附加额外维度。

    使用 openpyxl 只读模式，仅读取 sheet 元信息和少量预览行，
    避免加载完整 DataFrame，适合快速了解工作区全貌。

    Args:
        directory: 扫描目录（相对于工作目录），默认当前目录。
        max_files: 最多扫描文件数，默认 20。
        preview_rows: 每个 sheet 预览行数，默认 3。
        max_columns: header/preview 最多展示列数，默认 15。
        include: 按需请求的额外维度列表。可选值：
            freeze_panes, charts, images, conditional_formatting, column_widths。

    Returns:
        JSON 格式的批量概览结果。
    """
    from datetime import datetime, timezone
    from pathlib import Path

    from openpyxl import load_workbook

    include_set: set[str] = set(include) if include else set()
    invalid_dims = include_set - set(_SCAN_FILES_DIMENSIONS)
    include_set -= invalid_dims
    needs_full = bool(include_set)

    guard = _get_guard()
    safe_dir = guard.resolve_and_validate(directory)

    if not safe_dir.is_dir():
        return json.dumps(
            {"error": f"路径 '{directory}' 不是一个有效的目录"},
            ensure_ascii=False,
        )

    # 收集 Excel 文件（.xlsx / .xlsm），跳过隐藏文件和临时文件
    # 先收集全部再排序，确保结果确定性（glob 返回顺序依赖文件系统，不可靠）
    excel_paths: list[Path] = []
    for ext in ("*.xlsx", "*.xlsm"):
        for p in safe_dir.glob(ext):
            if p.name.startswith((".", "~$")):
                continue
            excel_paths.append(p)
    excel_paths.sort(key=lambda p: p.name.lower())
    excel_paths = excel_paths[:max_files]

    files_summary: list[dict[str, Any]] = []
    for fp in excel_paths:
        stat = fp.stat()
        file_info: dict[str, Any] = {
            "file": fp.name,
            "path": str(fp.relative_to(guard.workspace_root)),
            "size": _format_size(stat.st_size),
            "modified": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d"),
        }

        sheets_info: list[dict[str, Any]] = []
        try:
            wb = load_workbook(fp, read_only=not needs_full, data_only=True)
            for sn in wb.sheetnames:
                ws = wb[sn]
                total_cols = ws.max_column or 0
                sheet_data: dict[str, Any] = {
                    "name": sn,
                    "rows": ws.max_row or 0,
                    "columns": total_cols,
                }

                # 读取抽样行，用于表头识别与预览
                scan_rows = max(8, preview_rows + 8)
                scan_cols = max(1, min(total_cols if total_cols > 0 else _HEADER_SCAN_COLS, _HEADER_SCAN_COLS))
                rows_raw: list[list[Any]] = []
                for row in ws.iter_rows(
                    min_row=1,
                    max_row=scan_rows,
                    min_col=1,
                    max_col=scan_cols,
                    values_only=True,
                ):
                    rows_raw.append([_normalize_cell(c) for c in row])

                if rows_raw:
                    header_idx = _guess_header_row_from_rows(rows_raw, max_scan=scan_rows)
                    if header_idx is None:
                        header_idx = 0

                    header_raw = rows_raw[header_idx] if header_idx < len(rows_raw) else []
                    header = _trim_trailing_nulls([_cell_to_str(c) for c in header_raw])

                    preview_raw = rows_raw[header_idx + 1:header_idx + 1 + preview_rows]
                    preview = [_trim_trailing_nulls([_cell_to_str(c) for c in r]) for r in preview_raw]

                    # 仅对 preview 数据行限宽，header 完整保留以确保 agent 理解全部列语义
                    if any(len(r) > max_columns for r in preview):
                        preview = [r[:max_columns] for r in preview]
                        sheet_data["preview_columns_truncated"] = max_columns

                    sheet_data["header_row_hint"] = header_idx
                    sheet_data["business_columns"] = len(header)
                    sheet_data["header"] = header
                    sheet_data["preview"] = preview

                # 按需采集额外维度
                if needs_full and include_set:
                    if "freeze_panes" in include_set:
                        sheet_data["freeze_panes"] = _collect_freeze_panes(ws)
                    if "charts" in include_set:
                        sheet_data["charts"] = _collect_charts(ws)
                    if "images" in include_set:
                        sheet_data["images"] = _collect_images(ws)
                    if "conditional_formatting" in include_set:
                        sheet_data["conditional_formatting"] = _collect_conditional_formatting(ws)
                    if "column_widths" in include_set:
                        sheet_data["column_widths"] = _collect_column_widths(ws)

                sheets_info.append(sheet_data)
            wb.close()
        except Exception as exc:  # noqa: BLE001
            file_info["error"] = f"无法读取: {exc}"

        file_info["sheets"] = sheets_info
        files_summary.append(file_info)

    # 紧凑文件清单放在最前，即使详细信息被截断也能保留完整文件列表
    file_list = [
        {"file": fp.name, "size": _format_size(fp.stat().st_size)}
        for fp in excel_paths
    ]

    result: dict[str, Any] = {
        "directory": directory,
        "excel_files_found": len(excel_paths),
        "truncated": len(excel_paths) >= max_files,
        "file_list": file_list,
        "files": files_summary,
    }
    if invalid_dims:
        result["include_warning"] = f"未知的 include 维度已忽略: {sorted(invalid_dims)}"
    return json.dumps(result, ensure_ascii=False, separators=(',', ':'), default=str)


def _cell_to_str(value: Any) -> str | None:
    """将单元格值转换为紧凑字符串，None 保持为 None。"""
    if value is None:
        return None
    return str(value)


def _trim_trailing_nulls(row: list[Any]) -> list[Any]:
    """去除列表尾部连续的 None 值，减少 JSON 体积。"""
    end = len(row)
    while end > 0 and row[end - 1] is None:
        end -= 1
    return row[:end]


def _format_size(size_bytes: int) -> str:
    """将字节数格式化为可读字符串。"""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}" if unit != "B" else f"{size_bytes}{unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f}TB"


# ── 样式概览辅助函数 ──────────────────────────────────────


def _collect_style_summary(file_path: Any, sheet_name: str | None) -> dict[str, Any]:
    """用 openpyxl 扫描工作表，收集样式概览信息。"""
    from pathlib import Path

    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    path = Path(file_path) if not isinstance(file_path, Path) else file_path
    wb = load_workbook(path, data_only=True)
    ws = get_worksheet(wb, sheet_name)

    fill_colors: set[str] = set()
    font_colors: set[str] = set()
    has_colored_cells = False

    # 抽样扫描（最多前 100 行）避免大文件性能问题
    max_scan_rows = min(ws.max_row or 0, 100)
    for row in ws.iter_rows(min_row=1, max_row=max_scan_rows):
        for cell in row:
            # 填充色
            if cell.fill and cell.fill.fgColor:
                fg = cell.fill.fgColor
                if hasattr(fg, "rgb") and fg.rgb and fg.rgb not in ("00000000", "FFFFFFFF"):
                    rgb = str(fg.rgb)
                    color = rgb[2:] if len(rgb) == 8 else rgb
                    fill_colors.add(color)
                    has_colored_cells = True
            # 字体色
            if cell.font and cell.font.color:
                fc = cell.font.color
                if hasattr(fc, "rgb") and fc.rgb and fc.rgb not in ("00000000", "FF000000"):
                    rgb = str(fc.rgb)
                    color = rgb[2:] if len(rgb) == 8 else rgb
                    font_colors.add(color)
                    has_colored_cells = True

    # 合并单元格
    merged_ranges = [str(mr) for mr in ws.merged_cells.ranges]

    # 条件格式
    has_conditional = len(ws.conditional_formatting) > 0

    wb.close()

    return {
        "has_colored_cells": has_colored_cells,
        "fill_colors_used": sorted(fill_colors),
        "font_colors_used": sorted(font_colors),
        "has_merged_cells": len(merged_ranges) > 0,
        "merged_ranges": merged_ranges,
        "has_conditional_formatting": has_conditional,
        "rows_scanned": max_scan_rows,
    }


# ── include 维度采集函数 ──────────────────────────────────

# include 参数所有合法维度
INCLUDE_DIMENSIONS = (
    "data_preview",
    "styles",
    "charts",
    "images",
    "freeze_panes",
    "conditional_formatting",
    "data_validation",
    "print_settings",
    "column_widths",
    "formulas",
    "categorical_summary",
)

# categorical_summary 默认阈值：unique 值数量低于此值的列视为分类列
_CATEGORICAL_UNIQUE_THRESHOLD = 20


def _collect_categorical_summary(
    df: "pd.DataFrame",
    threshold: int = _CATEGORICAL_UNIQUE_THRESHOLD,
) -> dict[str, Any]:
    """对分类列（unique 值 < threshold）计算 value_counts，返回摘要字典。

    Returns:
        {"threshold": int, "columns": {col: {val: count, ...}, ...}}
    """
    result: dict[str, dict[str, int]] = {}
    for col in df.columns:
        series = df[col].dropna()
        if series.empty:
            continue
        n_unique = series.nunique()
        if 0 < n_unique <= threshold:
            vc = series.value_counts(dropna=True)
            result[str(col)] = {str(k): int(v) for k, v in vc.items()}
    return {"threshold": threshold, "columns": result}


def _color_to_hex_short(color: Any) -> str | None:
    """将 openpyxl Color 对象转为 6 位十六进制字符串，无效或默认色返回 None。"""
    if color is None:
        return None
    if hasattr(color, "rgb") and color.rgb:
        rgb = str(color.rgb)
        if rgb in ("00000000", "FFFFFFFF"):
            return None
        return rgb[2:] if len(rgb) == 8 else rgb
    if hasattr(color, "theme") and color.theme is not None:
        return f"theme:{color.theme}"
    return None


def _extract_style_tuple(cell: Any) -> tuple | None:
    """从单元格提取样式关键属性元组，全默认样式返回 None。"""
    parts: list[Any] = []
    has_custom = False

    # 字体
    f = cell.font
    if f:
        font_info: dict[str, Any] = {}
        if f.name and f.name != "Calibri":
            font_info["name"] = f.name
        if f.size and f.size != 11:
            font_info["size"] = f.size
        if f.bold:
            font_info["bold"] = True
        if f.italic:
            font_info["italic"] = True
        if f.underline and f.underline != "none":
            font_info["underline"] = f.underline
        if f.strike:
            font_info["strike"] = True
        c = _color_to_hex_short(f.color)
        if c and c != "000000":
            font_info["color"] = c
        if font_info:
            has_custom = True
        parts.append(tuple(sorted(font_info.items())) if font_info else ())
    else:
        parts.append(())

    # 填充
    fl = cell.fill
    if fl:
        fill_type = fl.fill_type or fl.patternType
        if fill_type and fill_type != "none":
            fg = _color_to_hex_short(fl.fgColor)
            parts.append(("fill", fill_type, fg))
            has_custom = True
        else:
            parts.append(())
    else:
        parts.append(())

    # 边框
    b = cell.border
    if b:
        border_parts: list[tuple[str, str]] = []
        for side_name in ("left", "right", "top", "bottom"):
            side = getattr(b, side_name, None)
            if side and side.style and side.style != "none":
                border_parts.append((side_name, side.style))
        if border_parts:
            has_custom = True
        parts.append(tuple(border_parts))
    else:
        parts.append(())

    # 对齐
    a = cell.alignment
    if a:
        align_info: dict[str, Any] = {}
        if a.horizontal and a.horizontal != "general":
            align_info["horizontal"] = a.horizontal
        if a.vertical and a.vertical != "bottom":
            align_info["vertical"] = a.vertical
        if a.wrap_text:
            align_info["wrap_text"] = True
        if align_info:
            has_custom = True
        parts.append(tuple(sorted(align_info.items())) if align_info else ())
    else:
        parts.append(())

    # 数字格式
    nf = cell.number_format
    if nf and nf != "General":
        parts.append(nf)
        has_custom = True
    else:
        parts.append("")

    if not has_custom:
        return None
    return tuple(parts)


def _style_tuple_to_dict(st: tuple) -> dict[str, Any]:
    """将样式元组还原为可读字典。"""
    result: dict[str, Any] = {}
    font_parts, fill_parts, border_parts, align_parts, num_fmt = st

    if font_parts:
        result["font"] = dict(font_parts)
    if fill_parts:
        _, fill_type, fg = fill_parts
        info: dict[str, Any] = {"type": fill_type}
        if fg:
            info["color"] = fg
        result["fill"] = info
    if border_parts:
        result["border"] = {side: style for side, style in border_parts}
    if align_parts:
        result["alignment"] = dict(align_parts)
    if num_fmt:
        result["number_format"] = num_fmt
    return result


def _collect_styles_compressed(
    ws: Any,
    max_rows: int = 200,
) -> dict[str, Any]:
    """扫描工作表，以 Style Classes 压缩方式返回样式信息。

    算法：
    1. 逐单元格提取样式元组
    2. 为唯一组合分配 sN ID
    3. 按列扫描合并连续相同样式的单元格为范围

    Returns:
        包含 style_classes, cell_style_map, merged_ranges 的字典。
    """
    from openpyxl.utils import get_column_letter

    scan_rows = min(ws.max_row or 0, max_rows)
    scan_cols = ws.max_column or 0
    if scan_rows == 0 or scan_cols == 0:
        return {
            "style_classes": {},
            "cell_style_map": {},
            "merged_ranges": [str(mr) for mr in ws.merged_cells.ranges],
            "rows_scanned": scan_rows,
        }

    # 第一遍：收集所有样式元组，分配 ID
    style_to_id: dict[tuple, str] = {}
    # cell_map[col_idx][row_idx] = style_id
    cell_map: dict[int, dict[int, str]] = {}
    id_counter = 0

    for row in ws.iter_rows(min_row=1, max_row=scan_rows, min_col=1, max_col=scan_cols):
        for cell in row:
            st = _extract_style_tuple(cell)
            if st is None:
                continue
            if st not in style_to_id:
                style_to_id[st] = f"s{id_counter}"
                id_counter += 1
            sid = style_to_id[st]
            col_idx = cell.column
            row_idx = cell.row
            if col_idx not in cell_map:
                cell_map[col_idx] = {}
            cell_map[col_idx][row_idx] = sid

    # 构建 style_classes 字典
    style_classes = {sid: _style_tuple_to_dict(st) for st, sid in style_to_id.items()}

    # 第二遍：按列合并连续相同 style_id 为范围
    range_map: dict[str, str] = {}  # "A1:A10" -> "s0"

    for col_idx in sorted(cell_map.keys()):
        col_letter = get_column_letter(col_idx)
        rows_dict = cell_map[col_idx]
        sorted_rows = sorted(rows_dict.keys())
        if not sorted_rows:
            continue

        # 合并连续行
        start_row = sorted_rows[0]
        current_sid = rows_dict[start_row]
        prev_row = start_row

        for r in sorted_rows[1:]:
            sid = rows_dict[r]
            if sid == current_sid and r == prev_row + 1:
                # 连续且相同
                prev_row = r
            else:
                # 输出前一段
                if start_row == prev_row:
                    range_map[f"{col_letter}{start_row}"] = current_sid
                else:
                    range_map[f"{col_letter}{start_row}:{col_letter}{prev_row}"] = current_sid
                start_row = r
                current_sid = sid
                prev_row = r

        # 输出最后一段
        if start_row == prev_row:
            range_map[f"{col_letter}{start_row}"] = current_sid
        else:
            range_map[f"{col_letter}{start_row}:{col_letter}{prev_row}"] = current_sid

    merged_ranges = [str(mr) for mr in ws.merged_cells.ranges]

    return {
        "style_classes": style_classes,
        "cell_style_map": range_map,
        "merged_ranges": merged_ranges,
        "rows_scanned": scan_rows,
    }


def _collect_charts(ws: Any) -> list[dict[str, Any]]:
    """检测工作表中嵌入的图表，返回元信息列表。"""
    charts_info: list[dict[str, Any]] = []
    chart_list = getattr(ws, "_charts", [])
    for chart in chart_list:
        info: dict[str, Any] = {}
        # 图表类型
        type_name = type(chart).__name__.replace("Chart", "").lower()
        info["type"] = type_name
        if hasattr(chart, "title") and chart.title:
            title = chart.title
            if hasattr(title, "text"):
                info["title"] = title.text
            elif isinstance(title, str):
                info["title"] = title
        info["series_count"] = len(chart.series) if hasattr(chart, "series") else 0
        # 锚点位置
        if hasattr(chart, "anchor") and chart.anchor:
            anchor = chart.anchor
            if hasattr(anchor, "_from") and anchor._from:
                f = anchor._from
                from openpyxl.utils import get_column_letter as gcl
                info["anchor_cell"] = f"{gcl(f.col + 1)}{f.row + 1}"
        charts_info.append(info)
    return charts_info


def _collect_images(ws: Any) -> list[dict[str, Any]]:
    """检测工作表中嵌入的图片，返回元信息列表。"""
    images_info: list[dict[str, Any]] = []
    image_list = getattr(ws, "_images", [])
    for img in image_list:
        info: dict[str, Any] = {}
        if hasattr(img, "width") and img.width:
            info["width_px"] = img.width
        if hasattr(img, "height") and img.height:
            info["height_px"] = img.height
        # 图片格式
        if hasattr(img, "format"):
            info["format"] = img.format
        elif hasattr(img, "path") and img.path:
            ext = str(img.path).rsplit(".", 1)[-1] if "." in str(img.path) else "unknown"
            info["format"] = ext
        # 锚点位置
        if hasattr(img, "anchor") and img.anchor:
            anchor = img.anchor
            if isinstance(anchor, str):
                info["anchor_cell"] = anchor
            elif hasattr(anchor, "_from") and anchor._from:
                f = anchor._from
                from openpyxl.utils import get_column_letter as gcl
                info["anchor_cell"] = f"{gcl(f.col + 1)}{f.row + 1}"
        images_info.append(info)
    return images_info


def _collect_freeze_panes(ws: Any) -> str | None:
    """返回冻结窗格位置（如 'A4'），未冻结返回 None。"""
    fp = ws.freeze_panes
    return str(fp) if fp else None


def _collect_conditional_formatting(ws: Any) -> list[dict[str, Any]]:
    """收集条件格式规则列表。"""
    rules_info: list[dict[str, Any]] = []
    for cf in ws.conditional_formatting:
        ranges_str = str(cf)
        for rule in cf.rules:
            info: dict[str, Any] = {"range": ranges_str}
            if hasattr(rule, "type") and rule.type:
                info["type"] = rule.type
            if hasattr(rule, "priority") and rule.priority is not None:
                info["priority"] = rule.priority
            if hasattr(rule, "formula") and rule.formula:
                info["formula"] = list(rule.formula) if not isinstance(rule.formula, str) else [rule.formula]
            if hasattr(rule, "operator") and rule.operator:
                info["operator"] = rule.operator
            rules_info.append(info)
    return rules_info


def _collect_data_validation(ws: Any) -> list[dict[str, Any]]:
    """收集数据验证规则列表。"""
    validations: list[dict[str, Any]] = []
    dv_list = getattr(ws, "data_validations", None)
    if dv_list is None:
        return validations
    dv_items = getattr(dv_list, "dataValidation", [])
    for dv in dv_items:
        info: dict[str, Any] = {}
        if hasattr(dv, "sqref") and dv.sqref:
            info["range"] = str(dv.sqref)
        if hasattr(dv, "type") and dv.type:
            info["type"] = dv.type
        if hasattr(dv, "formula1") and dv.formula1:
            info["formula1"] = str(dv.formula1)
        if hasattr(dv, "formula2") and dv.formula2:
            info["formula2"] = str(dv.formula2)
        if hasattr(dv, "allow_blank") and dv.allow_blank is not None:
            info["allow_blank"] = bool(dv.allow_blank)
        if hasattr(dv, "showDropDown") and dv.showDropDown is not None:
            info["show_dropdown"] = bool(dv.showDropDown)
        validations.append(info)
    return validations


def _collect_print_settings(ws: Any) -> dict[str, Any]:
    """收集打印设置信息。"""
    info: dict[str, Any] = {}
    if ws.print_area:
        info["print_area"] = ws.print_area
    ps = ws.page_setup
    if ps:
        if ps.orientation:
            info["orientation"] = ps.orientation
        if ps.paperSize is not None:
            info["paper_size"] = ps.paperSize
        if ps.fitToWidth is not None:
            info["fit_to_width"] = ps.fitToWidth
        if ps.fitToHeight is not None:
            info["fit_to_height"] = ps.fitToHeight
        if ps.scale is not None:
            info["scale"] = ps.scale
    if ws.print_title_rows:
        info["repeat_rows"] = ws.print_title_rows
    if ws.print_title_cols:
        info["repeat_columns"] = ws.print_title_cols
    return info


def _collect_column_widths(ws: Any) -> dict[str, float]:
    """收集非默认列宽映射。"""
    widths: dict[str, float] = {}
    for col_letter, dim in ws.column_dimensions.items():
        if dim.width is not None and dim.width != 8.0:
            widths[col_letter] = round(dim.width, 2)
    return widths


def _collect_formulas(ws: Any, max_rows: int = 200) -> list[dict[str, str]]:
    """收集含公式的单元格位置和公式内容。"""
    from openpyxl.utils import get_column_letter

    formulas: list[dict[str, str]] = []
    scan_rows = min(ws.max_row or 0, max_rows)
    scan_cols = ws.max_column or 0
    if scan_rows == 0 or scan_cols == 0:
        return formulas

    for row in ws.iter_rows(min_row=1, max_row=scan_rows, min_col=1, max_col=scan_cols):
        for cell in row:
            val = cell.value
            if isinstance(val, str) and val.startswith("="):
                coord = f"{get_column_letter(cell.column)}{cell.row}"
                formulas.append({"cell": coord, "formula": val})
    return formulas


def _dispatch_include_dimensions(
    ws_for_include: Any,
    include_set: set[str],
    max_style_scan_rows: int,
) -> dict[str, Any]:
    """根据 include 集合分发各维度采集，返回合并字典。"""
    extra: dict[str, Any] = {}

    if "styles" in include_set:
        extra["styles"] = _collect_styles_compressed(ws_for_include, max_rows=max_style_scan_rows)

    if "charts" in include_set:
        extra["charts"] = _collect_charts(ws_for_include)

    if "images" in include_set:
        extra["images"] = _collect_images(ws_for_include)

    if "freeze_panes" in include_set:
        extra["freeze_panes"] = _collect_freeze_panes(ws_for_include)

    if "conditional_formatting" in include_set:
        extra["conditional_formatting"] = _collect_conditional_formatting(ws_for_include)

    if "data_validation" in include_set:
        extra["data_validation"] = _collect_data_validation(ws_for_include)

    if "print_settings" in include_set:
        extra["print_settings"] = _collect_print_settings(ws_for_include)

    if "column_widths" in include_set:
        extra["column_widths"] = _collect_column_widths(ws_for_include)

    if "formulas" in include_set:
        extra["formulas"] = _collect_formulas(ws_for_include, max_rows=max_style_scan_rows)

    return extra


# ── 数值强制转换辅助 ──────────────────────────────────────


def _coerce_numeric(series: pd.Series) -> pd.Series:
    """尝试将含文本格式的数值列转换为 float。

    处理常见格式：千分位逗号 "1,234.56"、带单位后缀 "1,234.56元"、
    百分号 "16.36%"。无法转换的值保留 NaN。
    """
    if pd.api.types.is_numeric_dtype(series):
        return series

    cleaned = series.astype(str).str.strip()
    # 移除常见中文单位后缀
    cleaned = cleaned.str.replace(r'[元万亿份个台件套]$', '', regex=True)
    # 移除百分号并标记
    is_pct = cleaned.str.endswith('%')
    cleaned = cleaned.str.replace('%', '', regex=False)
    # 移除千分位逗号
    cleaned = cleaned.str.replace(',', '', regex=False)
    # 转换为数值
    result = pd.to_numeric(cleaned, errors='coerce')
    # 百分比列除以 100
    if is_pct.any() and not is_pct.all():
        # 混合格式，不做百分比转换
        pass
    elif is_pct.all():
        result = result / 100
    return result


def group_aggregate(
    file_path: str,
    group_by: str | list[str],
    aggregations: dict[str, str | list[str]] | None = None,
    sheet_name: str | None = None,
    header_row: int | None = None,
    sort_by: str | None = None,
    ascending: bool = True,
    limit: int | None = None,
) -> str:
    """按指定列分组并执行聚合统计。

    Args:
        file_path: Excel 文件路径。
        group_by: 分组列名（单个字符串或列表）。
        aggregations: 聚合配置，键为列名，值为聚合函数名或列表。
            支持的聚合函数：count, sum, mean, min, max, median, std, nunique, first, last。
            特殊值 "*" 作为键表示对所有行计数（等价于 COUNT(*)）。
        sheet_name: 工作表名称，默认第一个。
        header_row: 列头所在行号（从0开始），默认自动检测。
        sort_by: 结果排序列名，默认不排序。
        ascending: 排序方向，默认升序。
        limit: 限制返回行数，默认全部返回。

    Returns:
        JSON 格式的聚合结果。
    """
    if aggregations is None:
        return json.dumps(
            {"error": "缺少必需参数 'aggregations'，请指定聚合配置，例如: {\"销售额\": \"sum\"} 或 {\"*\": \"count\"}"},
            ensure_ascii=False,
        )

    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    df, _ = _read_df(safe_path, sheet_name, header_row=header_row)

    # 规范化 group_by 为列表
    if isinstance(group_by, str):
        group_by_cols = [group_by]
    else:
        group_by_cols = list(group_by)

    # 校验分组列
    missing_group = [c for c in group_by_cols if c not in df.columns]
    if missing_group:
        return json.dumps(
            {"error": f"分组列不存在: {missing_group}，可用列: {[str(c) for c in df.columns]}"},
            ensure_ascii=False,
            default=str,
        )

    _VALID_AGGS = {"count", "sum", "mean", "min", "max", "median", "std", "nunique", "first", "last"}
    numeric_funcs = {"sum", "mean", "min", "max", "median", "std"}
    formula_meta = df.attrs.get("formula_resolution", {})
    unresolved_formula_cols = set(formula_meta.get("unresolved_columns", [])) if isinstance(formula_meta, dict) else set()

    # 构建 pandas 聚合字典
    agg_dict: dict[str, list[str]] = {}
    has_star_count = False
    risky_formula_cols: set[str] = set()
    for col, funcs in aggregations.items():
        if col == "*":
            has_star_count = True
            continue
        if col not in df.columns:
            return json.dumps(
                {"error": f"聚合列 '{col}' 不存在，可用列: {[str(c) for c in df.columns]}"},
                ensure_ascii=False,
                default=str,
            )
        func_list = [funcs] if isinstance(funcs, str) else list(funcs)
        invalid = [f for f in func_list if f not in _VALID_AGGS]
        if invalid:
            return json.dumps(
                {"error": f"不支持的聚合函数: {invalid}，支持: {sorted(_VALID_AGGS)}"},
                ensure_ascii=False,
            )
        if col in unresolved_formula_cols and any(f in numeric_funcs for f in func_list):
            risky_formula_cols.add(col)
        # 对需要数值的聚合函数，尝试强制转换列类型
        if any(f in numeric_funcs for f in func_list):
            df[col] = _coerce_numeric(df[col])
        agg_dict[col] = func_list

    if risky_formula_cols:
        unresolved_details = formula_meta.get("unresolved_details", {}) if isinstance(formula_meta, dict) else {}
        blocked_details = {c: unresolved_details.get(c, "未解析公式列") for c in sorted(risky_formula_cols)}
        return json.dumps(
            {
                "error": "检测到未解析公式列，已阻止高风险数值聚合（避免返回误导性结果）",
                "blocked_columns": sorted(risky_formula_cols),
                "unresolved_formula_details": blocked_details,
                "suggestion": "请先在 Excel 重算并保存，或改用脚本直接计算这些列后再聚合",
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    if not agg_dict and not has_star_count:
        return json.dumps(
            {"error": "aggregations 不能为空，至少指定一个聚合列或 '*' 进行计数"},
            ensure_ascii=False,
        )

    grouped = df.groupby(group_by_cols, dropna=False)

    if agg_dict:
        result_df = grouped.agg(agg_dict)
        # 扁平化多级列名
        result_df.columns = [
            f"{col}_{func}" if len(funcs) > 1 or has_star_count else f"{col}_{func}"
            for col, funcs in agg_dict.items()
            for func in funcs
        ]
        result_df = result_df.reset_index()
    else:
        result_df = pd.DataFrame({c: [] for c in group_by_cols})

    # COUNT(*) 行计数
    if has_star_count:
        count_series = grouped.size().reset_index(name="count")
        if result_df.empty or len(result_df) == 0:
            result_df = count_series
        else:
            result_df = result_df.merge(count_series, on=group_by_cols, how="left")

    # 排序
    if sort_by and sort_by in result_df.columns:
        result_df = result_df.sort_values(by=sort_by, ascending=ascending)
    elif sort_by:
        # sort_by 可能是原始列名，尝试匹配生成的列名
        candidates = [c for c in result_df.columns if c.startswith(sort_by)]
        if candidates:
            result_df = result_df.sort_values(by=candidates[0], ascending=ascending)

    # 限制行数
    total_before_limit = len(result_df)
    if limit is not None and limit > 0:
        result_df = result_df.head(limit)

    result: dict[str, Any] = {
        "file": str(safe_path.name),
        "group_by": group_by_cols,
        "aggregations": {k: v if isinstance(v, list) else [v] for k, v in aggregations.items()},
        "total_groups": int(grouped.ngroups),
        "rows_returned": len(result_df),
        "columns": [str(c) for c in result_df.columns],
        "data": json.loads(result_df.to_json(orient="records", force_ascii=False, date_format="iso")),
    }
    if limit is not None and limit > 0 and total_before_limit > limit:
        completeness = build_completeness_meta(
            total_available=total_before_limit,
            returned=len(result_df),
            entity_name="组",
        )
        result["is_truncated"] = completeness.get("is_truncated", False)
        result["truncation_note"] = completeness.get("truncation_note", "")

    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


def _normalize_mapping_keys(series: pd.Series) -> pd.Series:
    """标准化映射键：转字符串、去首尾空格、移除空值。"""
    normalized = series.astype(str).str.strip()
    normalized = normalized[normalized.notna()]
    normalized = normalized[normalized != ""]
    normalized = normalized[normalized.str.lower() != "nan"]
    return normalized


def analyze_sheet_mapping(
    file_path: str,
    left_sheet: str,
    left_key: str,
    right_sheet: str,
    right_key: str | None = None,
    right_key_candidates: list[str] | None = None,
    left_header_row: int | None = None,
    right_header_row: int | None = None,
    max_unmatched_samples: int = 20,
) -> str:
    """分析两个工作表键字段的可映射性，输出覆盖率与未匹配样本。

    用于跨表写回前先确认映射口径是否可靠，避免误写。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    left_df, _ = _read_df(safe_path, left_sheet, header_row=left_header_row)
    if left_key not in left_df.columns:
        return json.dumps(
            {"error": f"左表字段 '{left_key}' 不存在，可用列: {[str(c) for c in left_df.columns]}"},
            ensure_ascii=False,
            default=str,
        )

    candidates: list[str] = []
    if right_key:
        candidates.append(right_key)
    if right_key_candidates:
        candidates.extend(right_key_candidates)
    # 去重且保序
    seen: set[str] = set()
    unique_candidates: list[str] = []
    for col in candidates:
        c = str(col).strip()
        if not c or c in seen:
            continue
        seen.add(c)
        unique_candidates.append(c)

    if not unique_candidates:
        return json.dumps(
            {"error": "right_key 或 right_key_candidates 至少提供一个候选字段"},
            ensure_ascii=False,
        )

    right_df, _ = _read_df(safe_path, right_sheet, header_row=right_header_row)
    left_keys = _normalize_mapping_keys(left_df[left_key])
    left_set = set(left_keys.unique().tolist())
    if not left_set:
        return json.dumps(
            {"error": f"左表字段 '{left_key}' 没有可用键值"},
            ensure_ascii=False,
        )

    analyses: list[dict[str, Any]] = []
    for candidate in unique_candidates:
        if candidate not in right_df.columns:
            analyses.append(
                {
                    "right_key": candidate,
                    "error": f"字段不存在，可用列: {[str(c) for c in right_df.columns]}",
                }
            )
            continue

        right_keys = _normalize_mapping_keys(right_df[candidate])
        right_set = set(right_keys.unique().tolist())
        matched = left_set & right_set
        unmatched_left = sorted(left_set - right_set)
        unmatched_right = sorted(right_set - left_set)

        left_cov = len(matched) / max(len(left_set), 1)
        right_cov = len(matched) / max(len(right_set), 1) if right_set else 0.0

        analyses.append(
            {
                "right_key": candidate,
                "left_unique_count": len(left_set),
                "right_unique_count": len(right_set),
                "matched_count": len(matched),
                "left_coverage": round(left_cov, 4),
                "right_coverage": round(right_cov, 4),
                "unmatched_left_samples": unmatched_left[:max_unmatched_samples],
                "unmatched_right_samples": unmatched_right[:max_unmatched_samples],
            }
        )

    valid_analyses = [a for a in analyses if "error" not in a]
    best = None
    if valid_analyses:
        best = max(
            valid_analyses,
            key=lambda x: (x["left_coverage"], x["matched_count"], -x["right_unique_count"]),
        )

    result: dict[str, Any] = {
        "file": str(safe_path.name),
        "left_sheet": left_sheet,
        "left_key": left_key,
        "right_sheet": right_sheet,
        "right_candidates": unique_candidates,
        "analysis": analyses,
    }
    if best is not None:
        result["best_candidate"] = best["right_key"]
        result["best_left_coverage"] = best["left_coverage"]
        result["mapping_recommendation"] = (
            "可自动映射"
            if best["left_coverage"] >= 0.8
            else "映射覆盖不足，建议人工确认口径"
        )
    else:
        result["mapping_recommendation"] = "候选字段均不可用"

    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


# ── get_tools() 导出 ──────────────────────────────────────



def get_tools() -> list[ToolDef]:
    """返回数据操作 Skill 的所有工具定义。"""
    return [
        ToolDef(
            name="read_excel",
            description=(
                "读取 Excel 文件并返回数据摘要（形状、列名、类型、前10行预览）。"
                "通过 include 参数可按需附加额外维度：styles（压缩样式类）、"
                "charts（图表检测）、images（图片检测）、freeze_panes（冻结窗格）、"
                "conditional_formatting（条件格式）、data_validation（数据验证）、"
                "print_settings（打印设置）、column_widths（列宽）、formulas（公式）、"
                "categorical_summary（分类列频次分布）"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名称，默认读取第一个",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "最大读取行数，默认全部",
                    },
                    "include_style_summary": {
                        "type": "boolean",
                        "description": "（已废弃，请用 include=[\"styles\"]）是否附带样式概览",
                        "default": False,
                    },
                    "header_row": {
                        "type": "integer",
                        "description": "列头所在行号（从0开始），默认自动检测。如不确定表头位置，建议不传此参数让工具自动检测，或先用 read_excel 预览数据确认表头行号",
                    },
                    "include": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "styles",
                                "charts",
                                "images",
                                "freeze_panes",
                                "conditional_formatting",
                                "data_validation",
                                "print_settings",
                                "column_widths",
                                "formulas",
                                "categorical_summary",
                            ],
                        },
                        "description": (
                            "按需请求的额外维度列表。"
                            "styles: 压缩样式类（Style Classes + 单元格映射 + 合并范围）；"
                            "charts: 嵌入图表元信息；images: 嵌入图片元信息；"
                            "freeze_panes: 冻结窗格位置；"
                            "conditional_formatting: 条件格式规则；"
                            "data_validation: 数据验证规则；"
                            "print_settings: 打印设置；"
                            "column_widths: 非默认列宽；"
                            "formulas: 含公式的单元格；"
                            "categorical_summary: 分类列的 value_counts（unique 值 < 阈值的列自动统计频次分布）"
                        ),
                    },
                    "max_style_scan_rows": {
                        "type": "integer",
                        "description": "styles/formulas 维度扫描的最大行数，默认 200",
                        "default": 200,
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            func=read_excel,
            max_result_chars=0,
        ),
        ToolDef(
            name="write_excel",
            description="将数据写入 Excel 文件。写入前必须先用 read_excel 确认目标区域现有内容；优先批量写入，避免逐行调用。",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "目标 Excel 文件路径",
                    },
                    "data": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "要写入的数据，每个对象代表一行",
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名称，默认 Sheet1",
                        "default": "Sheet1",
                    },
                },
                "required": ["file_path", "data"],
                "additionalProperties": False,
            },
            func=write_excel,
        ),
        ToolDef(
            name="analyze_data",
            description="对 Excel 数据进行基本统计分析（描述性统计、缺失值等）",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名称，默认第一个",
                    },
                    "header_row": {
                        "type": "integer",
                        "description": "列头所在行号（从0开始），默认自动检测。如不确定表头位置，建议不传此参数让工具自动检测，或先用 read_excel 预览数据确认表头行号",
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            func=analyze_data,
        ),
        ToolDef(
            name="filter_data",
            description="根据条件过滤 Excel 数据行并可选排序。支持单条件（column/operator/value）和多条件 AND/OR 组合（conditions 数组）。可通过 columns 参数只返回指定列，通过 sort_by 排序结果，通过 max_rows 限制返回行数以减少数据量。适用于排序、筛选、Top-N 等场景",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                    "column": {
                        "type": "string",
                        "description": "要过滤的列名（单条件模式，与 operator/value 配合使用）",
                    },
                    "operator": {
                        "type": "string",
                        "enum": ["eq", "ne", "gt", "ge", "lt", "le", "contains"],
                        "description": "比较运算符（单条件模式）",
                    },
                    "value": {
                        "description": "比较值（数字或字符串，单条件模式）",
                    },
                    "conditions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string", "description": "列名"},
                                "operator": {
                                    "type": "string",
                                    "enum": ["eq", "ne", "gt", "ge", "lt", "le", "contains"],
                                    "description": "比较运算符",
                                },
                                "value": {"description": "比较值"},
                            },
                            "required": ["column", "operator", "value"],
                        },
                        "description": "多条件数组，每个元素包含 column/operator/value。与单条件参数二选一",
                    },
                    "logic": {
                        "type": "string",
                        "enum": ["and", "or"],
                        "description": "多条件组合逻辑，默认 and。仅在 conditions 有多个条件时生效",
                        "default": "and",
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名称，默认第一个",
                    },
                    "header_row": {
                        "type": "integer",
                        "description": "列头所在行号（从0开始），默认自动检测。如不确定表头位置，建议不传此参数让工具自动检测，或先用 read_excel 预览数据确认表头行号",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "只返回指定列名列表（投影），默认返回全部列",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "最多返回的数据行数，默认返回全部匹配行。建议设置以控制返回数据量",
                    },
                    "sort_by": {
                        "type": "string",
                        "description": "排序列名。支持文本型数值列（如含千分位逗号、中文单位后缀）的正确排序",
                    },
                    "ascending": {
                        "type": "boolean",
                        "description": "排序方向，默认升序。设为 false 表示降序",
                        "default": True,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "排序后限制返回行数（用于 Top-N 场景），默认返回全部",
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            func=filter_data,
        ),
        ToolDef(
            name="inspect_excel_files",
            description=(
                "批量扫描目录下所有 Excel 文件，一次返回每个文件的 sheet 列表、行列数、列名和少量预览行。"
                "通过 include 可按需附加：freeze_panes、charts、images、conditional_formatting、column_widths"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "扫描目录（相对于工作目录），默认当前目录",
                        "default": ".",
                    },
                    "max_files": {
                        "type": "integer",
                        "description": "最多扫描文件数，默认 20",
                        "default": 20,
                    },
                    "preview_rows": {
                        "type": "integer",
                        "description": "每个 sheet 预览数据行数（不含标题行），默认 3",
                        "default": 3,
                    },
                    "max_columns": {
                        "type": "integer",
                        "description": "header/preview 最多展示列数，默认 15",
                        "default": 15,
                    },
                    "include": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "freeze_panes",
                                "charts",
                                "images",
                                "conditional_formatting",
                                "column_widths",
                            ],
                        },
                        "description": "按需请求的额外维度，每个文件的每个 sheet 均返回对应信息",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            func=inspect_excel_files,
            max_result_chars=0,
        ),
        ToolDef(
            name="transform_data",
            description="对 Excel 数据执行转换操作（重命名列、添加列、删除列、排序）",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "源 Excel 文件路径",
                    },
                    "operations": {
                        "type": "array",
                        "description": (
                            "转换操作列表，每项包含 type 及对应参数：\n"
                            "- sort: by（列名）+ ascending（布尔，默认 true）\n"
                            "- rename: columns（字典，{\"旧名\": \"新名\"}）\n"
                            "- add_column: name（列名）+ value（值或表达式）\n"
                            "- drop_columns: columns（列名列表）"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["rename", "add_column", "drop_columns", "sort"],
                                    "description": "操作类型：rename（重命名列）、add_column（添加列）、drop_columns（删除列）、sort（排序）",
                                },
                                "by": {
                                    "type": "string",
                                    "description": "sort 操作：排序依据的列名",
                                },
                                "ascending": {
                                    "type": "boolean",
                                    "description": "sort 操作：是否升序排列，默认 true",
                                },
                                "columns": {
                                    "description": "rename 操作时为字典 {\"旧列名\": \"新列名\"}；drop_columns 操作时为要删除的列名列表",
                                },
                                "name": {
                                    "type": "string",
                                    "description": "add_column 操作：新列的列名",
                                },
                                "value": {
                                    "description": "add_column 操作：新列的值（可以是常量或表达式）",
                                },
                            },
                            "required": ["type"],
                        },
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名称，默认第一个",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "输出文件路径，默认覆盖源文件",
                    },
                    "header_row": {
                        "type": "integer",
                        "description": "列头所在行号（从0开始），默认自动检测。如不确定表头位置，建议不传此参数让工具自动检测，或先用 read_excel 预览数据确认表头行号",
                    },
                },
                "required": ["file_path", "operations"],
                "additionalProperties": False,
            },
            func=transform_data,
        ),
        ToolDef(
            name="group_aggregate",
            description='按指定列分组并执行聚合统计（如 COUNT、SUM、MEAN 等）。仅适用于"统计每个X的Y总和/数量"类分组聚合需求，不适用于简单排序或筛选（请用 filter_data）。调用时必须提供 aggregations 参数，例如 aggregations={"销售额": "sum"}。自动处理含千分位逗号、中文单位后缀的文本型数值列',
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                    "group_by": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "分组列名（单个字符串或列表）",
                    },
                    "aggregations": {
                        "type": "object",
                        "description": (
                            "【必填】聚合配置字典，键为列名，值为聚合函数名(字符串)或函数名列表。"
                            "支持的聚合函数: count/sum/mean/min/max/median/std/nunique/first/last。"
                            "用 \"*\" 作为键表示 COUNT(*) 行计数。"
                            "示例: {\"销售额\": \"sum\", \"单价\": \"mean\"} 或 {\"*\": \"count\"} 或 {\"金额\": [\"sum\", \"mean\"]}"
                        ),
                        "additionalProperties": {
                            "oneOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}},
                            ],
                        },
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名称，默认第一个",
                    },
                    "header_row": {
                        "type": "integer",
                        "description": "列头所在行号（从0开始），默认自动检测。如不确定表头位置，建议不传此参数让工具自动检测，或先用 read_excel 预览数据确认表头行号",
                    },
                    "sort_by": {
                        "type": "string",
                        "description": "结果排序列名（可用聚合后的列名如 '总金额(元)_sum'，或原始列名自动匹配）",
                    },
                    "ascending": {
                        "type": "boolean",
                        "description": "排序方向，默认升序。设为 false 表示降序",
                        "default": True,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "限制返回行数，默认全部返回",
                    },
                },
                "required": ["file_path", "group_by", "aggregations"],
                "additionalProperties": False,
            },
            func=group_aggregate,
        ),
        ToolDef(
            name="analyze_sheet_mapping",
            description="分析两个工作表键字段的映射覆盖率，输出最佳映射候选和未匹配样本。适合跨表写回前做口径校验",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径",
                    },
                    "left_sheet": {
                        "type": "string",
                        "description": "左表工作表名称（待写回来源）",
                    },
                    "left_key": {
                        "type": "string",
                        "description": "左表映射键字段名",
                    },
                    "right_sheet": {
                        "type": "string",
                        "description": "右表工作表名称（目标维表）",
                    },
                    "right_key": {
                        "type": "string",
                        "description": "右表映射键字段名（单候选）",
                    },
                    "right_key_candidates": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "右表候选键字段名列表（多候选自动评分）",
                    },
                    "left_header_row": {
                        "type": "integer",
                        "description": "左表表头行号（从0开始），默认自动检测。如不确定表头位置，建议不传此参数让工具自动检测，或先用 read_excel 预览数据确认表头行号",
                    },
                    "right_header_row": {
                        "type": "integer",
                        "description": "右表表头行号（从0开始），默认自动检测。如不确定表头位置，建议不传此参数让工具自动检测，或先用 read_excel 预览数据确认表头行号",
                    },
                    "max_unmatched_samples": {
                        "type": "integer",
                        "description": "未匹配样本最多返回条数，默认20",
                        "default": 20,
                    },
                },
                "required": ["file_path", "left_sheet", "left_key", "right_sheet"],
                "additionalProperties": False,
            },
            func=analyze_sheet_mapping,
        ),
    ]
