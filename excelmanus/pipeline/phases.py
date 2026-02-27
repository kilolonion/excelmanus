"""4 阶段 VLM prompt 构建器与后处理器。"""

from __future__ import annotations

import logging
import re
from typing import Any

from excelmanus.replica_spec import (
    AlignmentSpec,
    BorderSideSpec,
    BorderSpec,
    CellSpec,
    ConditionalFormatRule,
    FillSpec,
    FontSpec,
    MergedRange,
    Provenance,
    ReplicaSpec,
    SemanticHints,
    SheetSpec,
    StyleClass,
    Uncertainty,
    WorkbookSpec,
)
from excelmanus.vision_extractor import (
    build_extract_style_prompt,
    resolve_semantic_color,
)

logger = logging.getLogger(__name__)

_ADDR_RE = re.compile(r'^[A-Z]{1,3}\d+$', re.IGNORECASE)


def _is_valid_address(addr: str) -> bool:
    return bool(addr and _ADDR_RE.match(addr.strip()))


# ════════════════════════════════════════════════════════════════
# 阶段 1 — 结构提示
# ════════════════════════════════════════════════════════════════

PHASE1_STRUCTURE_PROMPT = """\
你是一个精确的表格结构识别引擎。请仔细观察图片，**仅**识别表格的骨架结构，不需要提取具体数据。

如果图片中有多个独立表格，请分别提取为 tables 数组中的不同元素。

请严格输出以下 JSON 格式（不要输出任何其他内容）：

```json
{
  "tables": [
    {
      "name": "Sheet1",
      "title": "表���标题（如果有）",
      "dimensions": {"rows": 行数, "cols": 列数},
      "header_rows": [1],
      "total_rows": [],
      "merges": ["A1:E1", "A10:C10"],
      "col_widths": [15, 10, 12],
      "row_types": {
        "1": "header",
        "2": "data",
        "10": "total"
      },
      "uncertainties": [
        {"location": "row_count", "reason": "底部被截断", "candidates": ["15", "16"]}
      ]
    }
  ]
}
```

字段说明：
- dimensions: 表格的行列数
- header_rows: 表头行号列表（1-based）
- total_rows: 合计行号列表（1-based）
- merges: 合并单元格范围列表
- col_widths: 各列的估计宽度（Excel 字符单位，通常 8-25）
- row_types: 行号 → 行类型映射（header/data/total/label/signature/empty）
- uncertainties: 不确定项

**严格要求：**
- 不要提取单元格的具体值——只识别结构
- 准确计数行列数，不要遗漏
- 仔细识别所有合并区域
- 如果有多个独立表格，分别列出"""


def build_phase1_prompt() -> str:
    return PHASE1_STRUCTURE_PROMPT


# ════════════════════════════════════════════════════════════════
# 阶段 2 — 数据提示
# ════════════════════════════════════════════════════════════════

_PHASE2_DATA_PROMPT_TEMPLATE = """\
你是一个精确的表格数据提取引擎。以下是已识别的表格结构：
{structure_summary}

请基于上述结构，提取每个单元格的具体值。

请严格输出以下 JSON 格式（不要输出任何其他内容）：

```json
{{
  "tables": [
    {{
      "name": "Sheet1",
      "cells": [
        {{"addr": "A1", "val": "单元格值", "type": "string"}},
        {{"addr": "B2", "val": 1200.50, "type": "number", "display": "$1,200.50"}}
      ],
      "uncertainties": [
        {{"addr": "C5", "reason": "数字模糊", "candidates": ["350", "850"]}}
      ]
    }}
  ]
}}
```

字段说明：
- addr: Excel 单元格地址（如 A1, B2）
- val: 单元格的实际值（数字用数字类型，文字用字符串）
- type: "string" | "number" | "date" | "boolean" | "formula" | "empty"
- display: 可选，单元格的显示文本（如带货币符号、千分位的数字）
- uncertainties: 不确定项（看不清的内容）

**严格要求：**
- 不要编造数据——看不清就在 uncertainties 中标注
- 保留原始数字精度（12.50 不写成 12.5）
- 不要遗漏任何行或列
- 不需要输出结构信息（dimensions/merges/col_widths），只输出 cells"""


