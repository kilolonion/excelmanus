"""VLM 结构化提取：图片 → ReplicaSpec。

支持两种提取策略：
- "single": 单次 VLM 调用提取数据+样式（默认，兼容旧版）
- "two_phase": Phase A 提取 HTML 表格结构+数据，Phase B 提取样式（更高精度）
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from excelmanus.replica_spec import ReplicaSpec

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 公共 API
# ════════════════════════════════════════════════════════════════


def build_extraction_prompt(focus: str = "full") -> str:
    """构建专用的表格提取 prompt（单次调用模式）。

    Args:
        focus: 提取模式 — "full"(数据+样式), "data"(仅数据), "style"(仅样式)
    """
    parts = [_SYSTEM_ROLE, _COT_STEPS]

    if focus == "data":
        parts.append(
            "\n\n**本次只需提取数据和结构，styles 字典留空，忽略字体/颜色/边框等样式信息。**"
        )
    elif focus == "style":
        parts.append(
            "\n\n**本次只需提取样式信息（styles/column_widths/row_heights），"
            "cells 中只需 address 和 style_id 映射。**"
        )

    parts.append(_REPLICA_SPEC_SCHEMA)
    parts.append(_FEW_SHOT_EXAMPLE)
    parts.append(_NEGATIVE_CONSTRAINTS)
    parts.append(_OUTPUT_RULES)
    return "\n\n".join(parts)


def build_phase_a_prompt() -> str:
    """Phase A prompt：仅提取表格结构和数据（HTML 中间格式）。"""
    return "\n\n".join([_PHASE_A_ROLE, _PHASE_A_STEPS, _PHASE_A_RULES])


def build_phase_b_prompt(html_table: str) -> str:
    """Phase B prompt：基于 Phase A 结果提取样式。"""
    return "\n\n".join([
        _PHASE_B_ROLE,
        f"**Phase A 提取的表格结构（HTML）：**\n```html\n{html_table}\n```",
        _PHASE_B_STEPS,
        _PHASE_B_RULES,
    ])


def parse_extraction_result(raw: str) -> ReplicaSpec:
    """解析 LLM 返回的 JSON 为 ReplicaSpec。

    支持 LLM 输出被包裹在 ```json ... ``` 代码块中的情况。

    Raises:
        ValueError: JSON 解析失败或 schema 校验失败。
    """
    text = raw.strip()

    # 尝试提取 ```json ... ``` 代码块
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"期望 JSON 对象，得到 {type(data).__name__}")

    try:
        return ReplicaSpec.model_validate(data)
    except Exception as exc:
        raise ValueError(f"ReplicaSpec 校验失败: {exc}") from exc


def parse_html_table(raw: str) -> str:
    """从 VLM Phase A 输出中提取 HTML table 标签内容。

    Raises:
        ValueError: 找不到 <table> 标签。
    """
    text = raw.strip()
    # 先尝试从 ```html ... ``` 代码块中提取
    code_match = re.search(r"```(?:html)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if code_match:
        text = code_match.group(1).strip()

    # 提取 <table>...</table>
    table_match = re.search(r"<table[\s>].*?</table>", text, re.DOTALL | re.IGNORECASE)
    if table_match:
        return table_match.group(0)

    raise ValueError("VLM Phase A 输出中未找到 <table> 标签")


def html_table_to_replica_spec(
    html: str,
    style_json: dict[str, Any] | None = None,
) -> ReplicaSpec:
    """将 HTML table + 可选的样式 JSON 合并转换为 ReplicaSpec。

    Args:
        html: Phase A 返回的 HTML table 字符串
        style_json: Phase B 返回的样式字典（可选）

    Returns:
        ReplicaSpec 对象
    """
    from excelmanus.replica_spec import (
        CellSpec,
        MergedRange,
        Provenance,
        SheetSpec,
        StyleClass,
        WorkbookSpec,
    )

    cells: list[CellSpec] = []
    merged_ranges: list[MergedRange] = []

    # 解析 HTML table — 使用正则（避免引入 bs4 依赖）
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)

    row_idx = 0
    for row_html in rows:
        # 提取 <td> 和 <th> 单元格
        cell_tags = re.findall(
            r"<(td|th)([^>]*)>(.*?)</(?:td|th)>",
            row_html,
            re.DOTALL | re.IGNORECASE,
        )
        col_idx = 0
        for tag_name, attrs, content in cell_tags:
            # 列号（跳过已被 colspan/rowspan 占用的位置由调用方处理）
            col_letter = _col_num_to_letter(col_idx + 1)
            address = f"{col_letter}{row_idx + 1}"

            # 清理内容
            text = re.sub(r"<[^>]+>", "", content).strip()
            text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            text = text.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')

            # 推断类型
            value, value_type = _infer_value_type(text)

            # 置信度：含 [?] 标记的为低置信度
            confidence = 0.5 if "[?]" in content else 0.95

            cells.append(CellSpec(
                address=address,
                value=value,
                value_type=value_type,
                display_text=text if text else None,
                confidence=confidence,
            ))

            # 解析 colspan/rowspan
            colspan = int(m.group(1)) if (m := re.search(r'colspan=["\']?(\d+)', attrs)) else 1
            rowspan = int(m.group(1)) if (m := re.search(r'rowspan=["\']?(\d+)', attrs)) else 1
            if colspan > 1 or rowspan > 1:
                end_col = _col_num_to_letter(col_idx + colspan)
                end_row = row_idx + rowspan
                merge_range = f"{address}:{end_col}{end_row}"
                merged_ranges.append(MergedRange(range=merge_range, confidence=0.9))

            col_idx += colspan
        row_idx += 1

    # 构建样式
    styles: dict[str, StyleClass] = {}
    column_widths: list[float] = []
    if style_json:
        raw_styles = style_json.get("styles", {})
        for sid, sdata in raw_styles.items():
            try:
                styles[sid] = StyleClass.model_validate(sdata)
            except Exception:
                logger.warning("样式 %s 解析失败，已跳过", sid)

        # 样式映射到 cells
        cell_styles = style_json.get("cell_styles", {})
        for cell in cells:
            if cell.address in cell_styles:
                cell.style_id = cell_styles[cell.address]

        column_widths = style_json.get("column_widths", [])

    max_row = row_idx
    max_col = max((cells[-1] if cells else CellSpec(address="A1")).address, default="A1")
    # 简单推算列数
    n_cols = col_idx if col_idx > 0 else 1

    sheet = SheetSpec(
        name="Sheet1",
        dimensions={"rows": max_row, "cols": n_cols},
        cells=cells,
        merged_ranges=merged_ranges,
        styles=styles,
        column_widths=column_widths,
    )

    return ReplicaSpec(
        provenance=Provenance(source_image_hash="", model="", timestamp=""),
        workbook=WorkbookSpec(name="replica"),
        sheets=[sheet],
        uncertainties=[],
    )


# ════════════════════════════════════════════════════════════════
# 内部工具函数
# ════════════════════════════════════════════════════════════════


def _col_num_to_letter(n: int) -> str:
    """1-indexed 列号 → Excel 列字母（A, B, ..., Z, AA, AB, ...）。"""
    result = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _infer_value_type(text: str) -> tuple[Any, str]:
    """根据文本内容推断值和类型。"""
    if not text or text == "[?]":
        return None, "empty"

    # 尝试整数
    cleaned = text.replace(",", "").replace(" ", "")
    try:
        val = int(cleaned)
        return val, "number"
    except ValueError:
        pass

    # 尝试浮点数
    try:
        val = float(cleaned)
        return val, "number"
    except ValueError:
        pass

    # 百分数
    if cleaned.endswith("%"):
        try:
            val = float(cleaned[:-1]) / 100
            return val, "number"
        except ValueError:
            pass

    # 货币
    for prefix in ("$", "¥", "€", "£"):
        if cleaned.startswith(prefix):
            try:
                val = float(cleaned[len(prefix):].replace(",", ""))
                return val, "number"
            except ValueError:
                pass

    return text, "string"


# ════════════════════════════════════════════════════════════════
# 语义颜色映射
# ════════════════════════════════════════════════════════════════

# Excel 标准调色板语义映射（覆盖 90%+ 常见表格配色）
SEMANTIC_COLOR_MAP: dict[str, str] = {
    # 蓝色系（Excel 默认主题最常用）
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
# Prompt 模板 — 单次调用模式（改进版）
# ════════════════════════════════════════════════════════════════

_SYSTEM_ROLE = """\
你是高精度表格结构化提取引擎。你的任务是将图片中的表格精确转换为 JSON 格式。
精确度是最高优先级——每个数字、每个文字都必须与图片中完全一致。"""

_COT_STEPS = """\
**请按以下步骤思考和提取（不要输出思考过程，只输出最终 JSON）：**

