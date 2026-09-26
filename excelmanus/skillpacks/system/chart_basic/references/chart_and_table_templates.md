# ExcelManus V2 图表与表格模板

工作区 xlsx 的改写统一走 `apply_spreadsheet_changes`。先用 `observe_spreadsheet` 或 `analyze_spreadsheet` 取得版本和数据范围，再提交对象；不要在脚本里 `wb.save()`。

## 1. 原生 Excel 图表

原生图表优先使用一个 ChangeSet，`data_range` 应包含表头行，类别范围与数据行对齐：

```python
from em import observe_spreadsheet, apply_spreadsheet_changes

seen = observe_spreadsheet(
    file_path="outputs/summary.xlsx", sheet="汇总", mode="range",
    range="A1:B12", facets=["data", "objects", "geometry"],
)
changed = apply_spreadsheet_changes(
    file_path="outputs/summary.xlsx", expected_version=seen["content_version"],
    operations=[{
        "kind": "chart", "sheet": "汇总", "chart_type": "bar",
        "data_range": "A1:B12", "categories_range": "A2:A12",
        "target_cell": "E2", "title": "按区域汇总", "y_title": "金额",
    }],
)
print(changed["receipt"])
```

当前图表种类和更新/删除边界以 `apply_spreadsheet_changes.operations.chart` 的实时 schema 为准。提交后再用 `observe_spreadsheet(facets=["objects","geometry"])`，需要视觉结论时调用 `preview_spreadsheet`。

## 2. 生成独立 PNG

需要 matplotlib 图像而不是原生图表时，在 `run_code` 中先用 `analyze_spreadsheet` 取有限结果，再绘图；不要让 pandas 或 openpyxl 直接重新读取工作簿。示意：

```python
from em import analyze_spreadsheet
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

data = analyze_spreadsheet(
    file_path="outputs/summary.xlsx", sheet="汇总", mode="aggregate",
    group_by=["类别"], aggregations={"数值": "sum"}, max_rows=50,
)
rows = data.get("data") or data.get("records") or []
labels = [str(row.get("类别", "")) for row in rows]
values = [row.get("数值_sum", 0) for row in rows]
fig, ax = plt.subplots(figsize=(10, 6))
ax.bar(labels, values); ax.set_title("类别汇总")
fig.tight_layout(); fig.savefig("outputs/category-summary.png", dpi=150, bbox_inches="tight")
plt.close(fig)
```

PNG 是派生产物，不会替代工作簿的对象和布局核验；仍需检查分析结果的 `status`、`content_version` 和 `coverage`。

## 3. 原生 Table 对象

原生 Table 使用 `kind="table"`，对象创建、更新、样式和 `ref` 字段按当前操作 schema 查询。写入后用 `observe_spreadsheet(mode="objects")` 确认对象数量、范围和名称；不要把静态汇总矩阵称为 Table 或 PivotTable。
