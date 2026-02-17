# 颜色速查表可见性修复 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让所有路由模式下 LLM 都能看到 format_cells 支持的完整颜色名列表，消除 general_excel 路由下模型猜测 hex 值的问题。

**Architecture:** 在 format_cells 工具 schema 的 description 中嵌入精简颜色名列表（约 50 字符），使其对所有 skillpack 路由可见。这是"工具层自描述"的正确做法，不需要修改 skillpack 或 system prompt。

**Tech Stack:** Python 3.12, pytest

---

### Task 1: 在 format_cells 工具 schema 中嵌入颜色名列表

**Files:**
- Modify: `excelmanus/tools/format_tools.py:670` — `get_tools` 函数中 `format_cells` 的 ToolDef

**Step 1: 写失败测试**

创建 `tests/test_format_tools_color_schema.py`：

```python
"""测试 format_cells schema 中包含颜色名列表。"""

from excelmanus.tools.format_tools import get_tools


class TestFormatCellsColorSchema:
    """format_cells 的 schema description 应包含常用颜色名列表。"""

    def test_top_description_contains_color_names(self) -> None:
        tools = get_tools()
        format_cells_tool = next(t for t in tools if t.name == "format_cells")
        desc = format_cells_tool.description
        # 应包含关键颜色名
        for color in ["深蓝", "浅蓝", "深红", "天蓝"]:
            assert color in desc, f"format_cells description 缺少颜色名 '{color}'"

    def test_font_color_description_mentions_names(self) -> None:
        tools = get_tools()
        format_cells_tool = next(t for t in tools if t.name == "format_cells")
        font_color_desc = format_cells_tool.input_schema["properties"]["font"]["properties"]["color"]["description"]
        assert "深蓝" in font_color_desc or "颜色名" in font_color_desc

    def test_fill_color_description_mentions_names(self) -> None:
        tools = get_tools()
        format_cells_tool = next(t for t in tools if t.name == "format_cells")
        fill_color_desc = format_cells_tool.input_schema["properties"]["fill"]["properties"]["color"]["description"]
        assert "深蓝" in fill_color_desc or "颜色名" in fill_color_desc
```

**Step 2: 运行测试确认失败**

```bash
pytest tests/test_format_tools_color_schema.py -v
```

预期：FAIL — `assert "深蓝" in desc`

**Step 3: 修改 format_tools.py**

在 `get_tools()` 函数中，修改 `format_cells` 的 ToolDef：

1. 修改顶层 description，在末尾追加颜色名列表：

将：
```python
description="对 Excel 单元格范围应用格式化样式（字体、填充、边框、对齐、数字格式）。颜色参数支持中文名（如 '红色'）或十六进制码（如 'FF0000'）。设置 return_styles=true 可在格式化后直接返回样式快照，省去额外 read_cell_styles 验证",
```

改为：
```python
description="对 Excel 单元格范围应用格式化样式（字体、填充、边框、对齐、数字格式）。颜色参数支持中文名或十六进制码。内置颜色名：红/绿/蓝/黄/白/黑/橙/紫/粉/灰/浅蓝/浅绿/浅黄/浅灰/浅红/浅紫/深蓝/深绿/深红/深灰/天蓝/草绿/金/银/珊瑚。设置 return_styles=true 可在格式化后直接返回样式快照，省去额外 read_cell_styles 验证",
```

2. 修改 font.color 的 description：

将：
```python
"color": {"type": "string", "description": "颜色码或颜色名（如 '红色'、'FF0000'）"},
```

改为：
```python
"color": {"type": "string", "description": "颜色名（如 '深蓝色'、'白色'）或十六进制码（如 'FF0000'），优先使用中文颜色名"},
```

3. 修改 fill.color 的 description：

将：
```python
"color": {"type": "string", "description": "颜色码或颜色名（如 '浅黄色'、'FFFF00'）"},
```

改为：
```python
"color": {"type": "string", "description": "颜色名（如 '深蓝'、'浅灰'）或十六进制码（如 'FFFF00'），优先使用中文颜色名"},
```

**Step 4: 运行测试确认通过**

```bash
pytest tests/test_format_tools_color_schema.py -v
```

预期：3 passed

**Step 5: 运行全量测试确认无回归**

```bash
pytest tests/ -q --tb=short 2>&1 | tail -20
```

**Step 6: 提交**

```bash
git add excelmanus/tools/format_tools.py tests/test_format_tools_color_schema.py
git commit -m "feat: format_cells schema 嵌入完整颜色名列表，所有路由下 LLM 可见"
```