Step 1 — 观察：确定表格的行数、列数、表头位置、是否有合并单元格、是否有小计/合计行。
Step 2 — 逐行提取：从第 1 行开始，逐行逐列读取每个单元格的内容。对每个单元格：
  - 精确复制文字/数字（保留原始精度，如 12.50 不要写成 12.5）
  - 判断数据类型（string/number/date/formula/empty）
  - 识别合并单元格范围
Step 3 — 样式提取：识别各区域的样式差异（表头加粗/填充色、数据区对齐方式、边框等）
Step 4 — 不确定标记：模糊/不确定的内容放入 uncertainties，给出候选值"""

_REPLICA_SPEC_SCHEMA = """\
**输出 JSON 格式（ReplicaSpec）：**
```json
{
  "version": "1.0",
  "provenance": {"source_image_hash": "", "model": "", "timestamp": ""},
  "sheets": [{
    "name": "Sheet1",
    "dimensions": {"rows": 5, "cols": 4},
    "cells": [
      {"address": "A1", "value": "产品名称", "value_type": "string", "style_id": "h1", "confidence": 0.98},
      {"address": "B1", "value": "单价", "value_type": "string", "style_id": "h1", "confidence": 0.98},
      {"address": "A2", "value": "商品A", "value_type": "string", "confidence": 0.95},
      {"address": "B2", "value": 128.50, "value_type": "number", "number_format": "#,##0.00", "confidence": 0.95}
    ],
    "merged_ranges": [{"range": "A1:C1", "confidence": 0.95}],
    "styles": {
      "h1": {"font": {"bold": true, "size": 11, "color": "#FFFFFF"}, "fill": {"type": "solid", "color": "#4472C4"}, "alignment": {"horizontal": "center"}},
      "d1": {"alignment": {"horizontal": "right"}, "font": {"size": 10}}
    },
    "column_widths": [18, 12, 12, 15],
    "row_heights": {}
  }],
  "uncertainties": [
    {"location": "C3", "reason": "数字模糊，可能是 8 或 6", "candidate_values": ["1280", "1260"], "confidence": 0.6}
  ]
}
```"""

_FEW_SHOT_EXAMPLE = """\
**示例：假设图片是一个 2×3 的简单销售表**

