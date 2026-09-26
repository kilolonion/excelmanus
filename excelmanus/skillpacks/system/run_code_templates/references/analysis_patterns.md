# ExcelManus V2 读取与分析模板

这些模板只编排 ExcelManus 工具。不要在工作区直接使用 `pandas.read_excel()`、`openpyxl.load_workbook()` 或把工作簿对象保存回原路径；单次分析优先直接调用 native 工具，只有循环、跨文件组合或自定义计算才放入 `run_code`。

## 1. 先看结构和表头

```python
from em import observe_spreadsheet

overview = observe_spreadsheet(
    file_path="uploads/source.xlsx",
    mode="overview",
    facets=["data", "presentation", "geometry", "objects"],
)
print({"sheets": overview.get("sheets"), "version": overview.get("content_version"),
       "coverage": overview.get("coverage")})
```

只对需要判断的范围做第二次读取：

```python
sample = observe_spreadsheet(
    file_path="uploads/source.xlsx", sheet="明细", mode="range", range="A1:H20",
    facets=["data", "presentation"], expected_version=overview["content_version"],
)
print(sample["regions"])
```

`regions[].cells` 的键是原始 `row,column`；合并标题、空洞和 `coverage` 都要保留，不能把它们压成一个没有坐标的 DataFrame。

## 2. 描述统计和数据质量

```python
from em import analyze_spreadsheet

profile = analyze_spreadsheet(file_path="uploads/source.xlsx", sheet="明细",
                              mode="profile", max_rows=2000)
quality = analyze_spreadsheet(file_path="uploads/source.xlsx", sheet="明细",
                              mode="quality", max_rows=2000,
                              expected_version=profile["content_version"])
print({"profile": profile.get("profile"), "quality": quality.get("quality_signals")})
```

`max_rows` 是采样/结果上限；返回 `truncated` 或非 complete coverage 时，只报告样本结论。

## 3. 分组、TopN 和重复键

```python
summary = analyze_spreadsheet(
    file_path="uploads/source.xlsx", sheet="明细", mode="aggregate",
    group_by=["部门"],
    aggregations={"金额": ["sum", "mean"], "订单号": "count"},
    sort_by="金额_sum", ascending=False, max_rows=20,
)
duplicates = analyze_spreadsheet(file_path="uploads/source.xlsx", sheet="明细",
                                  mode="distinct", column="订单号", dup_only=True)
print(summary.get("data"), duplicates.get("values"))
```

删除重复行不是 `distinct` 的副作用；确认键和保留策略后，才使用 `apply_spreadsheet_changes` 的 `transform`/`dedupe` 操作。

## 4. 跨表筛选和键匹配

`filter` 用 `conditions=[{"column": ..., "operator": ..., "value": ...}]`，结果中的 `selection` 绑定原始行列。跨表关系优先使用 `aggregate`/`pivot` 的 `join` 或 `query_spreadsheet`，并为每个源带上观察到的版本。右表可能有重复键时，先用 `distinct(dup_only=true)` 量化，再决定是一对一还是一对多。

## 5. 必须自定义计算时

```python
from em import observe_spreadsheet

offset, version, total, processed = 1, None, 0.0, 0
while True:
    page = observe_spreadsheet(file_path="outputs/book.xlsx", sheet="明细",
        mode="range", range=f"A{offset}:H{offset + 999}", facets=["data"],
        expected_version=version)
    version = page["content_version"]
    regions = page.get("regions") or []
    cells = regions[0].get("cells", {}) if regions else {}
    rows = set()
    for key, cell in cells.items():
        row, col = (int(part) for part in key.split(",")); rows.add(row)
        if col == 8:
            if cell.get("f") and cell.get("v") is None:
                raise ValueError("公式没有缓存值，不能当作 0")
            if isinstance(cell.get("v"), (int, float)):
                total += cell["v"]
    processed += len(rows)
    if len(rows) < 1000: break
    offset += 1000
print({"processed_rows": processed, "total": total, "content_version": version})
```

如果需要写回，使用最后一次成功读取的 `version` 作为 `expected_version`，只写目标列或目标 selection。批次中断后先检查当前版本和已提交范围，不重放整段循环。
