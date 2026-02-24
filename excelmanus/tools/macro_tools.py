"""Macro 工具：高频声明式复合操作，内部调用 pandas/openpyxl 直接执行。

LLM 只需传结构化 JSON 参数，无需手写 Python 代码。
所有写入操作经过 CowWriter 保护层（路径校验 + bench CoW + 原子写入）。
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from excelmanus.security import FileAccessGuard
from excelmanus.tools._guard_ctx import get_guard as _get_ctx_guard
from excelmanus.security.cow_writer import CowWriter
from excelmanus.tools.registry import ToolDef

# ── 模块级 FileAccessGuard（延迟初始化） ─────────────────

_guard: FileAccessGuard | None = None


def _get_guard() -> FileAccessGuard:
    """获取或创建 FileAccessGuard（优先 per-session contextvar）。"""
    ctx_guard = _get_ctx_guard()
    if ctx_guard is not None:
        return ctx_guard
    global _guard
    if _guard is None:
        _guard = FileAccessGuard(".")
    return _guard


def init_guard(workspace_root: str) -> None:
    global _guard
    _guard = FileAccessGuard(workspace_root)


def _ok(message: str, *, details: dict | None = None, cow_mapping: dict | None = None) -> str:
    result: dict[str, Any] = {"status": "success", "message": message}
    if details:
        result["details"] = details
    if cow_mapping:
        result["cow_mapping"] = cow_mapping
    return json.dumps(result, ensure_ascii=False, indent=2)


def _err(message: str, *, error_type: str = "validation", details: dict | None = None) -> str:
    result: dict[str, Any] = {"status": "error", "error_type": error_type, "message": message}
    if details:
        result["details"] = details
    return json.dumps(result, ensure_ascii=False, indent=2)


# ── vlookup_write ─────────────────────────────────────────


def vlookup_write(
    file_path: str,
    source_sheet: str,
    source_key: str,
    source_values: str | list[str],
    target_sheet: str,
    target_key: str,
    output_columns: str | list[str] | None = None,
    agg_func: str = "first",
    header_row: int | None = None,
) -> str:
    """跨表匹配写回：从源表查找/聚合数据，写入目标表新列。"""
    # ── 参数规范化 ──
    if isinstance(source_values, str):
        source_values = [source_values]
    if output_columns is None:
        output_columns = list(source_values)
    elif isinstance(output_columns, str):
        output_columns = [output_columns]
    if len(output_columns) != len(source_values):
        return _err(
            f"output_columns 长度 ({len(output_columns)}) 与 source_values 长度 ({len(source_values)}) 不匹配"
        )

    valid_agg = {"first", "sum", "mean", "count", "max", "min"}
    if agg_func not in valid_agg:
        return _err(f"不支持的聚合函数: {agg_func}，可选: {sorted(valid_agg)}")

    writer = CowWriter(_get_guard())

    try:
        target = writer.resolve(file_path)
    except Exception as e:
        return _err(f"文件路径错误: {e}", error_type="io")

    # ── 读取数据 ──
    read_kwargs: dict[str, Any] = {}
    if header_row is not None:
        read_kwargs["header"] = header_row - 1  # 用户传 1-indexed

    try:
        src_df = pd.read_excel(target, sheet_name=source_sheet, **read_kwargs)
    except Exception as e:
        return _err(f"读取源表 '{source_sheet}' 失败: {e}", error_type="io")

    try:
        tgt_df = pd.read_excel(target, sheet_name=target_sheet, **read_kwargs)
    except Exception as e:
        return _err(f"读取目标表 '{target_sheet}' 失败: {e}", error_type="io")

    # ── 校验列名 ──
    src_cols = list(src_df.columns)
    tgt_cols = list(tgt_df.columns)

    if source_key not in src_cols:
        return _err(
            f"源表 '{source_sheet}' 中不存在列 '{source_key}'",
            details={"available_columns": src_cols},
        )
    for sv in source_values:
        if sv not in src_cols:
            return _err(
                f"源表 '{source_sheet}' 中不存在列 '{sv}'",
                details={"available_columns": src_cols},
            )
    if target_key not in tgt_cols:
        return _err(
            f"目标表 '{target_sheet}' 中不存在列 '{target_key}'",
            details={"available_columns": tgt_cols},
        )

    # ── 聚合 ──
    try:
        if agg_func == "first":
            src_agg = src_df.drop_duplicates(subset=[source_key], keep="first")[
                [source_key] + source_values
            ]
        else:
            src_agg = (
                src_df.groupby(source_key, as_index=False)[source_values]
                .agg(agg_func)
            )
    except Exception as e:
        return _err(f"聚合失败: {e}", error_type="data")

    # ── 键类型对齐 ──
    type_warning = ""
    src_key_dtype = src_agg[source_key].dtype
    tgt_key_dtype = tgt_df[target_key].dtype
    if src_key_dtype != tgt_key_dtype:
        src_agg[source_key] = src_agg[source_key].astype(str)
        tgt_df[target_key] = tgt_df[target_key].astype(str)
        type_warning = f"（键列类型不一致 {src_key_dtype} vs {tgt_key_dtype}，已自动转换为字符串匹配）"

    # ── 合并 ──
    rename_map = dict(zip(source_values, output_columns))
    src_agg = src_agg.rename(columns=rename_map)
    merge_cols = [source_key] + output_columns

    merged = tgt_df.merge(
        src_agg[merge_cols],
        left_on=target_key,
        right_on=source_key,
        how="left",
        suffixes=("", "_vlookup_dup"),
    )

    # 如果 source_key != target_key，删除多余的 source_key 列
    if source_key != target_key and source_key in merged.columns:
        dup_col = source_key + "_vlookup_dup"
        if dup_col in merged.columns:
            merged = merged.drop(columns=[dup_col])
        elif source_key in merged.columns and source_key not in tgt_cols:
            merged = merged.drop(columns=[source_key])

    # 清理 suffixed 重复列
    dup_cols = [c for c in merged.columns if c.endswith("_vlookup_dup")]
    if dup_cols:
        merged = merged.drop(columns=dup_cols)

    # ── 统计 ──
    total = len(merged)
    null_counts = {col: int(merged[col].isna().sum()) for col in output_columns}
    matched = total - max(null_counts.values()) if null_counts else total

    # ── 写回 ──
    try:
        writer.atomic_save_dataframe(merged, target, target_sheet)
    except Exception as e:
        return _err(f"写回失败: {e}", error_type="io")

    # ── 返回 ──
    message = (
        f"已将 {len(source_values)} 列从 '{source_sheet}' 匹配写入 '{target_sheet}'"
        f"（匹配 {matched}/{total} 行，聚合方式: {agg_func}）"
    )
    if type_warning:
        message += type_warning

    sample_rows = merged[output_columns].head(5).to_dict(orient="records")

    return _ok(
        message,
        details={
            "total_rows": total,
            "matched_rows": matched,
            "null_counts": null_counts,
            "output_columns": output_columns,
            "sample_rows": sample_rows,
        },
        cow_mapping=writer.cow_mapping or None,
    )


# ── computed_column ───────────────────────────────────────

# AST 白名单函数，用于安全执行表达式
import ast
import datetime
import operator

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}

_SAFE_COMPARE = {
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}


class _ExpressionError(Exception):
    """表达式解析/执行错误。"""


def _parse_expression(expr: str, df: pd.DataFrame) -> Any:
    """安全解析并执行列表达式。

    支持的语法:
    - col('列名') → 引用列
    - today() → 当前日期
    - where(cond, true_val, false_val) → 条件判断
    - 算术/比较运算符
    - round(), abs(), len(), int(), float(), str()
    - .dt.days, .dt.total_seconds(), .str.strip() 等
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise _ExpressionError(f"表达式语法错误: {e}") from e

    # 提取引用的列名并校验
    col_refs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "col":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                col_refs.append(node.args[0].value)

    available = list(df.columns)
    for ref in col_refs:
        if ref not in available:
            raise _ExpressionError(
                f"列 '{ref}' 不存在，可用列: {available}"
            )

    # 构建安全执行环境
    import numpy as np

    safe_env = {
        "col": lambda name: df[name],
        "today": lambda: pd.Timestamp(datetime.date.today()),
        "where": lambda cond, true_val, false_val: np.where(cond, true_val, false_val),
        "round": round,
        "abs": abs,
        "len": len,
        "int": int,
        "float": float,
        "str": str,
        "pd": pd,
        "np": np,
    }

    # 安全性检查：禁止 import、lambda、函数定义
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise _ExpressionError("表达式中不允许使用 import")
        if isinstance(node, ast.Lambda):
            raise _ExpressionError("表达式中不允许使用 lambda")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            raise _ExpressionError("表达式中不允许定义函数")
        if isinstance(node, ast.Name) and node.id == "__import__":
            raise _ExpressionError("表达式中不允许使用 __import__")
        # 限制 attribute 访问：仅允许 .dt .str .values 等 pandas 常用属性
        if isinstance(node, ast.Attribute):
            allowed_attrs = {
                "dt", "str", "values", "days", "total_seconds",
                "strip", "upper", "lower", "contains", "replace",
                "startswith", "endswith", "len", "cat",
                "year", "month", "day", "hour", "minute", "second",
                "astype", "fillna", "round", "abs", "clip",
            }
            if node.attr not in allowed_attrs:
                raise _ExpressionError(
                    f"不允许访问属性 '.{node.attr}'，"
                    f"允许的属性: {sorted(allowed_attrs)}"
                )

    # 执行
    try:
        compiled = compile(tree, "<expression>", "eval")
        result = eval(compiled, {"__builtins__": {}}, safe_env)  # noqa: S307
    except _ExpressionError:
        raise
    except Exception as e:
        raise _ExpressionError(f"表达式执行错误: {type(e).__name__}: {e}") from e

    return result


