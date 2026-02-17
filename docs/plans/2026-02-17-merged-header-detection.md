# 合并标题行检测与列名稳定性修复 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复窗口感知层在遇到合并标题行时列名显示为 Unnamed 导致 LLM 列定位不稳定的问题。

**Architecture:** 三层防御 — (1) 在 `_detect_header_row()` 中利用 openpyxl 合并单元格信息排除宽合并行；(2) 在 `_read_df()` 中增加 Unnamed 列名回退重试；(3) 在 `read_excel()` 返回结果中添加 Unnamed 警告字段，让窗口感知层和 LLM 知道列名不可靠。

**Tech Stack:** Python 3.12, openpyxl, pandas, pytest

---

### Task 1: 在 `_detect_header_row()` 中增加合并单元格感知

**Files:**
- Modify: `excelmanus/tools/data_tools.py` — `_detect_header_row` 函数
- Test: `tests/test_datetime_serialization.py` — `TestDetectHeaderRow` 类

**Step 1: 写失败测试**

在 `tests/test_datetime_serialization.py` 的 `TestDetectHeaderRow` 类末尾添加：

```python
def test_merged_title_row_skipped(self, tmp_path: Path) -> None:
    """合并标题行（跨多列）应被跳过，检测到真正的 header。"""
    wb = Workbook()
    ws = wb.active
    # 第 0 行：合并标题 "2024年销售数据" 跨 A1:F1
    ws.append(["2024年销售数据", None, None, None, None, None])
    ws.merge_cells("A1:F1")
    # 第 1 行：真正的表头
    ws.append(["月份", "产品", "地区", "销售额", "成本", "利润"])
    # 第 2 行：数据
    ws.append(["1月", "产品A", "华东", 10000, 6000, 4000])
    ws.append(["2月", "产品B", "华北", 12000, 7000, 5000])
    fp = tmp_path / "merged_title.xlsx"
    wb.save(fp)
    assert data_tools._detect_header_row(fp, None) == 1

def test_merged_title_two_rows_skipped(self, tmp_path: Path) -> None:
    """两行合并标题都应被跳过。"""
    wb = Workbook()
    ws = wb.active
    ws.append(["年度销售报表", None, None, None])
    ws.merge_cells("A1:D1")
    ws.append(["生成时间：2024-01", None, None, None])
    ws.merge_cells("A2:D2")
    ws.append(["月份", "产品", "销售额", "利润"])
    ws.append(["1月", "A", 10000, 4000])
    fp = tmp_path / "two_merged_titles.xlsx"
    wb.save(fp)
    assert data_tools._detect_header_row(fp, None) == 2
```

**Step 2: 运行测试确认失败**

Run: `pytest tests/test_datetime_serialization.py::TestDetectHeaderRow::test_merged_title_row_skipped tests/test_datetime_serialization.py::TestDetectHeaderRow::test_merged_title_two_rows_skipped -v`
Expected: FAIL（当前逻辑不感知合并单元格）

**Step 3: 实现合并行检测**

修改 `excelmanus/tools/data_tools.py` 中的 `_detect_header_row` 函数。在打开 workbook 后、调用 `_guess_header_row_from_rows` 前，收集宽合并行集合，然后传给打分函数：

1. 在 `_detect_header_row` 中，将 `load_workbook` 改为 `read_only=False`（需要读取 merged_cells），收集宽合并行：

```python
def _detect_header_row(
    safe_path: Any,
    sheet_name: str | None,
    max_scan: int = _HEADER_SCAN_ROWS,
    max_scan_columns: int = _HEADER_SCAN_COLS,
) -> int | None:
    """启发式检测 header 行号（0-indexed）。

    策略：
    1. 扫描前 N 行（默认 30）和前 M 列（默认 200）；
    2. 对每一行按"文本占比、关键字、唯一性、数据行特征"打分；
    3. 跨多列合并的行（列跨度 > 50% 总列数）视为标题行，自动排除；
    4. 选择分数最高者作为表头。

    Returns:
        检测到的 header 行号（从0开始），无法确定时返回 None。
    """
    try:
        from openpyxl import load_workbook
        wb = load_workbook(safe_path, read_only=False, data_only=True)
    except Exception:
        return None

    try:
        if sheet_name:
            resolved = resolve_sheet_name(sheet_name, wb.sheetnames)
            if resolved:
                ws = wb[resolved]
            else:
                ws = wb.active
        else:
            ws = wb.active
        if ws is None:
            return None

        scan_cols = max(1, min(max_scan_columns, ws.max_column or max_scan_columns))

        # 收集宽合并行（列跨度 > 50% 总列数）
        wide_merged_rows: set[int] = set()
        for merged_range in ws.merged_cells.ranges:
            col_span = merged_range.max_col - merged_range.min_col + 1
            if col_span > scan_cols * 0.5:
                for r in range(merged_range.min_row, merged_range.max_row + 1):
                    if r <= max_scan:
                        wide_merged_rows.add(r - 1)  # 转为 0-indexed

        rows: list[list[Any]] = []
        for row in ws.iter_rows(
            min_row=1,
            max_row=max_scan,
            min_col=1,
            max_col=scan_cols,
            values_only=True,
        ):
            rows.append([_normalize_cell(c) for c in row])

        if not rows:
            return None

        return _guess_header_row_from_rows(rows, max_scan=max_scan, skip_rows=wide_merged_rows)
    finally:
        wb.close()
```

