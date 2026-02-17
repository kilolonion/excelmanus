# Anchored 默认模式 + 写后聚焦渲染 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将窗口感知层默认模式从 enriched 改为 anchored，并在写操作后只渲染受影响区域的数据行，其他缓存块折叠为一行摘要，以节省 token。

**Architecture:** 在 SheetCache 中新增 `last_op_kind` 和 `last_write_range` 字段追踪最近操作类型。`_apply_ingest` 三条路径（read/write/filter）末尾设置标记。`render_window_wurm_full` 根据标记决定全量渲染还是聚焦渲染。adaptive 默认模式改为 anchored。

**Tech Stack:** Python 3.12, openpyxl (range_boundaries), pytest

---

### Task 1: SheetCache 新增 last_op_kind / last_write_range 字段

**Files:**
- Modify: `excelmanus/window_perception/domain.py` — `SheetCache` dataclass (Line 246)
- Modify: `excelmanus/window_perception/domain.py` — `SheetWindow` class — 新增 property

**Step 1: 在 SheetCache 中新增字段**

在 `excelmanus/window_perception/domain.py` 的 `SheetCache` dataclass 中，在 `unfiltered_buffer` 之后新增：

```python
last_op_kind: str | None = None        # "read" | "write" | "filter" | None
last_write_range: str | None = None    # 写操作受影响范围
```

**Step 2: 在 SheetWindow 中新增 property**

在 `SheetWindow` 类中（在 `unfiltered_buffer` property 之后）新增：

```python
@property
def last_op_kind(self) -> str | None:
    return self.data.cache.last_op_kind

@last_op_kind.setter
def last_op_kind(self, value: str | None) -> None:
    self.data.cache.last_op_kind = value

@property
def last_write_range(self) -> str | None:
    return self.data.cache.last_write_range

@last_write_range.setter
def last_write_range(self, value: str | None) -> None:
    self.data.cache.last_write_range = value
```

**Step 3: 验证无语法错误**

Run: `python -c "from excelmanus.window_perception.domain import SheetWindow; w = SheetWindow.new(id='t', title='t', file_path='f', sheet_name='s'); print(w.last_op_kind, w.last_write_range)"`
Expected: `None None`

**Step 4: Commit**

```bash
git add excelmanus/window_perception/domain.py
git commit -m "feat(window): SheetCache 新增 last_op_kind/last_write_range 字段"
```

---

### Task 2: _apply_ingest 三条路径设置操作类型标记

**Files:**
- Modify: `excelmanus/window_perception/manager.py` — `_apply_ingest` 方法 (Line 860)

**Step 1: 在 _apply_ingest 的三条路径中设置标记**

在 `_apply_ingest` 方法中，找到末尾的 `self._set_window_field(window, "detail_level", DetailLevel.FULL)` 这一行之前，根据路径设置标记。

具体改动：在 filter 路径的 `change = make_change_record(...)` 之后、write 路径的 `change = make_change_record(...)` 之后、read 路径的 `change = make_change_record(...)` 之后，分别插入标记设置。

最简洁的做法是在三个分支结束后、公共尾部之前，根据 `change.operation` 设置：

在 `self._set_window_field(window, "detail_level", DetailLevel.FULL)` 之前插入：

```python
# 设置操作类型标记，供渲染器聚焦渲染使用
if change.operation == "write":
    self._set_window_field(window, "last_op_kind", "write")
    self._set_window_field(window, "last_write_range", change.affected_range)
elif change.operation == "filter":
    self._set_window_field(window, "last_op_kind", "filter")
    self._set_window_field(window, "last_write_range", None)
else:
    self._set_window_field(window, "last_op_kind", "read")
    self._set_window_field(window, "last_write_range", None)
```

**Step 2: 验证导入无报错**

Run: `python -c "from excelmanus.window_perception.manager import WindowPerceptionManager; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add excelmanus/window_perception/manager.py
git commit -m "feat(window): _apply_ingest 设置 last_op_kind/last_write_range 标记"
```

---

### Task 3: renderer.py — 写后聚焦渲染逻辑

**Files:**
- Modify: `excelmanus/window_perception/renderer.py` — `render_window_wurm_full` 函数 (Line 469)
- Modify: `excelmanus/window_perception/renderer.py` — 新增 `_range_overlaps` 辅助函数

**Step 1: 新增 _range_overlaps 辅助函数**

在 `renderer.py` 文件中（`render_window_wurm_full` 函数之前或之后），新增：

```python
def _range_overlaps(range_a: str, range_b: str) -> bool:
    """判断两个 Excel 范围是否有交集。"""
    try:
        from openpyxl.utils.cell import range_boundaries
        a_min_col, a_min_row, a_max_col, a_max_row = range_boundaries(range_a)
        b_min_col, b_min_row, b_max_col, b_max_row = range_boundaries(range_b)
    except (ValueError, TypeError):
        return False
    return not (
        a_max_row < b_min_row or b_max_row < a_min_row
        or a_max_col < b_min_col or b_max_col < a_min_col
    )
```

**Step 2: 修改 render_window_wurm_full 签名和渲染逻辑**

在 `render_window_wurm_full` 中：

2a. 从 window 对象读取标记（不改签名，保持向后兼容）：

在函数体开头（`profile = _normalize_intent_profile(...)` 之后）插入：

```python
last_op_kind = getattr(window, "last_op_kind", None)
focus_range = getattr(window, "last_write_range", None)
is_write_focus = last_op_kind == "write" and bool(focus_range)
```

2b. 修改 cached_ranges 渲染循环。将现有的：