| 商品 | 数量 | 金额 |
|------|------|------|
| 苹果 | 10   | 50.00|

正确提取为：
```json
{"version":"1.0","provenance":{"source_image_hash":"","model":"","timestamp":""},
 "sheets":[{"name":"Sheet1","dimensions":{"rows":2,"cols":3},
   "cells":[
     {"address":"A1","value":"商品","value_type":"string","style_id":"h1","confidence":0.99},
     {"address":"B1","value":"数量","value_type":"string","style_id":"h1","confidence":0.99},
     {"address":"C1","value":"金额","value_type":"string","style_id":"h1","confidence":0.99},
     {"address":"A2","value":"苹果","value_type":"string","confidence":0.98},
     {"address":"B2","value":10,"value_type":"number","confidence":0.98},
     {"address":"C2","value":50.00,"value_type":"number","number_format":"#,##0.00","confidence":0.98}],
   "styles":{"h1":{"font":{"bold":true},"fill":{"type":"solid","color":"#4472C4"},"alignment":{"horizontal":"center"}}},
   "column_widths":[12,8,10]}],
 "uncertainties":[]}
```"""

_NEGATIVE_CONSTRAINTS = """\
**严格禁止：**
- ❌ 不要编造或猜测任何数据——看不清的必须放入 uncertainties
- ❌ 不要改变数字精度（12.50 不能写成 12.5，1,200 不能写成 1200）
- ❌ 不要遗漏空单元格——空格也要用 {"address":"X1","value":null,"value_type":"empty"} 表示
- ❌ 不要遗漏任何行或列——即使内容重复
- ❌ 不要在 JSON 外输出任何解释文字"""

_OUTPUT_RULES = """\
**输出格式要求：**
- 只输出纯 JSON（可包裹在 ```json``` 代码块中）
- 样式用短 style_id 引用（h1=表头, d1=数据, t1=合计等）
- 颜色使用 6 位 hex（如 #4472C4），不要 3 位缩写
- 数字 value 为 JSON 数值类型，不是字符串
- 日期 value 为 ISO 格式字符串（如 "2024-01-15"）
- 包含千分位/百分号/货币符的数字：value 为纯数值，显示格式放 number_format
- 省略值为 null 的可选字段以减少输出量"""


# ════════════════════════════════════════════════════════════════
# Prompt 模板 — 两阶段模式
# ════════════════════════════════════════════════════════════════

_PHASE_A_ROLE = """\
你是高精度表格提取引擎。你的任务是将图片中的表格精确转换为 HTML <table> 格式。
**本阶段只关注结构和数据，不关注样式。精确度是最高优先级。**"""

_PHASE_A_STEPS = """\
**请按以下步骤操作（不要输出思考过程，只输出最终 HTML）：**