2. 修改 `_guess_header_row_from_rows` 签名，增加 `skip_rows` 参数：

```python
def _guess_header_row_from_rows(
    rows: list[list[Any]], *, max_scan: int | None = None, skip_rows: set[int] | None = None,
) -> int | None:
    """基于抽样行猜测 header 行号（0-indexed）。"""
    if not rows:
        return None

    upper = len(rows) if max_scan is None else min(len(rows), max_scan)
    best_row: int | None = None
    best_score = float("-inf")

    for idx in range(upper):
        if skip_rows and idx in skip_rows:
            continue
        row = _trim_trailing_nulls_generic(rows[idx])
        next_row = _trim_trailing_nulls_generic(rows[idx + 1]) if idx + 1 < upper else None
        score = _header_row_score(row, idx, next_row)
        if score > best_score:
            best_score = score
            best_row = idx

    if best_row is None or best_score == float("-inf"):
        return None
    return best_row
```

**Step 4: 运行测试确认通过**

Run: `pytest tests/test_datetime_serialization.py::TestDetectHeaderRow -v`
Expected: ALL PASS（包括新增的 2 个和原有的 6 个）

**Step 5: 运行全量 data_tools 相关测试确认无回归**

Run: `pytest tests/test_datetime_serialization.py -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add excelmanus/tools/data_tools.py tests/test_datetime_serialization.py
git commit -m "feat(data_tools): 在 header 检测中排除宽合并标题行"
```

---

### Task 2: 在 `_read_df()` 中增加 Unnamed 列名回退重试

**Files:**
- Modify: `excelmanus/tools/data_tools.py` — `_read_df` 函数
- Test: `tests/test_datetime_serialization.py` — 新增 `TestUnnamedFallback` 类

**Step 1: 写失败测试**

在 `tests/test_datetime_serialization.py` 末尾添加新测试类：

```python
class TestUnnamedFallback:
    """当自动检测的 header_row 产生 Unnamed 列名时，应自动回退到下一行。"""

    def test_fallback_on_unnamed_columns(self, tmp_path: Path) -> None:
        """合并标题行导致 Unnamed 列名时，_read_df 应自动重试下一行。"""
        wb = Workbook()
        ws = wb.active
        # 第 0 行：只有 A1 有值（合并标题），其余为空
        ws["A1"] = "季度销售汇总"
        # 不做 merge_cells，模拟 values_only 下只有 1 个非空值的场景
        # 但 _HEADER_MIN_NON_EMPTY=3 会让 row0 得 -inf，所以这里用 3 个非空值
        ws["A1"] = "标题A"
        ws["B1"] = "标题B"
        ws["C1"] = "标题C"
        # 第 1 行：真正的表头
        ws["A2"] = "月份"
        ws["B2"] = "产品"
        ws["C2"] = "销售额"
        # 第 2-3 行：数据
        ws["A3"] = "1月"
        ws["B3"] = "产品A"
        ws["C3"] = 10000
        ws["A4"] = "2月"
        ws["B4"] = "产品B"
        ws["C4"] = 12000
        fp = tmp_path / "unnamed_fallback.xlsx"
        wb.save(fp)

        from excelmanus.tools.data_tools import _read_df
        df, effective_header = _read_df(fp, None)
        # 不管最终选了哪行，列名不应包含 Unnamed
        unnamed_count = sum(1 for c in df.columns if str(c).startswith("Unnamed"))
        assert unnamed_count == 0, f"列名中仍有 Unnamed: {list(df.columns)}"
```

**Step 2: 运行测试确认失败**

Run: `pytest tests/test_datetime_serialization.py::TestUnnamedFallback::test_fallback_on_unnamed_columns -v`
Expected: 可能 PASS 也可能 FAIL，取决于 Task 1 的合并检测是否覆盖此场景。如果 PASS 则此 Task 仍需实现作为防御层。

**Step 3: 实现 Unnamed 回退逻辑**

修改 `excelmanus/tools/data_tools.py` 中的 `_read_df` 函数：

