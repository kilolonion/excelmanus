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
# 样式提取提示（供 pipeline Phase 3 使用）
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
      "alignment": {{"horizontal": "center", "vertical": "center"}},
      "border": {{
        "top": {{"style": "thin", "color": "white"}},
        "bottom": {{"style": "medium", "color": "black"}},
        "left": {{"style": "thin", "color": "dark_blue"}},
        "right": {{"style": "thin", "color": "dark_blue"}}
      }}
    }},
    "data_text": {{
      "alignment": {{"horizontal": "left", "vertical": "center"}},
      "border": {{"style": "thin", "color": "light_gray"}}
    }},
    "data_number": {{
      "alignment": {{"horizontal": "right", "vertical": "center"}},
      "border": {{"style": "thin", "color": "light_gray"}}
    }},
    "total_row": {{
      "font": {{"bold": true}},
      "alignment": {{"horizontal": "right"}},
      "border": {{
        "top": {{"style": "medium", "color": "black"}},
        "bottom": {{"style": "double", "color": "black"}},
        "left": {{"style": "thin", "color": "light_gray"}},
        "right": {{"style": "thin", "color": "light_gray"}}
      }}
    }},
    "subtotal_row": {{
      "font": {{"bold": true, "size": 11}},
      "fill": {{"color": "pale_blue"}},
      "border": {{
        "top": {{"style": "thin", "color": "gray"}},
        "bottom": {{"style": "thin", "color": "gray"}},
        "left": {{"style": "none"}},
        "right": {{"style": "none"}}
      }}
    }},
    "outline_cell": {{
      "border": {{
        "top": {{"style": "medium", "color": "black"}},
        "bottom": {{"style": "medium", "color": "black"}},
        "left": {{"style": "medium", "color": "black"}},
        "right": {{"style": "medium", "color": "black"}}
      }}
    }}
  }},
  "cell_styles": {{
    "A1:E1": "header",
    "A2:B9": "data_text",
    "C2:E9": "data_number",
    "A5:E5": "subtotal_row",
    "A10:E10": "total_row"
  }},
  "row_heights": {{"1": 28}},
  "conditional_formats": [
    {{
      "type": "color_scale",
      "range": "C2:C9",
      "min_color": "#F8696B",
      "mid_color": "#FFEB84",
      "max_color": "#63BE7B"
    }},
    {{
      "type": "data_bar",
      "range": "D2:D9",
      "bar_color": "#638EC6"
    }},
    {{
      "type": "icon_set",
      "range": "E2:E9",
      "icon_style": "3_arrows"
    }},
    {{
      "type": "cell_value",
      "range": "C2:C9",
      "operator": "less_than",
      "value": 0,
      "font_color": "red",
      "fill_color": "light_red"
    }}
  ]
}}
```

字段说明：
- default_font: 表格的默认字体（大多数单元格使用的字体）
- styles: 样式类定义，每个样式有唯一 ID
- cell_styles: 单元格范围 → 样式 ID 的映射（用 Excel 范围表示，如 "A1:E1"）
- row_heights: 行号 → 行高的映射（仅非默认行高的行）
- conditional_formats: 条件格式列表（见下方详细说明）
- 颜色可以用语义名（dark_blue, light_gray, white 等）或 hex 值（#1F4E79）

**对齐方式（alignment）是复刻精度的关键，请仔细观察：**
- horizontal: "left" | "center" | "right" — 每个区域必须单独判断
- vertical: "top" | "center" | "bottom" — 合并单元格和多行文本通常垂直居中
- wrap_text: true | false — 当单元格内容换行显示时设为 true
- 典型规律：标题行居中、文本列左对齐、数字/金额列右对齐、合计行右对齐或居中
- **不同列的对齐方式往往不同**，请按列或区域分别定义样式，不要将整个数据区归为同一个样式

**边框（border）——四边独立指定是高保真还原的关键：**
- 统一四边相同时：{{"style": "thin", "color": "black"}}
- 四边不同时**必须**分别指定 top/bottom/left/right：
  {{"top": {{"style": "medium", "color": "black"}}, "bottom": {{"style": "double", "color": "black"}}, "left": {{"style": "thin", "color": "light_gray"}}, "right": {{"style": "thin", "color": "light_gray"}}}}
- 某一边无边框时使用：{{"style": "none"}}
- style 可选值: "thin" | "medium" | "thick" | "double" | "hair" | "none"
- **常见四边独立场景（请务必识别）：**
  1. 表头行：底边粗线（medium/thick）区隔数据区，其余三边细线或无线
  2. 合计行：上方粗线 + 下方双线（financial convention），左右细线
  3. 小计行：仅上下细线，左右无线（分组分隔）
  4. 外框粗内框细：外边缘 medium/thick，内部 thin/hair
  5. 右侧分隔线：某些列之间有粗竖线分隔不同数据组
  6. 标题区底边：标题合并单元格仅底边有线，其余三边无线

**条件格式（conditional_formats）说明：**
如果你观察到单元格颜色随数值变化（渐变色、数据条、图标箭头）或特定值有特殊格式，请在 conditional_formats 中描述：
- **color_scale**（颜色刻度/热力图）：数值从小到大对应从一种颜色渐变到另一种
  必填: range, min_color, max_color；可选: mid_color
- **data_bar**（数据条）：单元格内有水平条形图
  必填: range, bar_color
- **icon_set**（图标集）：单元格中有箭头/旗帜/交通灯等图标
  必填: range, icon_style（"3_arrows" | "3_traffic_lights" | "3_flags" | "4_arrows" | "5_arrows"）
- **cell_value**（基于值的格式）：特定条件下单元格有不同字体/背景色
  必填: range, operator（"greater_than" | "less_than" | "between" | "equal" | "not_equal"）, value
  可选: value2（仅 between 时）, font_color, fill_color, bold
- 如果没有观察到条件格式，省略 conditional_formats 字段即可

**注意：**
- 只描述你能看到的样式，不要猜测
- 如果所有单元格样式相同，可以只定义一个 "default" 样式
- 四边边框差异是高保真复刻中最容易被遗漏的细节——请特别注意"""