```python
        for cached in ranges:
            marker = " [current-viewport]" if cached.is_current_viewport else ""
            rows_to_render = cached.rows
            if profile.get("show_quality"):
                rows_to_render = _pick_anomaly_rows(cached.rows, limit=render_max_rows) or cached.rows
            lines.append(f"-- cached {cached.range_ref} ({len(cached.rows)}r){marker} --")
            lines.extend(_render_pipe_rows(
                rows=rows_to_render,
                columns=column_names,
                max_rows=render_max_rows,
                current_iteration=current_iteration,
                changed_indices=set(window.change_log[-1].affected_row_indices) if window.change_log else set(),
            ))
```

替换为：

```python
        any_focus_hit = False
        for cached in ranges:
            marker = " [current-viewport]" if cached.is_current_viewport else ""
            if is_write_focus:
                if _range_overlaps(cached.range_ref, focus_range):
                    any_focus_hit = True
                    focus_marker = " [FOCUS·STALE]" if window.stale_hint else " [FOCUS]"
                    lines.append(f"-- cached {cached.range_ref} ({len(cached.rows)}r){marker}{focus_marker} --")
                    rows_to_render = cached.rows
                    if profile.get("show_quality"):
                        rows_to_render = _pick_anomaly_rows(cached.rows, limit=render_max_rows) or cached.rows
                    lines.extend(_render_pipe_rows(
                        rows=rows_to_render,
                        columns=column_names,
                        max_rows=render_max_rows,
                        current_iteration=current_iteration,
                        changed_indices=set(window.change_log[-1].affected_row_indices) if window.change_log else set(),
                    ))
                else:
                    lines.append(f"-- cached {cached.range_ref} ({len(cached.rows)}r){marker} [collapsed] --")
            else:
                rows_to_render = cached.rows
                if profile.get("show_quality"):
                    rows_to_render = _pick_anomaly_rows(cached.rows, limit=render_max_rows) or cached.rows
                lines.append(f"-- cached {cached.range_ref} ({len(cached.rows)}r){marker} --")
                lines.extend(_render_pipe_rows(
                    rows=rows_to_render,
                    columns=column_names,
                    max_rows=render_max_rows,
                    current_iteration=current_iteration,
                    changed_indices=set(window.change_log[-1].affected_row_indices) if window.change_log else set(),
                ))
        if is_write_focus and not any_focus_hit:
            lines.append(f"⚠️ 写入范围 {focus_range} 不在缓存视口中，数据可能需要重新读取")
```

2c. 同样修改 `else` 分支（无 cached_ranges 时用 data_buffer）。在 `else:` 分支中，如果 `is_write_focus`，只渲染前几行 + 写入确认摘要：

```python
    else:
        rows_to_render = window.data_buffer
        if profile.get("show_quality"):
            rows_to_render = _pick_anomaly_rows(window.data_buffer, limit=render_max_rows) or window.data_buffer
        if is_write_focus:
            lines.append(f"data: (write-focus @ {focus_range})")
        else:
            lines.append("data:")
        lines.extend(_render_pipe_rows(
            rows=rows_to_render,
            columns=column_names,
            max_rows=render_max_rows,
            current_iteration=current_iteration,
            changed_indices=set(window.change_log[-1].affected_row_indices) if window.change_log else set(),
        ))
```

**Step 3: 验证无语法错误**

Run: `python -c "from excelmanus.window_perception.renderer import render_window_wurm_full; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add excelmanus/window_perception/renderer.py
git commit -m "feat(window): render_window_wurm_full 写后聚焦渲染 + _range_overlaps"
```

---

### Task 4: adaptive.py — 默认模式改为 anchored

**Files:**
- Modify: `excelmanus/window_perception/adaptive.py`

**Step 1: 修改 _SAFE_MODE 和 _DEFAULT_PREFIX_MAP**

将 `_SAFE_MODE = "enriched"` 改为 `_SAFE_MODE = "anchored"`。

将 `_DEFAULT_PREFIX_MAP` 中 `("deepseek", "enriched")` 改为 `("deepseek", "anchored")`。

同时将 `_SAFE_MODE` 重命名为 `_DEFAULT_MODE`（更准确的语义），并更新所有引用（`reset`、`_resolve_initial_mode`、`_normalize_requested_mode`）。

**Step 2: 验证**

Run: `python -c "from excelmanus.window_perception.adaptive import AdaptiveModeSelector; s = AdaptiveModeSelector(); print(s.select_mode(model_id='deepseek-v3', requested_mode='adaptive'))"`
Expected: `anchored`

Run: `python -c "from excelmanus.window_perception.adaptive import AdaptiveModeSelector; s = AdaptiveModeSelector(); print(s.select_mode(model_id='unknown-model', requested_mode='adaptive'))"`
Expected: `anchored`

**Step 3: Commit**

```bash
git add excelmanus/window_perception/adaptive.py
git commit -m "feat(window): 默认模式从 enriched 改为 anchored"
```

---

### Task 5: 运行全量测试验证无回归

**Step 1: 运行测试**

Run: `pytest tests/ -x -q --timeout=30 2>&1 | tail -20`

如果有失败，分析原因并修复。常见可能的失败：
- 测试中硬编码了 `enriched` 作为默认模式的断言
- 测试中 mock 了 `_SAFE_MODE` 常量

**Step 2: 修复测试（如有）**

根据失败信息修复。

**Step 3: 最终 Commit**

```bash
git add -A
git commit -m "feat(window): anchored 默认模式 + 写后聚焦渲染 完整实现"
```
