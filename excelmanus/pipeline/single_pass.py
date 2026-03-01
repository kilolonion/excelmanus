"""单轮合并提取：强视觉模型一次 VLM 调用完成结构+数据+样式。

适用于强模型（Gemini 2.5 Pro 级别）+ 中小表格场景，
将 4 阶段 Pipeline 的 40-120s 压缩到 10-20s。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from excelmanus.replica_spec import (
    AlignmentSpec,
    BorderSpec,
    CellSpec,
    FillSpec,
    FontSpec,
    MergedRange,
    Provenance,
    ReplicaSpec,
    SemanticHints,
    SheetSpec,
    StyleClass,
    Uncertainty,
)
from excelmanus.vision_extractor import resolve_semantic_color

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# 单轮合并提取 Prompt
# ════════════════════════════════════════════════════════════════

SINGLE_PASS_PROMPT = """\
你是一个精确的表格提取引擎。请仔细观察图片中的表格，一次性提取完整的结构、数据和样式。

如果图片中有多个独立表格，请分别提取为 tables 数组中的不同元素。

请严格输出以下 JSON 格式（不要输出任何其他内容）：

```json
{
  "tables": [
    {
      "name": "Sheet1",
      "title": "表格标题（如有）",
      "dimensions": {"rows": 行数, "cols": 列数},
      "header_rows": [1],
      "total_rows": [],
      "merges": ["A1:E1"],
      "col_widths": [15, 10, 12],
      "row_heights": {},
      "cells": [
        {"addr": "A1", "val": "标题", "type": "string", "display": "标题", "style_id": "s1"},
        {"addr": "B2", "val": 1200.50, "type": "number", "display": "$1,200.50", "style_id": "s2"}
      ],
      "styles": {
        "s1": {
          "font": {"name": "宋体", "size": 14, "bold": true, "color": "#000000"},
          "fill": {"color": "#4472C4"},
          "alignment": {"horizontal": "center", "vertical": "center", "wrap_text": false},
          "border": {"style": "thin", "color": "#000000"}
        },
        "s2": {
          "font": {"size": 11},
          "alignment": {"horizontal": "right"}
        }
      },
      "uncertainties": [
        {"addr": "C5", "reason": "数字模糊", "candidates": ["350", "850"]}
      ]
    }
  ]
}
```

字段说明：
- dimensions: 表格的行列数
- header_rows: 表头行号列表（1-based）
- merges: 合并单元格范围列表
- col_widths: 各列的估计宽度（Excel 字符单位，通常 8-25）
- cells: 所有单元格数据
  - addr: Excel 地址（如 A1）
  - val: 实际值（数字用数字类型，文字用字符串）
  - type: "string" | "number" | "date" | "boolean" | "formula" | "empty"
  - display: 可选，显示文本（如带货币符号的数字）
  - style_id: 可选，引用 styles 中的样式 ID
- styles: 样式 class 定义（相同样式共享 style_id，避免重复）
- uncertainties: 不确定项

**严格要求：**
- 一次提取所有信息：结构、数据、样式
- 不要编造数据——看不清就在 uncertainties 中标注
- 保留原始数字精度（12.50 不写成 12.5）
- 不要遗漏任何行或列
- 样式用 style class 去重（相同样式共享 style_id）
- 颜色用 hex 值（如 #FF0000）或语义名（如 dark_blue）"""

SINGLE_PASS_NO_STYLE_PROMPT = """\
你是一个精确的表格提取引擎。请仔细观察图片中的表格，一次性提取完整的结构和数据（不需要样式）。

如果图片中有多个独立表格，请分别提取。

请严格输出以下 JSON 格式（不要输出任何其他内容）：

```json
{
  "tables": [
    {
      "name": "Sheet1",
      "title": "表格标题（如有）",
      "dimensions": {"rows": 行数, "cols": 列数},
      "header_rows": [1],
      "total_rows": [],
      "merges": ["A1:E1"],
      "col_widths": [15, 10, 12],
      "cells": [
        {"addr": "A1", "val": "标题", "type": "string"},
        {"addr": "B2", "val": 1200.50, "type": "number", "display": "$1,200.50"}
      ],
      "uncertainties": []
    }
  ]
}
```

**严格要求：**
- 不要编造数据——看不清就在 uncertainties 中标注
- 保留原始数字精度
- 不要遗漏任何行或列"""


# ════════════════════════════════════════════════════════════════
# 解析器
# ════════════════════════════════════════════════════════════════

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_ADDR_RE = re.compile(r'^[A-Z]{1,3}\d+$', re.IGNORECASE)