```python
def _read_df(
    safe_path: Any,
    sheet_name: str | None,
    max_rows: int | None = None,
    header_row: int | None = None,
) -> tuple[pd.DataFrame, int]:
    """统一读取 Excel 为 DataFrame，含 header 自动检测 + 公式列求值。

    当自动检测的 header_row 导致超过 50% 列名为 Unnamed 时，
    自动向下尝试最多 5 行寻找更合理的表头。

    Returns:
        (DataFrame, effective_header_row) 元组。
    """
    kwargs = _build_read_kwargs(safe_path, sheet_name, max_rows=max_rows, header_row=header_row)
    effective_header = kwargs.get("header", 0)
    df = pd.read_excel(**kwargs)

    # 仅在自动检测模式下（用户未显式指定 header_row）执行 Unnamed 回退
    if header_row is None:
        unnamed_ratio = (
            sum(1 for c in df.columns if str(c).startswith("Unnamed"))
            / max(len(df.columns), 1)
        )
        if unnamed_ratio > 0.5:
            logger.info(
                "自动检测 header_row=%d 产生 %.0f%% Unnamed 列名，尝试回退",
                effective_header, unnamed_ratio * 100,
            )
            for try_header in range(effective_header + 1, min(effective_header + 6, 30)):
                retry_kwargs = {**kwargs, "header": try_header}
                try:
                    df_retry = pd.read_excel(**retry_kwargs)
                except Exception:
                    break
                if df_retry.empty:
                    break
                retry_unnamed = sum(
                    1 for c in df_retry.columns if str(c).startswith("Unnamed")
                )
                if retry_unnamed / max(len(df_retry.columns), 1) < 0.3:
                    logger.info("回退成功：header_row=%d → %d", effective_header, try_header)
                    df = df_retry
                    effective_header = try_header
                    break

    df = _resolve_formula_columns(df, safe_path, sheet_name, effective_header)
    return df, effective_header
```

**Step 4: 运行测试确认通过**

Run: `pytest tests/test_datetime_serialization.py::TestUnnamedFallback -v`
Expected: PASS

**Step 5: 运行全量测试确认无回归**

Run: `pytest tests/test_datetime_serialization.py -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add excelmanus/tools/data_tools.py tests/test_datetime_serialization.py
git commit -m "feat(data_tools): Unnamed 列名超 50% 时自动回退重试 header_row"
```

---

### Task 3: 在 `read_excel()` 返回结果中添加 Unnamed 警告

**Files:**
- Modify: `excelmanus/tools/data_tools.py` — `read_excel` 函数
- Test: `tests/test_datetime_serialization.py` — 新增测试

**Step 1: 写失败测试**

```python
class TestUnnamedWarning:
    """read_excel 返回结果中应包含 Unnamed 列名警告。"""

    def test_unnamed_warning_present(self, tmp_path: Path) -> None:
        """当列名中仍有 Unnamed 时，summary 应包含警告字段。"""
        wb = Workbook()
        ws = wb.active
        # 构造一个即使回退也无法消除 Unnamed 的场景
        ws.append([None, None, None])
        ws.append([None, None, None])
        ws.append([1, 2, 3])
        ws.append([4, 5, 6])
        fp = tmp_path / "all_unnamed.xlsx"
        wb.save(fp)

        data_tools.init_guard(str(tmp_path))
        result_json = json.loads(data_tools.read_excel(str(fp)))
        # 应该有 unnamed_columns_warning 字段
        assert "unnamed_columns_warning" in result_json

    def test_no_warning_when_clean(self, tmp_path: Path) -> None:
        """列名正常时不应有警告。"""
        wb = Workbook()
        ws = wb.active
        ws.append(["月份", "产品", "销售额"])
        ws.append(["1月", "A", 10000])
        fp = tmp_path / "clean.xlsx"
        wb.save(fp)

        data_tools.init_guard(str(tmp_path))
        result_json = json.loads(data_tools.read_excel(str(fp)))
        assert "unnamed_columns_warning" not in result_json
```

**Step 2: 运行测试确认失败**

Run: `pytest tests/test_datetime_serialization.py::TestUnnamedWarning -v`
Expected: FAIL（`unnamed_columns_warning` 字段尚未添加）

**Step 3: 实现警告字段**

在 `excelmanus/tools/data_tools.py` 的 `read_excel` 函数中，在构建 `summary` 之后、返回之前添加：

```python
    # Unnamed 列名警告：提醒 LLM 列名不可靠，建议指定 header_row
    unnamed_cols = [str(c) for c in df.columns if str(c).startswith("Unnamed")]
    if unnamed_cols:
        summary["unnamed_columns_warning"] = (
            f"检测到 {len(unnamed_cols)} 个 Unnamed 列名（共 {len(df.columns)} 列），"
            f"可能是合并标题行导致。建议使用 header_row 参数指定真正的列头行号重新读取。"
        )
```

插入位置：在 `summary["detected_header_row"]` 赋值之后、`include_set` 处理之前。