def build_phase2_prompt(structure_summary: str) -> str:
    return _PHASE2_DATA_PROMPT_TEMPLATE.format(structure_summary=structure_summary)


_PHASE2_CHUNKED_PROMPT_TEMPLATE = """\
你是一个精确的表格数据提取引擎。以下是已识别的表格结构：
{structure_summary}

**重要：本次只需提取第 {row_start} 行到第 {row_end} 行的数据。**
不要提取范围外的行。

请严格输出以下 JSON 格式（不要输出任何其他内容）：

```json
{{{{
  "tables": [
    {{{{
      "name": "Sheet1",
      "cells": [
        {{{{"addr": "A{row_start}", "val": "单元格值", "type": "string"}}}},
        {{{{"addr": "B{row_start}", "val": 1200.50, "type": "number", "display": "$1,200.50"}}}}
      ],
      "uncertainties": [
        {{{{"addr": "C5", "reason": "数字模糊", "candidates": ["350", "850"]}}}}
      ]
    }}}}
  ]
}}}}
```

字段说明：
- addr: Excel 单元格地址（如 A1, B2）
- val: 单元格的实际值（数字用数字类型，文字用字符串）
- type: "string" | "number" | "date" | "boolean" | "formula" | "empty"
- display: 可选，单元格的显示文本（如带货币符号、千分位的数字）
- uncertainties: 不确定项（看不清的内容）

**严格要求：**
- 只提取第 {row_start} 行到第 {row_end} 行
- 不要编造数据——看不清就在 uncertainties 中标注
- 保留原始数字精度（12.50 不写成 12.5）
- 不要遗漏指定范围内的任何行或列"""


def build_phase2_chunked_prompt(
    structure_summary: str, row_start: int, row_end: int,
) -> str:
    """构建分区 Phase 2 prompt，仅提取指定行范围内的数据。"""
    return _PHASE2_CHUNKED_PROMPT_TEMPLATE.format(
        structure_summary=structure_summary,
        row_start=row_start,
        row_end=row_end,
    )


# ════════════════════════════════════════════════════════════════
# 阶段 3 — 样式提示（复用 vision_extractor）
# ════════════════════════════════════════════════════════════════

def build_phase3_prompt(data_summary: str) -> str:
    return build_extract_style_prompt(data_summary)


# ════════════════════════════════════════════════════════════════
# 阶段 4 — 校验提示
# ════════════════════════════════════════════════════════════════

_PHASE4_VERIFY_PROMPT_TEMPLATE = """\
你是一个精确的表格校验引擎。请根据你在前几轮对话中观察到的原始图片内容，对比以下已提取的表格数据，找出所有错误并生成修正补丁。

已提取的表格摘要：
{spec_summary}

请仔细检查以下方面：
1. 单元格值是否正确（特别注意数字精度、小数点、千分位）
2. 合并区域是否遗漏或多余
3. 行列数是否正确
4. 样式是否与图片一致（颜色、字体、对齐）
5. 是否有遗漏的行或列

请严格输出以下 JSON 格式：

```json
{{
  "patches": [
    {{
      "target": "cell",
      "sheet_name": "Sheet1",
      "address": "C5",
      "field": "value",
      "old_value": "350",
      "new_value": "850",
      "reason": "原始图片中该数字为 850，非 350",
      "confidence": 0.9
    }},
    {{
      "target": "merge",
      "sheet_name": "Sheet1",
      "address": "A12:E12",
      "field": "range",
      "old_value": null,
      "new_value": "A12:E12",
      "reason": "遗漏的合并区域",
      "confidence": 0.85
    }}
  ],
  "overall_confidence": 0.92,
  "summary": "发现 2 处修正：1 个数值错误，1 个遗漏合并区域"
}}
```

target 类型：
- "cell": 单元格值/类型修正（field: "value" | "value_type" | "display_text" | "number_format"）
- "merge": 合并区域修正（field: "range"，old_value=null 表示新增，new_value=null 表示删除）
- "style": 样式修正（field: "style_id" 或具体样式属性）
- "dimension": 行列数修正（field: "rows" | "cols"）

**严格要求：**
- 只报告你确信的错误，不要猜测
- 每个 patch 必须有 reason 说明
- 如果没有发现错误，patches 为空数组
- confidence < 0.7 的修正不要包含"""


