"""VLM 视觉描述模块：B 通道 — 小视觉模型生成 Markdown + 自然语言表格描述。

B+C 混合架构中：
- B 通道：小 VLM 生成文字描述 → 注入主模型上下文
- C 通道：图片直接注入主模型视觉上下文（由 read_image 处理）
"""

from __future__ import annotations

import logging
import re

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
