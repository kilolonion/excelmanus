# Viewport 列截断修复 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复直接路由模式下 `read_cell_styles` 不返回 shape 信息导致窗口感知层 `total_cols=0`、模型从 viewport range 推断列数造成列截断的问题。

**Architecture:** 三层防御策略——第一层在 `read_cell_styles` 返回中注入 `rows`/`columns` 字段（根因修复）；第二层在 `format_basic/SKILL.md` 中增加列数感知指引（行为引导）；第三层在 `render_tool_perception_block` 中增加列截断警告（感知增强）。

**Tech Stack:** Python 3.12, openpyxl, pytest

---

### Task 1: `read_cell_styles` 返回 shape 信息（根因修复）

**Files:**
- Modify: `excelmanus/tools/format_tools.py:243` — `read_cell_styles` 函数
- Create: `tests/test_format_tools_shape.py` — 新测试文件

**Step 1: 写失败测试**

```python
"""测试 read_cell_styles 返回 shape 信息。"""

import json
from pathlib import Path

import pytest
from openpyxl import Workbook


@pytest.fixture()
def sample_xlsx(tmp_path: Path) -> Path:
    """创建一个 3 行 5 列的测试 Excel 文件。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    # 写入 3 行 5 列数据
    for r in range(1, 4):
        for c in range(1, 6):
            ws.cell(row=r, column=c, value=f"R{r}C{c}")
    # 给 A1 加粗
    ws["A1"].font = ws["A1"].font.copy(bold=True)
    path = tmp_path / "test_shape.xlsx"
    wb.save(path)
    wb.close()
    return path


class TestReadCellStylesShape:
    """read_cell_styles 应在返回中包含 rows 和 columns 字段。"""

    def test_returns_rows_and_columns(self, sample_xlsx: Path, monkeypatch) -> None:
        """返回 JSON 中应包含 rows 和 columns 顶层字段。"""
        from excelmanus.tools.format_tools import read_cell_styles
        from excelmanus.security.guard import FileAccessGuard

        guard = FileAccessGuard(str(sample_xlsx.parent))
        monkeypatch.setattr(
            "excelmanus.tools.format_tools._get_guard", lambda: guard
        )

        result_str = read_cell_styles(
            file_path=str(sample_xlsx),
            cell_range="A1:E1",
        )
        result = json.loads(result_str)

        assert result["rows"] == 3, f"expected rows=3, got {result.get('rows')}"
        assert result["columns"] == 5, f"expected columns=5, got {result.get('columns')}"

    def test_shape_fields_compatible_with_extract_shape(self, sample_xlsx: Path, monkeypatch) -> None:
        """rows/columns 字段应能被 extract_shape() 正确提取。"""
        from excelmanus.tools.format_tools import read_cell_styles
        from excelmanus.security.guard import FileAccessGuard
        from excelmanus.window_perception.extractor import extract_shape

        guard = FileAccessGuard(str(sample_xlsx.parent))
        monkeypatch.setattr(
            "excelmanus.tools.format_tools._get_guard", lambda: guard
        )

        result_str = read_cell_styles(
            file_path=str(sample_xlsx),
            cell_range="1:1",
        )
        result = json.loads(result_str)
        total_rows, total_cols = extract_shape(result)

        assert total_rows == 3
        assert total_cols == 5
```

**Step 2: 运行测试确认失败**

```bash
pytest tests/test_format_tools_shape.py -v
```

预期：FAIL — `KeyError: 'rows'` 或 `assert result["rows"] == 3`

**Step 3: 实现最小改动**

在 `excelmanus/tools/format_tools.py` 的 `read_cell_styles` 函数中，找到构建 `result` dict 的位置（约第 355 行），在 `"total_cells"` 之后加入 `"rows"` 和 `"columns"`：

```python
    result: dict[str, Any] = {
        "status": "success",
        "file": safe_path.name,
        "range": cell_range,
        "total_cells": total_cells,
        "rows": ws.max_row or 0,
        "columns": ws.max_column or 0,
        "summary": {
            ...
        },
    }
```

注意：`ws` 变量在 `wb.close()` 之前仍然可用，但 `wb.close()` 在 result 构建之前被调用了。需要在 `wb.close()` 之前先保存 `max_row` 和 `max_column`：

```python
    # 在 wb.close() 之前保存 shape 信息
    sheet_max_row = ws.max_row or 0
    sheet_max_col = ws.max_column or 0

    wb.close()

    # 构建合并范围列表
    range_merged: list[str] = [str(mr) for mr in merged_ranges]

    result: dict[str, Any] = {
        "status": "success",
        "file": safe_path.name,
        "range": cell_range,
        "total_cells": total_cells,
        "rows": sheet_max_row,
        "columns": sheet_max_col,
        "summary": {
            ...
        },
    }
```