def build_phase4_prompt(spec_summary: str) -> str:
    return _PHASE4_VERIFY_PROMPT_TEMPLATE.format(spec_summary=spec_summary)


# ════════════════════════════════════════════════════════════════
# 后处理器
# ════════════════════════════════════════════════════════════════


def build_skeleton_spec(
    structure_json: dict[str, Any],
    provenance: dict[str, Any],
) -> ReplicaSpec:
    """阶段 1 输出 → 骨架 ReplicaSpec（无 cells、无 styles）。"""
    tables = structure_json.get("tables") or []
    if not tables:
        raise ValueError("Phase 1 结果中没有 tables 数据")

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
        for u in table.get("uncertainties") or []:
            all_uncertainties.append(Uncertainty(
                location=u.get("location") or u.get("addr", "unknown"),
                reason=u.get("reason", ""),
                candidate_values=u.get("candidates") or [],
                confidence=0.5,
            ))
        sheets.append(SheetSpec(
            name=name,
            dimensions=dims,
            cells=[],
            merged_ranges=merges,
            styles={},
            column_widths=table.get("col_widths") or [],
            row_heights={},
            semantic_hints=hints,
        ))

    return ReplicaSpec(
        version="1.0",
        provenance=Provenance(**provenance),
        workbook=WorkbookSpec(name="replica"),
        sheets=sheets,
        uncertainties=all_uncertainties,
    )


def fill_data_into_spec(
    skeleton: ReplicaSpec,
    data_json: dict[str, Any],
) -> ReplicaSpec:
    """阶段 2 输出 → 将 cells 填入骨架 spec。"""
    spec = ReplicaSpec.model_validate(skeleton.model_dump())
    tables = data_json.get("tables") or []

    for idx, table in enumerate(tables):
        if idx >= len(spec.sheets):
            break
        sheet = spec.sheets[idx]
        cells: list[CellSpec] = []
        for raw_cell in table.get("cells") or []:
            addr = raw_cell.get("addr", "")
            if not _is_valid_address(addr):
                spec.uncertainties.append(Uncertainty(
                    location=addr or "unknown",
                    reason=f"无效的单元格地址: {addr!r}",
                ))
                continue
            display = raw_cell.get("display")
            vtype = raw_cell.get("type", "string")
            val = raw_cell.get("val")
            nf = None
            if display and vtype == "number" and val is not None:
                nf = _infer_number_format(display)
            cells.append(CellSpec(
                address=addr,
                value=val,
                value_type=vtype,
                display_text=display,
                number_format=nf,
                confidence=1.0,
            ))
        sheet.cells = cells

        for u in table.get("uncertainties") or []:
            spec.uncertainties.append(Uncertainty(
                location=u.get("addr", "unknown"),
                reason=u.get("reason", ""),
                candidate_values=u.get("candidates") or [],
                confidence=0.5,
            ))

    return spec