def _extract_json(text: str) -> dict[str, Any] | None:
    """从 VLM 输出中提取 JSON dict。"""
    # 先尝试 code fence
    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 尝试修复截断的 JSON
    try:
        from excelmanus.engine_core.tool_dispatcher import _parse_vlm_json
        return _parse_vlm_json(text, try_repair=True)
    except Exception:
        return None


def parse_single_pass_result(
    raw_text: str,
    provenance: dict[str, Any],
) -> ReplicaSpec | None:
    """将单轮提取的 JSON 结果解析为 ReplicaSpec。

    合并了 Pipeline Phase 1-3 的后处理逻辑。
    """
    parsed = _extract_json(raw_text)
    if parsed is None:
        logger.warning("单轮提取: JSON 解析失败 (%d 字符)", len(raw_text))
        return None

    tables = parsed.get("tables") or []
    if not tables:
        logger.warning("单轮提取: 结果中没有 tables 数据")
        return None

    sheets: list[SheetSpec] = []
    all_uncertainties: list[Uncertainty] = []

    for idx, table in enumerate(tables):
        name = table.get("name") or table.get("title") or f"Table{idx + 1}"
        dims = table.get("dimensions") or {"rows": 0, "cols": 0}
        merges = [MergedRange(range=m) for m in (table.get("merges") or [])]
        hints = SemanticHints(
            header_rows=table.get("header_rows") or [],
            total_rows=table.get("total_rows") or [],
        )
        col_widths = table.get("col_widths") or []
        row_heights = table.get("row_heights") or {}

        # 解析 cells
        cells: list[CellSpec] = []
        for c in table.get("cells") or []:
            addr = c.get("addr", "")
            if not addr or not _ADDR_RE.match(addr.strip()):
                continue
            cells.append(CellSpec(
                address=addr.strip().upper(),
                value=c.get("val"),
                value_type=c.get("type", "string"),
                display_text=c.get("display"),
                style_id=c.get("style_id"),
                number_format=c.get("number_format"),
                confidence=c.get("confidence", 0.9),
            ))

        # 解析 styles
        style_classes: dict[str, StyleClass] = {}
        for style_id, style_data in (table.get("styles") or {}).items():
            sc = _parse_style_class(style_data)
            if sc:
                style_classes[style_id] = sc

        # 解析 uncertainties
        for u in table.get("uncertainties") or []:
            all_uncertainties.append(Uncertainty(
                location=u.get("addr") or u.get("location", "unknown"),
                reason=u.get("reason", ""),
                candidate_values=u.get("candidates") or [],
                confidence=u.get("confidence", 0.5),
            ))

        sheets.append(SheetSpec(
            name=name,
            dimensions=dims,
            merged_ranges=merges,
            column_widths=col_widths,
            row_heights={str(k): v for k, v in row_heights.items()} if row_heights else {},
            cells=cells,
            styles=style_classes,
            semantic_hints=hints,
        ))

    if not sheets:
        return None

    # 公式检测
    from excelmanus.pipeline.formula_detector import detect_formulas
    spec = ReplicaSpec(
        sheets=sheets,
        uncertainties=all_uncertainties,
        provenance=Provenance(**provenance) if provenance else None,
    )
    detect_formulas(spec)
    return spec


def _parse_style_class(data: dict[str, Any]) -> StyleClass | None:
    """解析单个样式 class 定义。"""
    font = None
    fill = None
    border = None
    alignment = None

    if "font" in data:
        f = data["font"]
        color = resolve_semantic_color(f.get("color")) if f.get("color") else None
        font = FontSpec(
            name=f.get("name"),
            size=f.get("size"),
            bold=f.get("bold"),
            italic=f.get("italic"),
            color=color,
        )

    if "fill" in data:
        fl = data["fill"]
        color = resolve_semantic_color(fl.get("color")) if fl.get("color") else None
        if color:
            fill = FillSpec(color=color)

    if "border" in data:
        b = data["border"]
        if isinstance(b, dict):
            border = BorderSpec(
                style=b.get("style"),
                color=resolve_semantic_color(b.get("color")) if b.get("color") else None,
            )

    if "alignment" in data:
        a = data["alignment"]
        alignment = AlignmentSpec(
            horizontal=a.get("horizontal"),
            vertical=a.get("vertical"),
            wrap_text=a.get("wrap_text"),
        )

    if not any([font, fill, border, alignment]):
        return None

    return StyleClass(
        font=font,
        fill=fill,
        border=border,
        alignment=alignment,
    )