Step 1 — 观察：确定表格的行数、列数、表头位置、合并单元格情况。
Step 2 — 逐行提取：从第 1 行开始，逐行逐列提取，输出为 HTML <table>：
  - 精确复制每个单元格的文字/数字（保留原始精度：12.50 不写成 12.5）
  - 合并单元格使用 colspan/rowspan
  - 表头用 <th>，数据用 <td>
  - 空单元格用空 <td></td>
  - 看不清的内容用 [?] 标记
Step 3 — 自检：检查行列数是否与图片一致，是否有遗漏。"""

_PHASE_A_RULES = """\
**输出要求：**
- 只输出 HTML <table>...</table>（可包裹在 ```html``` 代码块中）
- 不要输出任何 CSS 样式或 style 属性
- 不要在表格外输出解释文字
- 不要编造数据，不确定的用 [?] 标记

**严格禁止：**
- ❌ 不要改变数字精度
- ❌ 不要遗漏任何行或列
- ❌ 不要编造或推测数据"""

_PHASE_B_ROLE = """\
你是表格样式分析引擎。你已经有了精确的表格结构（HTML），现在需要从图片中提取样式信息。"""

_PHASE_B_STEPS = """\
**请观察图片中各区域的样式差异，输出 JSON：**

1. 定义 style classes（如 h1=表头样式, d1=数据样式）
2. 记录每个样式的：字体（bold/italic/size/color）、填充色、对齐方式、边框
3. 将样式映射到具体单元格地址
4. 估算列宽（Excel 字符单位）"""

_PHASE_B_RULES = """\
**输出格式（纯 JSON）：**
```json
{
  "styles": {
    "h1": {"font": {"bold": true, "size": 11, "color": "#FFFFFF"}, "fill": {"type": "solid", "color": "#4472C4"}, "alignment": {"horizontal": "center"}}
  },
  "cell_styles": {"A1": "h1", "B1": "h1", "A2": "d1"},
  "column_widths": [18, 12, 12]
}
```

- 颜色使用 6 位 hex
- 只输出纯 JSON，无解释文字
- 省略默认值（如无边框、左对齐等）"""