def apply_styles_to_spec(
    spec: ReplicaSpec,
    style_json: dict[str, Any],
) -> ReplicaSpec:
    """阶段 3 输出 → 将样式应用到 spec。

    支持两种 VLM 返回格式：
    1. per-sheet 格式（推荐）：
       {"sheets": [{"name": "Sheet1", "styles": {...}, "cell_styles": {...}, "row_heights": {...}}]}
    2. flat 格式（向后兼容）：
       {"styles": {...}, "cell_styles": {...}, "row_heights": {...}}
       — 此时按 sheet 独立匹配 cell_styles，row_heights 按各 sheet 实际行数过滤
    """
    patched = ReplicaSpec.model_validate(spec.model_dump())

    # default_font 始终为全局
    df = style_json.get("default_font")
    if isinstance(df, dict):
        patched.workbook.default_font = FontSpec(
            name=df.get("name"), size=df.get("size"),
        )

    # ── 全局条件格式（flat 格式）──
    cond_formats_raw = style_json.get("conditional_formats")

    # ── per-sheet 格式 ──
    sheets_data = style_json.get("sheets")
    if isinstance(sheets_data, list) and sheets_data:
        sheet_map = {s.name: s for s in patched.sheets}
        for sd in sheets_data:
            name = sd.get("name", "")
            sheet = sheet_map.get(name)
            if sheet is None:
                continue
            _apply_sheet_style_block(sheet, sd)
            # per-sheet 条件格式
            per_sheet_cf = sd.get("conditional_formats")
            if isinstance(per_sheet_cf, list):
                sheet.conditional_formats = _parse_conditional_formats(per_sheet_cf)
        # flat 格式的条件格式应用到所有 sheet
        if isinstance(cond_formats_raw, list) and cond_formats_raw:
            parsed_cf = _parse_conditional_formats(cond_formats_raw)
            for sheet in patched.sheets:
                if not sheet.conditional_formats:
                    sheet.conditional_formats = list(parsed_cf)
        return patched

    # ── flat 格式（向后兼容）──
    style_defs = style_json.get("styles") or {}
    cell_style_map = style_json.get("cell_styles") or {}
    row_heights_raw = style_json.get("row_heights") or {}

    compiled_styles: dict[str, StyleClass] = {}
    for sid, sraw in style_defs.items():
        compiled_styles[sid] = _build_style_class(sraw)

    # 展开范围映射为 per-cell
    cell_to_style: dict[str, str] = {}
    for range_str, style_id in cell_style_map.items():
        for addr in _expand_range(range_str):
            cell_to_style[addr.upper()] = style_id

    # flat 格式条件格式
    parsed_cf = _parse_conditional_formats(cond_formats_raw) if isinstance(cond_formats_raw, list) else []

    for sheet in patched.sheets:
        sheet.styles = dict(compiled_styles)
        # row_heights：按该 sheet 实际行数过滤，避免跨 sheet 错误覆盖
        if row_heights_raw:
            max_rows = sheet.dimensions.get("rows", 0)
            sheet.row_heights = {
                str(k): float(v)
                for k, v in row_heights_raw.items()
                if _safe_int(k, 0) <= max_rows
            }
        # cell_styles：仅匹配本 sheet 已有的 cell 地址
        existing_addrs = {c.address.upper() for c in sheet.cells}
        for cell in sheet.cells:
            addr_up = cell.address.upper()
            if addr_up in existing_addrs:
                sid = cell_to_style.get(addr_up)
                if sid:
                    cell.style_id = sid
        # 条件格式
        if parsed_cf:
            sheet.conditional_formats = list(parsed_cf)

    return patched


def _apply_sheet_style_block(sheet: "SheetSpec", sd: dict[str, Any]) -> None:
    """将单个 sheet 的样式块应用到 SheetSpec。"""
    style_defs = sd.get("styles") or {}
    cell_style_map = sd.get("cell_styles") or {}
    row_heights_raw = sd.get("row_heights") or {}

    compiled: dict[str, StyleClass] = {}
    for sid, sraw in style_defs.items():
        compiled[sid] = _build_style_class(sraw)
    sheet.styles = compiled

    if row_heights_raw:
        sheet.row_heights = {
            str(k): float(v) for k, v in row_heights_raw.items()
        }

    cell_to_style: dict[str, str] = {}
    for range_str, style_id in cell_style_map.items():
        for addr in _expand_range(range_str):
            cell_to_style[addr.upper()] = style_id

    for cell in sheet.cells:
        sid = cell_to_style.get(cell.address.upper())
        if sid:
            cell.style_id = sid


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