**Step 4: 运行测试确认通过**

```bash
pytest tests/test_format_tools_shape.py -v
```

预期：2 passed

**Step 5: 提交**

```bash
git add excelmanus/tools/format_tools.py tests/test_format_tools_shape.py
git commit -m "fix: read_cell_styles 返回 rows/columns 字段，修复窗口感知层 total_cols=0"
```

---

### Task 2: `format_basic/SKILL.md` 列数感知指引（行为引导）

**Files:**
- Modify: `excelmanus/skillpacks/system/format_basic/SKILL.md`

**Step 1: 修改 SKILL.md**

在"1. 感知优先"部分末尾追加列数感知指引：

```markdown
1. 感知优先
- 修改样式前**必须**先调用 `read_cell_styles` 了解目标范围的现有样式。
- 如果用户提到"把红色改为蓝色"等基于现有样式的需求，先用 `read_cell_styles` 定位哪些单元格有该样式。
- 对整表样式概览，可使用 `read_excel(include=["styles"])` 快速获取压缩样式类；也可按需附加 charts/images/freeze_panes 等维度。
- **列数感知**：格式化整行时，优先使用行引用（如 `1:1`）而非具体列范围（如 `A1:J1`），避免因视口限制遗漏列。如需精确列范围，从感知块的 `range: NNNr x NNc` 读取总列数。
```

**Step 2: 验证 SKILL.md 格式正确**

确认 frontmatter 未被破坏，markdown 语法正确。

**Step 3: 提交**

```bash
git add excelmanus/skillpacks/system/format_basic/SKILL.md
git commit -m "docs: format_basic SKILL.md 增加列数感知指引，引导模型使用行引用避免列截断"
```

---

### Task 3: `render_tool_perception_block` 列截断警告（感知增强）

**Files:**
- Modify: `excelmanus/window_perception/renderer.py:150` — `render_tool_perception_block` 函数
- Modify: `tests/test_window_perception_renderer.py` — 追加测试

**Step 1: 写失败测试**

在 `tests/test_window_perception_renderer.py` 的 `TestWindowRenderer` 类中追加：

```python
    def test_tool_perception_block_column_truncation_warning(self) -> None:
        """当 total_cols > visible_cols 时，perception block 应包含列截断警告。"""
        window = make_window(
            id="sheet_trunc",
            type=WindowType.SHEET,
            title="sheet",
            file_path="sales.xlsx",
            sheet_name="Q1",
            sheet_tabs=["Q1"],
            viewport=Viewport(
                range_ref="A1:J25",
                visible_rows=25,
                visible_cols=10,
                total_rows=2004,
                total_cols=12,
            ),
        )
        payload = build_tool_perception_payload(window)
        assert payload is not None
        block = render_tool_perception_block(payload)
        assert "2004r x 12c" in block
        assert "视口仅显示 10 列" in block or "总列数: 12" in block
```

**Step 2: 运行测试确认失败**

```bash
pytest tests/test_window_perception_renderer.py::TestWindowRenderer::test_tool_perception_block_column_truncation_warning -v
```

预期：FAIL — `assert "视口仅显示 10 列" in block` 失败

**Step 3: 实现最小改动**

在 `excelmanus/window_perception/renderer.py` 的 `render_tool_perception_block` 函数中，在 `viewport` 行之后、`freeze` 行之前，插入列截断警告：

```python
    lines = [
        "--- perception ---",
        ...
        f"viewport: {viewport.get('range') or '未知'}",
    ]

    # 列截断警告
    _visible_cols = viewport.get("visible_cols", 0)
    _total_cols = viewport.get("total_cols", 0)
    if _total_cols > _visible_cols > 0:
        lines.append(
            f"⚠️ 列截断：工作表共 {_total_cols} 列，视口仅显示 {_visible_cols} 列，"
            f"格式化整行时建议使用行引用（如 1:1）"
        )

    freeze = payload.get("freeze_panes")
    ...
```

**Step 4: 运行测试确认通过**

```bash
pytest tests/test_window_perception_renderer.py -v
```

预期：全部 PASS（包括新增的 `test_tool_perception_block_column_truncation_warning`）

**Step 5: 运行全量测试确认无回归**

```bash
pytest tests/ -q --tb=short
```

预期：无新增失败

**Step 6: 提交**

```bash
git add excelmanus/window_perception/renderer.py tests/test_window_perception_renderer.py
git commit -m "feat: render_tool_perception_block 增加列截断警告，提醒模型使用行引用"
```