def computed_column(
    file_path: str,
    sheet_name: str,
    column_name: str,
    expression: str,
    output_type: str | None = None,
    header_row: int | None = None,
) -> str:
    """新增计算列：用声明式表达式计算新列值并写回。"""
    writer = CowWriter(_get_guard())

    try:
        target = writer.resolve(file_path)
    except Exception as e:
        return _err(f"文件路径错误: {e}", error_type="io")

    read_kwargs: dict[str, Any] = {}
    if header_row is not None:
        read_kwargs["header"] = header_row - 1

    try:
        df = pd.read_excel(target, sheet_name=sheet_name, **read_kwargs)
    except Exception as e:
        return _err(f"读取工作表 '{sheet_name}' 失败: {e}", error_type="io")

    # ── 执行表达式 ──
    try:
        result = _parse_expression(expression, df)
    except _ExpressionError as e:
        return _err(str(e), error_type="validation")

    # ── 赋值 ──
    df[column_name] = result

    # ── 类型转换 ──
    type_note = ""
    if output_type:
        try:
            if output_type == "number":
                df[column_name] = pd.to_numeric(df[column_name], errors="coerce")
            elif output_type == "date":
                df[column_name] = pd.to_datetime(df[column_name], errors="coerce")
            elif output_type == "text":
                df[column_name] = df[column_name].astype(str)
            elif output_type == "timedelta":
                pass  # timedelta 通常由日期差生成，不需要转换
            else:
                type_note = f"（未知的 output_type '{output_type}'，已忽略）"
        except Exception as e:
            type_note = f"（类型转换警告: {e}）"

    # ── 写回 ──
    try:
        writer.atomic_save_dataframe(df, target, sheet_name)
    except Exception as e:
        return _err(f"写回失败: {e}", error_type="io")

    # ── 返回 ──
    dtype_str = str(df[column_name].dtype)
    null_count = int(df[column_name].isna().sum())
    sample_values = df[column_name].head(5).tolist()
    # 处理不可 JSON 序列化的值
    safe_sample = []
    for v in sample_values:
        if pd.isna(v):
            safe_sample.append(None)
        elif hasattr(v, "isoformat"):
            safe_sample.append(v.isoformat())
        elif hasattr(v, "item"):
            safe_sample.append(v.item())
        else:
            safe_sample.append(v)

    message = f"已在 '{sheet_name}' 新增列 '{column_name}'（{dtype_str}，{len(df)} 行）"
    if type_note:
        message += type_note

    return _ok(
        message,
        details={
            "column_name": column_name,
            "dtype": dtype_str,
            "total_rows": len(df),
            "null_count": null_count,
            "sample_values": safe_sample,
        },
        cow_mapping=writer.cow_mapping or None,
    )