# ════════════════════════════════════════════════════════════════
# 摘要生成器（供后续阶段 prompt 使用）
# ════════════════════════════════════════════════════════════════


def build_structure_summary(spec: ReplicaSpec) -> str:
    """从骨架 spec 生成结构摘要文本。"""
    lines = []
    for sheet in spec.sheets:
        dims = sheet.dimensions
        rows = dims.get("rows", "?")
        cols = dims.get("cols", "?")
        merge_count = len(sheet.merged_ranges)
        lines.append(
            f"- {sheet.name}: {rows}行×{cols}列, "
            f"{merge_count}个合并区域"
        )
        if sheet.merged_ranges:
            merges_str = ", ".join(m.range for m in sheet.merged_ranges[:10])
            lines.append(f"  合并: {merges_str}")
        if sheet.semantic_hints.header_rows:
            lines.append(f"  表头行: {sheet.semantic_hints.header_rows}")
    return "\n".join(lines) if lines else "（无表格信息）"


def build_data_summary(spec: ReplicaSpec) -> str:
    """从含数据的 spec 生成摘要文本。"""
    lines = []
    for sheet in spec.sheets:
        dims = sheet.dimensions
        cell_count = len(sheet.cells)
        merge_count = len(sheet.merged_ranges)
        lines.append(
            f"- {sheet.name}: {dims.get('rows', '?')}行×{dims.get('cols', '?')}列, "
            f"{cell_count}个单元格, {merge_count}个合并区域"
        )
    return "\n".join(lines) if lines else "（无表格信息）"


