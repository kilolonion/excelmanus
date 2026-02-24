"""VLM 视觉描述模块：B 通道描述 + 结构化提取。

B+C 混合架构中：
- B 通道：小 VLM 生成文字描述 → 注入主模型上下文
- C 通道：图片直接注入主模型视觉上下文（由 read_image 处理）
- 结构化提取：VLM 直接输出 JSON → 后处理为 ReplicaSpec
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 公共 API
# ════════════════════════════════════════════════════════════════


def build_describe_prompt() -> str:
    """构建 B 通道 VLM 描述 prompt。

    要求小视觉模型输出 Markdown 表格 + 自然语言布局描述，
    不要求结构化 JSON，降低格式遵循压力。
    """
    return _DESCRIBE_PROMPT


def build_extract_data_prompt() -> str:
    """构建 Phase 1 结构化提取 prompt（数据+结构）。"""
    return _EXTRACT_DATA_PROMPT


def build_extract_style_prompt(table_summary: str) -> str:
    """构建 Phase 2 样式提取 prompt。

    Args:
        table_summary: Phase 1 提取结果的摘要（表名、维度、区域），
                       帮助 VLM 定位样式描述的目标区域。
    """
    return _EXTRACT_STYLE_PROMPT_TEMPLATE.format(table_summary=table_summary)


# ════════════════════════════════════════════════════════════════
# 语义颜色映射
# ════════════════════════════════════════════════════════════════

# Excel 标准调色板语义映射（常见表格配色）
SEMANTIC_COLOR_MAP: dict[str, str] = {
    # 蓝色系
    "dark_blue": "#1F4E79",
    "blue": "#4472C4",
    "light_blue": "#BDD7EE",
    "pale_blue": "#D6E4F0",
    # 绿色系
    "dark_green": "#375623",
    "green": "#70AD47",
    "light_green": "#C6EFCE",
    # 红色系
    "dark_red": "#843C0C",
    "red": "#FF0000",
    "light_red": "#FFC7CE",
    # 橙/黄
    "orange": "#ED7D31",
    "yellow": "#FFC000",
    "light_yellow": "#FFEB9C",
    # 灰色系
    "black": "#000000",
    "dark_gray": "#404040",
    "gray": "#808080",
    "light_gray": "#D9D9D9",
    "white": "#FFFFFF",
    # 紫色
    "purple": "#7030A0",
    "light_purple": "#CCC0DA",
    # 无填充
    "none": "",
    "transparent": "",
}


def resolve_semantic_color(value: str | None) -> str | None:
    """将语义颜色名解析为 hex 值。

    - 已经是 hex → 直接透传
    - 语义名 → 查表返回 hex
    - 未知名称 → 返回 None
    """
    if value is None:
        return None
    value = value.strip()
    # 已经是 hex → 直接透传
    if re.match(r'^#[0-9A-Fa-f]{6}$', value):
        return value
    # 语义名 → 查表
    normalized = value.lower().replace(" ", "_").replace("-", "_")
    mapped = SEMANTIC_COLOR_MAP.get(normalized)
    if mapped is not None:
        return mapped if mapped else None
    return None


# ════════════════════════════════════════════════════════════════
# B 通道 Prompt — 自然语言 + Markdown 表格描述
# ════════════════════════════════════════════════════════════════

_DESCRIBE_PROMPT = """\
你是一个精确的表格视觉描述引擎。请仔细观察图片中的表格，输出以下信息。

## 1. 概览
- 表格用途/标题（如果有）
- 总行数、总列数（用 Excel 列号标注，如 A-H 共 8 列）

## 2. 逐行结构描述
对每一行，描述：
- 该行的功能（标题行 / 标签行 / 数据行 / 合计行 / 签名行 / 空行）
- 每个单元格的精确内容（文字/数字，保留原始精度：12.50 不写成 12.5）
- 合并情况：明确标注哪些单元格是合并的（如 "A1:H1 合并"），哪些是独立的
- 特别注意：同一行内多组"标签:值"对（如 日期:2024-01-15 / 客户:张三）是各自独立的单元格，**不要**描述为整行合并