# ── get_tools() 导出 ──────────────────────────────────────


def get_tools() -> list[ToolDef]:
    """返回 Macro 工具的所有工具定义。"""
    return [
        ToolDef(
            name="vlookup_write",
            description=(
                "跨表匹配写回：从源表查找或聚合数据，写入目标表新列。"
                "类似 VLOOKUP/INDEX-MATCH 但支持聚合（sum/mean/count 等）。"
                "适用于：产品销售额写入产品目录、部门人数统计等跨表操作"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径（相对于工作目录）",
                    },
                    "source_sheet": {
                        "type": "string",
                        "description": "源表（数据来源）的工作表名",
                    },
                    "source_key": {
                        "type": "string",
                        "description": "源表中用于匹配的键列名",
                    },
                    "source_values": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "源表中要提取的值列名（单个或多个）",
                    },
                    "target_sheet": {
                        "type": "string",
                        "description": "目标表（写入目标）的工作表名",
                    },
                    "target_key": {
                        "type": "string",
                        "description": "目标表中用于匹配的键列名",
                    },
                    "output_columns": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "写入目标表的新列名（可选，默认同 source_values）",
                    },
                    "agg_func": {
                        "type": "string",
                        "enum": ["first", "sum", "mean", "count", "max", "min"],
                        "description": "聚合函数：first（取第一条）、sum、mean、count、max、min。默认 first",
                        "default": "first",
                    },
                    "header_row": {
                        "type": "integer",
                        "description": "表头行号（1-indexed），默认自动检测。建议不传，由 read_excel 自动检测后参考",
                    },
                },
                "required": ["file_path", "source_sheet", "source_key", "source_values", "target_sheet", "target_key"],
                "additionalProperties": False,
            },
            func=vlookup_write,
            write_effect="workspace_write",
        ),
        ToolDef(
            name="computed_column",
            description=(
                "新增计算列：用声明式表达式计算新列并写回工作表。"
                "表达式语法：col('列名') 引用列，today() 当前日期，where(条件,真值,假值) 条件判断。"
                "示例：col('实付金额') * 0.3、(today() - col('入职日期')).dt.days / 365"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Excel 文件路径（相对于工作目录）",
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名",
                    },
                    "column_name": {
                        "type": "string",
                        "description": "新列的列名",
                    },
                    "expression": {
                        "type": "string",
                        "description": (
                            "Python 表达式。用 col('列名') 引用列，today() 获取当前日期，"
                            "where(条件, 真值, 假值) 做条件判断。"
                            "示例：col('金额') * 0.3、(today() - col('入职日期')).dt.days / 365"
                        ),
                    },
                    "output_type": {
                        "type": "string",
                        "enum": ["number", "date", "text", "timedelta"],
                        "description": "输出列类型（可选，默认自动推断）",
                    },
                    "header_row": {
                        "type": "integer",
                        "description": "表头行号（1-indexed），默认自动检测",
                    },
                },
                "required": ["file_path", "sheet_name", "column_name", "expression"],
                "additionalProperties": False,
            },
            func=computed_column,
            write_effect="workspace_write",
        ),
    ]