**Step 4: 运行测试确认通过**

Run: `pytest tests/test_datetime_serialization.py::TestUnnamedWarning -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/tools/data_tools.py tests/test_datetime_serialization.py
git commit -m "feat(data_tools): read_excel 返回 Unnamed 列名警告字段"
```

---

### Task 4: 在窗口感知 `extract_columns()` 中过滤 Unnamed 列名

**Files:**
- Modify: `excelmanus/window_perception/ingest.py` — `extract_columns` 函数
- Test: `tests/test_ingest.py`（如存在）或新建 `tests/test_ingest_columns.py`

**Step 1: 写失败测试**

新建 `tests/test_ingest_columns.py`：

```python
"""extract_columns 对 Unnamed 列名的处理测试。"""

from __future__ import annotations

from excelmanus.window_perception.ingest import extract_columns


class TestExtractColumnsUnnamed:
    """当 columns 包含 Unnamed 时，应从数据行推断更有意义的名称。"""

    def test_unnamed_columns_get_sample_annotation(self) -> None:
        """Unnamed 列名应被标注数据样本。"""
        result_json = {
            "columns": ["Unnamed: 0", "Unnamed: 1", "Unnamed: 2"],
        }
        rows = [
            {"Unnamed: 0": "1月", "Unnamed: 1": "产品A", "Unnamed: 2": 10000},
            {"Unnamed: 0": "2月", "Unnamed: 1": "产品B", "Unnamed: 2": 12000},
        ]
        columns = extract_columns(result_json, rows)
        # 列名不应仍是纯 Unnamed
        for col in columns:
            assert not col.name.startswith("Unnamed:"), f"列名未被标注: {col.name}"

    def test_clean_columns_unchanged(self) -> None:
        """正常列名不应被修改。"""
        result_json = {
            "columns": ["月份", "产品", "销售额"],
        }
        rows = [
            {"月份": "1月", "产品": "A", "销售额": 10000},
        ]
        columns = extract_columns(result_json, rows)
        names = [col.name for col in columns]
        assert names == ["月份", "产品", "销售额"]
```

**Step 2: 运行测试确认失败**

Run: `pytest tests/test_ingest_columns.py -v`
Expected: FAIL（Unnamed 列名未被标注）

**Step 3: 实现 Unnamed 列名标注**

修改 `excelmanus/window_perception/ingest.py` 中的 `extract_columns` 函数：

```python
def extract_columns(result_json: dict[str, Any] | None, rows: list[dict[str, Any]]) -> list[ColumnDef]:
    """提取并推断列信息。

    当列名为 Unnamed 模式时，从数据行中提取样本值作为标注，
    帮助 LLM 更准确地识别列含义。
    """
    raw_columns = result_json.get("columns") if isinstance(result_json, dict) else None
    names: list[str] = []

    if isinstance(raw_columns, list):
        names = [str(item).strip() for item in raw_columns if str(item).strip()]
    elif rows:
        names = [str(key) for key in rows[0].keys()]

    if not names:
        return []

    # 对 Unnamed 列名进行样本标注
    has_unnamed = any(name.startswith("Unnamed") for name in names)
    if has_unnamed and rows:
        annotated: list[str] = []
        for name in names:
            if name.startswith("Unnamed"):
                # 从前 3 行数据中取第一个非空值作为样本
                sample = None
                for row in rows[:3]:
                    val = row.get(name)
                    if val is not None and str(val).strip():
                        sample = str(val).strip()
                        break
                if sample:
                    # 截断过长的样本值
                    if len(sample) > 20:
                        sample = sample[:20] + "…"
                    annotated.append(f"{name}(样本:{sample})")
                else:
                    annotated.append(name)
            else:
                annotated.append(name)
        names = annotated

    inferred: list[ColumnDef] = []
    for idx, name in enumerate(names):
        # 用原始列名从 rows 中取值（rows 的 key 仍是原始 Unnamed 名）
        original_key = name.split("(样本:")[0] if "(样本:" in name else name
        values = [row.get(original_key) for row in rows[:50]]
        inferred.append(ColumnDef(name=name, inferred_type=_infer_type(values, fallback_idx=idx)))
    return inferred
```

**Step 4: 运行测试确认通过**

Run: `pytest tests/test_ingest_columns.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add excelmanus/window_perception/ingest.py tests/test_ingest_columns.py
git commit -m "feat(ingest): Unnamed 列名自动标注数据样本值"
```

---

### Task 5: 全量回归测试

**Step 1: 运行全量测试**

Run: `pytest tests/ -v --timeout=60`
Expected: ALL PASS，无回归

**Step 2: 如有失败，修复后重新运行**

**Step 3: 最终 Commit**

```bash
git add -A
git commit -m "test: 全量回归通过 — 合并标题行检测与列名稳定性修复"
```