## 3. 数据区 Markdown 表格
用标准 Markdown 表格输出数据区内容（不含标题行、签名行等非数据行）

## 4. 样式特征
- 各区域的背景色（用颜色名或 hex 值）
- 字体特征（加粗、字号、颜色）
- 对齐方式（水平：左/中/右）
- 边框样式（细线/粗线/无边框）
- 各列的相对宽度比例（如 A列较窄、B列中等、C列较宽）

## 5. 不确定项
看不清或模糊的内容用 [?] 标注，并说明可能的候选值

**严格禁止：**
- 不要编造任何数据——看不清就标 [?]
- 不要改变数字精度（12.50 不写成 12.5，1,200 不写成 1200）
- 不要遗漏任何行或列"""


# ════════════════════════════════════════════════════════════════
# Phase 1 Prompt — 结构化数据提取（JSON）
# ════════════════════════════════════════════════════════════════

_EXTRACT_DATA_PROMPT = """\
你是一个精确的表格结构提取引擎。请仔细观察图片，提取所有表格的结构和数据。

**如果图片中有多个独立表格**（被空白、标题或明显间隔分隔），请分别提取为 tables 数组中的不同元素。

请严格输出以下 JSON 格式（不要输出任何其他内容）：

```json
{
  "tables": [
    {
      "name": "Sheet1",
      "title": "表格标题（如果有）",
      "dimensions": {"rows": 行数, "cols": 列数},
      "header_rows": [1],
      "total_rows": [],
      "cells": [
        {"addr": "A1", "val": "单元格值", "type": "string"},
        {"addr": "B2", "val": 1200.50, "type": "number", "display": "$1,200.50"}
      ],
      "merges": ["A1:E1", "A10:C10"],
      "col_widths": [15, 10, 12],
      "uncertainties": [
        {"addr": "C5", "reason": "数字模糊", "candidates": ["350", "850"]}
      ]
    }
  ]
}
```

字段说明：
- addr: Excel 单元格地址（如 A1, B2）
- val: 单元格的实际值（数字用数字类型，文字用字符串）
- type: "string" | "number" | "date" | "boolean" | "formula" | "empty"
- display: 可选，单元格的显示文本（如带货币符号、千分位的数字）
- merges: 合并单元格范围列表
- col_widths: 各列的估计宽度（Excel 字符单位，通常 8-25）
- header_rows: 表头行号列表（1-based）
- total_rows: 合计行号列表（1-based）
- uncertainties: 不确定项（看不清的内容）

**严格要求：**
- 不要编造数据——看不清就在 uncertainties 中标注
- 保留原始数字精度（12.50 不写成 12.5）
- 不要遗漏任何行或列
- 每个独立表格用单独的 tables 元素表示
- 如果只有一个表格，tables 数组也只有一个元素"""


# ════════════════════════════════════════════════════════════════
# Phase 2 Prompt — 样式提取（JSON）
# ════════════════════════════════════════════════════════════════

_EXTRACT_STYLE_PROMPT_TEMPLATE = """\
你是一个精确的表格样式提取引擎。请观察图片中表格的视觉样式。

以下是已提取的表格结构摘要：
{table_summary}

请严格输出以下 JSON 格式（不要输出任何其他内容）：

```json
{{
  "default_font": {{"name": "字体名", "size": 11}},
  "styles": {{
    "header": {{
      "font": {{"bold": true, "size": 12, "color": "white", "name": "字体名"}},
      "fill": {{"color": "dark_blue"}},
      "alignment": {{"horizontal": "center"}},
      "border": {{"style": "thin", "color": "black"}}
    }},
    "data": {{
      "border": {{"style": "thin", "color": "light_gray"}}
    }}
  }},
  "cell_styles": {{
    "A1:E1": "header",
    "A2:E9": "data"
  }},
  "row_heights": {{"1": 28}}
}}
```