def build_full_summary(
    spec: ReplicaSpec,
    *,
    head_cells: int = 50,
    tail_cells: int = 30,
    sample_cells: int = 10,
) -> str:
    """从完整 spec 生成详细摘要（供 Phase 4 校验用）。

    对每个 sheet：
    - 小表（≤ head + tail）：全部输出
    - 大表：输出前 *head_cells* 个、末尾 *tail_cells* 个、
      中间等间距采样 *sample_cells* 个，并附带统计摘要
    """
    lines: list[str] = []
    for sheet in spec.sheets:
        dims = sheet.dimensions
        total = len(sheet.cells)
        lines.append(
            f"## {sheet.name} ({dims.get('rows', '?')}行×{dims.get('cols', '?')}列, "
            f"{total}个单元格)"
        )

        show_limit = head_cells + tail_cells
        if total <= show_limit:
            # 小表：全部输出
            for cell in sheet.cells:
                val_repr = repr(cell.value) if cell.value is not None else "(空)"
                lines.append(f"  {cell.address}: {val_repr} [{cell.value_type}]")
        else:
            # 大表：首部
            for cell in sheet.cells[:head_cells]:
                val_repr = repr(cell.value) if cell.value is not None else "(空)"
                lines.append(f"  {cell.address}: {val_repr} [{cell.value_type}]")

            # 中间采样
            middle_start = head_cells
            middle_end = total - tail_cells
            middle_count = middle_end - middle_start
            if middle_count > 0 and sample_cells > 0:
                step = max(1, middle_count // sample_cells)
                sampled_indices = list(range(middle_start, middle_end, step))[:sample_cells]
                lines.append(f"  --- 中间区域采样 ({len(sampled_indices)}/{middle_count} 个) ---")
                for idx in sampled_indices:
                    cell = sheet.cells[idx]
                    val_repr = repr(cell.value) if cell.value is not None else "(空)"
                    lines.append(f"  {cell.address}: {val_repr} [{cell.value_type}]")

            lines.append(f"  --- 末尾 {tail_cells} 个 ---")
            # 尾部
            for cell in sheet.cells[-tail_cells:]:
                val_repr = repr(cell.value) if cell.value is not None else "(空)"
                lines.append(f"  {cell.address}: {val_repr} [{cell.value_type}]")

        # 统计摘要（大表时特别重要）
        if total > show_limit:
            type_dist: dict[str, int] = {}
            styled_count = 0
            for cell in sheet.cells:
                vt = cell.value_type or "unknown"
                type_dist[vt] = type_dist.get(vt, 0) + 1
                if cell.style_id:
                    styled_count += 1
            dist_str = ", ".join(f"{k}:{v}" for k, v in sorted(type_dist.items()))
            lines.append(f"  统计: 总计{total}格, 类型分布=[{dist_str}], 已设样式={styled_count}")

        if sheet.merged_ranges:
            merges_str = ", ".join(m.range for m in sheet.merged_ranges)
            lines.append(f"  合并区域: {merges_str}")

    if spec.uncertainties:
        lines.append("\n不确定项:")
        for u in spec.uncertainties[:10]:
            lines.append(f"  - {u.location}: {u.reason}")
    return "\n".join(lines)


def build_partial_summary(
    spec: ReplicaSpec,
    row_start: int,
    row_end: int,
) -> str:
    """为指定行范围生成摘要（供 Phase 4 分区校验用）。

    只输出地址行号在 [row_start, row_end] 范围内的单元格。
    """
    import re as _re
    _row_re = _re.compile(r'\d+$')

    lines: list[str] = []
    for sheet in spec.sheets:
        dims = sheet.dimensions
        lines.append(
            f"## {sheet.name} ({dims.get('rows', '?')}行×{dims.get('cols', '?')}列) "
            f"— 校验范围: 第{row_start}行~第{row_end}行"
        )
        included = 0
        for cell in sheet.cells:
            m = _row_re.search(cell.address)
            if not m:
                continue
            row_num = int(m.group())
            if row_start <= row_num <= row_end:
                val_repr = repr(cell.value) if cell.value is not None else "(空)"
                lines.append(f"  {cell.address}: {val_repr} [{cell.value_type}]")
                included += 1
        lines.append(f"  （本区间 {included} 个单元格）")

        if sheet.merged_ranges:
            relevant = [
                m.range for m in sheet.merged_ranges
                if _merge_in_range(m.range, row_start, row_end)
            ]
            if relevant:
                lines.append(f"  合并区域: {', '.join(relevant)}")

    return "\n".join(lines)


def _merge_in_range(range_str: str, row_start: int, row_end: int) -> bool:
    """判断合并区域是否与行范围有交集。"""
    import re as _re
    nums = _re.findall(r'\d+', range_str)
    if len(nums) >= 2:
        r1, r2 = int(nums[0]), int(nums[-1])
        return r1 <= row_end and r2 >= row_start
    elif nums:
        r = int(nums[0])
        return row_start <= r <= row_end
    return True


_PHASE4_CHUNKED_VERIFY_PROMPT_TEMPLATE = """\
你是一个精确的表格校验引擎。请根据图片内容，对比以下已提取的**部分**表格数据（第 {row_start} 行到第 {row_end} 行），找出所有错误并生成修正补丁。

已提取的表格摘要（仅含第 {row_start}~{row_end} 行）：
{spec_summary}

请仔细检查以下方面：
1. 单元格值是否正确（特别注意数字精度、小数点、千分位）
2. 合并区域是否遗漏或多余
3. 是否有遗漏的行或列
4. 样式是否与图片一致

请严格输出以下 JSON 格式：

```json
{{
  "patches": [
    {{
      "target": "cell",
      "sheet_name": "Sheet1",
      "address": "C5",
      "field": "value",
      "old_value": "350",
      "new_value": "850",
      "reason": "原始图片中该数字为 850，非 350",
      "confidence": 0.9
    }}
  ],
  "overall_confidence": 0.92,
  "summary": "第 {row_start}~{row_end} 行校验结果"
}}
```

**严格要求：**
- 只校验第 {row_start} 行到第 {row_end} 行范围内的内容
- 只报告你确信的错误，不要猜测
- 每个 patch 必须有 reason 说明
- 如果没有发现错误，patches 为空数组
- confidence < 0.7 的修正不要包含"""


def build_phase4_chunked_prompt(
    spec_summary: str, row_start: int, row_end: int,
) -> str:
    """构建分区 Phase 4 prompt，仅校验指定行范围。"""
    return _PHASE4_CHUNKED_VERIFY_PROMPT_TEMPLATE.format(
        spec_summary=spec_summary,
        row_start=row_start,
        row_end=row_end,
    )


# ════════════════════════════════════════════════════════════════
# 内部辅助
# ════════════════════════════════════════════════════════════════


def _parse_border_side(raw_side: dict | None) -> BorderSideSpec | None:
    """解析单边边框定义 {"style": "thin", "color": "black"}。"""
    if not isinstance(raw_side, dict):
        return None
    return BorderSideSpec(
        style=raw_side.get("style"),
        color=resolve_semantic_color(raw_side.get("color")),
    )


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
        # 检测是否为 per-side 格式（含 top/bottom/left/right 子对象）
        has_sides = any(
            isinstance(raw_border.get(side), dict)
            for side in ("top", "bottom", "left", "right")
        )
        if has_sides:
            border = BorderSpec(
                style=raw_border.get("style"),
                color=resolve_semantic_color(raw_border.get("color")),
                top=_parse_border_side(raw_border.get("top")),
                bottom=_parse_border_side(raw_border.get("bottom")),
                left=_parse_border_side(raw_border.get("left")),
                right=_parse_border_side(raw_border.get("right")),
            )
        else:
            border = BorderSpec(
                style=raw_border.get("style"),
                color=resolve_semantic_color(raw_border.get("color")),
            )
    return StyleClass(font=font, fill=fill, alignment=alignment, border=border)


def _expand_range(range_str: str) -> list[str]:
    from openpyxl.utils import get_column_letter, range_boundaries

    range_str = range_str.strip()
    if ":" not in range_str:
        return [range_str]
    try:
        min_col, min_row, max_col, max_row = range_boundaries(range_str)
        return [
            f"{get_column_letter(c)}{r}"
            for r in range(min_row, max_row + 1)
            for c in range(min_col, max_col + 1)
        ]
    except Exception:
        return [range_str]


def _infer_number_format(display_text: str) -> str | None:
    from excelmanus.format_utils import infer_number_format
    return infer_number_format(display_text)


def _parse_conditional_formats(
    raw_list: list[dict[str, Any]],
) -> list[ConditionalFormatRule]:
    """将 VLM 返回的条件格式 JSON 列表解析为模型对象。"""
    rules: list[ConditionalFormatRule] = []
    valid_types = {"color_scale", "data_bar", "icon_set", "cell_value"}
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        cf_type = raw.get("type")
        cf_range = raw.get("range")
        if cf_type not in valid_types or not cf_range:
            continue
        try:
            rule = ConditionalFormatRule(
                type=cf_type,
                range=cf_range,
                min_color=resolve_semantic_color(raw.get("min_color")),
                mid_color=resolve_semantic_color(raw.get("mid_color")),
                max_color=resolve_semantic_color(raw.get("max_color")),
                bar_color=resolve_semantic_color(raw.get("bar_color")),
                icon_style=raw.get("icon_style"),
                operator=raw.get("operator"),
                value=raw.get("value"),
                value2=raw.get("value2"),
                font_color=resolve_semantic_color(raw.get("font_color")),
                fill_color=resolve_semantic_color(raw.get("fill_color")),
                bold=raw.get("bold"),
            )
            rules.append(rule)
        except Exception:
            logger.warning("条件格式解析失败: %s", raw, exc_info=True)
    return rules