字段说明：
- default_font: 表格的默认字体（大多数单元格使用的字体）
- styles: 样式类定义，每个样式有唯一 ID
- cell_styles: 单元格范围 → 样式 ID 的映射（用 Excel 范围表示，如 "A1:E1"）
- row_heights: 行号 → 行高的映射（仅非默认行高的行）
- 颜色可以用语义名（dark_blue, light_gray, white 等）或 hex 值（#1F4E79）

**注意：**
- 只描述你能看到的样式，不要猜测
- 如果所有单元格样式相同，可以只定义一个 "default" 样式"""


# ════════════════════════════════════════════════════════════════
# 后处理：VLM 提取结果 → ReplicaSpec
# ════════════════════════════════════════════════════════════════


def postprocess_extraction_to_spec(
    data_json: dict[str, Any],
    style_json: dict[str, Any] | None,
    provenance: dict[str, Any],
) -> "ReplicaSpec":
    """将 VLM 两阶段提取结果合并为完整 ReplicaSpec。

    Args:
        data_json: Phase 1 输出（tables 数组）
        style_json: Phase 2 输出（styles + cell_styles），可为 None
        provenance: 溯源信息 dict
    """
    from excelmanus.replica_spec import (
        AlignmentSpec,
        BorderSpec,
        CellSpec,
        FontSpec,
        MergedRange,
        Provenance,
        ReplicaSpec,
        SemanticHints,
        SheetSpec,
        StyleClass,
        Uncertainty,
        WorkbookSpec,
        FillSpec,
    )

    tables = data_json.get("tables") or []
    if not tables:
        raise ValueError("VLM 提取结果中没有 tables 数据")

    # ── 解析样式 ──
    style_defs: dict[str, dict] = {}
    cell_style_map: dict[str, str] = {}  # "A1:E1" → style_id
    row_heights_global: dict[str, float] = {}
    default_font_spec: FontSpec | None = None

    if style_json:
        style_defs = style_json.get("styles") or {}
        cell_style_map = style_json.get("cell_styles") or {}
        row_heights_global = style_json.get("row_heights") or {}
        df = style_json.get("default_font")
        if isinstance(df, dict):
            default_font_spec = FontSpec(
                name=df.get("name"), size=df.get("size"),
            )

    # ── 构建 StyleClass 对象 ──
    def _build_style_class(raw: dict) -> StyleClass:
        font = None
        if raw_font := raw.get("font"):
            font = FontSpec(
                name=raw_font.get("name"),
                size=raw_font.get("size"),
                bold=raw_font.get("bold"),
                italic=raw_font.get("italic"),
                color=resolve_semantic_color(raw_font.get("color")),
            )
        fill = None
        if raw_fill := raw.get("fill"):
            color = resolve_semantic_color(raw_fill.get("color"))
            if color:
                fill = FillSpec(type="solid", color=color)
        alignment = None
        if raw_align := raw.get("alignment"):
            alignment = AlignmentSpec(
                horizontal=raw_align.get("horizontal"),
                vertical=raw_align.get("vertical"),
                wrap_text=raw_align.get("wrap_text"),
            )
        border = None
        if raw_border := raw.get("border"):
            border = BorderSpec(
                style=raw_border.get("style"),
                color=resolve_semantic_color(raw_border.get("color")),
            )
        return StyleClass(font=font, fill=fill, alignment=alignment, border=border)

    compiled_styles: dict[str, StyleClass] = {
        sid: _build_style_class(sraw) for sid, sraw in style_defs.items()
    }

    # ── 展开范围样式映射为 per-cell ──
    cell_to_style: dict[str, str] = {}
    for range_str, style_id in cell_style_map.items():
        for addr in _expand_range(range_str):
            cell_to_style[addr.upper()] = style_id

    # ── 构建 SheetSpec 列表 ──
    sheets: list[SheetSpec] = []
    all_uncertainties: list[Uncertainty] = []

    for idx, table in enumerate(tables):
        name = table.get("name") or table.get("title") or f"Table{idx + 1}"
        dims = table.get("dimensions") or {"rows": 0, "cols": 0}

        # 转换 cells
        cells: list[CellSpec] = []
        for raw_cell in table.get("cells") or []:
            addr = raw_cell.get("addr", "")
            if not _is_valid_address(addr):
                all_uncertainties.append(Uncertainty(
                    location=addr or "unknown",
                    reason=f"无效的单元格地址: {addr!r}",
                ))
                continue
            val = raw_cell.get("val")
            vtype = raw_cell.get("type", "string")
            display = raw_cell.get("display")
            # 推断 number_format
            nf = None
            if display and vtype == "number" and val is not None:
                nf = _infer_number_format_from_display(display)
            cells.append(CellSpec(
                address=addr,
                value=val,
                value_type=vtype,
                display_text=display,
                number_format=nf,
                style_id=cell_to_style.get(addr.upper()),
                confidence=1.0,
            ))

        # 转换 merges
        merges = [MergedRange(range=m) for m in (table.get("merges") or [])]

        # 语义提示
        hints = SemanticHints(
            header_rows=table.get("header_rows") or [],
            total_rows=table.get("total_rows") or [],
        )

        # 不确定项
        for u in table.get("uncertainties") or []:
            all_uncertainties.append(Uncertainty(
                location=u.get("addr", "unknown"),
                reason=u.get("reason", ""),
                candidate_values=u.get("candidates") or [],
                confidence=0.5,
            ))

        sheets.append(SheetSpec(
            name=name,
            dimensions=dims,
            cells=cells,
            merged_ranges=merges,
            styles=compiled_styles,
            column_widths=table.get("col_widths") or [],
            row_heights=row_heights_global,
            semantic_hints=hints,
        ))

    workbook = WorkbookSpec(
        name="replica",
        default_font=default_font_spec,
    )

    return ReplicaSpec(
        version="1.0",
        provenance=Provenance(**provenance),
        workbook=workbook,
        sheets=sheets,
        uncertainties=all_uncertainties,
    )


# ── 辅助函数 ──

_ADDR_RE = re.compile(r'^[A-Z]{1,3}\d+$', re.IGNORECASE)


def _is_valid_address(addr: str) -> bool:
    """检查是否为合法的 Excel 单元格地址。"""
    return bool(addr and _ADDR_RE.match(addr.strip()))


def _expand_range(range_str: str) -> list[str]:
    """将 Excel 范围（如 'A1:C3'）展开为单元格地址列表。"""
    from openpyxl.utils import get_column_letter, range_boundaries

    range_str = range_str.strip()
    if ":" not in range_str:
        return [range_str]
    try:
        min_col, min_row, max_col, max_row = range_boundaries(range_str)
        addrs = []
        for r in range(min_row, max_row + 1):
            for c in range(min_col, max_col + 1):
                addrs.append(f"{get_column_letter(c)}{r}")
        return addrs
    except Exception:
        return [range_str]


def _infer_number_format_from_display(display_text: str) -> str | None:
    """从 display_text 推断 number_format（复用 image_tools 的逻辑）。"""
    try:
        from excelmanus.tools.image_tools import _infer_number_format
        return _infer_number_format(display_text)
    except ImportError:
        return None


def build_table_summary(data_json: dict[str, Any]) -> str:
    """从 Phase 1 结果生成摘要文本，供 Phase 2 prompt 使用。"""
    lines = []
    for idx, table in enumerate(data_json.get("tables") or []):
        name = table.get("name") or f"Table{idx + 1}"
        dims = table.get("dimensions") or {}
        rows = dims.get("rows", "?")
        cols = dims.get("cols", "?")
        cell_count = len(table.get("cells") or [])
        merge_count = len(table.get("merges") or [])
        lines.append(
            f"- {name}: {rows}行×{cols}列, {cell_count}个单元格, "
            f"{merge_count}个合并区域"
        )
    return "\n".join(lines) if lines else "（无表格信息）"
