# ExcelManus 表格实用配方

## 1. 新建工作簿

新文件使用 `apply_spreadsheet_changes(file_path="outputs/report.xlsx", create=True, workbook_spec=...)`。`workbook_spec` 的字段必须从`introspect_capability(query_type="tool_detail", query="apply_spreadsheet_changes.workbook_spec")` 或 `introspect_capability(query_type="knowledge_spec", query="apply_spreadsheet_changes")` 取得。至少明确：工作表名称、表头、数据类型、公式区域、文档目的和任何不确定项。创建后观察 overview、回读关键范围；有公式再调用 `calculate_spreadsheet`。

工作区只有 CSV、尚无 xlsx 时该工具仍可用（新建不依赖已有 xlsx）：直接 `apply_spreadsheet_changes(file_path="outputs/<名>.xlsx", workbook_spec=...)` 产出交付物，不要用 openpyxl/pandas 直存。`convert_spreadsheet` 只用于真正的格式转换（xls/xlsb 等转 xlsx，或 `mode="data_only"` 仅迁移值），不是新建工作簿的前置步骤；它的产物工作表名是 `input`（不是 CSV 文件名，引用前先 `observe_spreadsheet(overview)` 确认）。依赖已有工作簿的 `trace_spreadsheet_formulas` 在 CSV-only 下仍被目录门控，等 `outputs/`（或工作区顶层）出现 xlsx 后下一轮目录自动解锁。

## 2. 先分析再写回

```python
from em import analyze_spreadsheet, apply_spreadsheet_changes

result = analyze_spreadsheet(
    file_path="outputs/orders.xlsx",
    sheet="订单",
    mode="filter",
    conditions=[{"column": "状态", "operator": "eq", "value": "待处理"}],
    columns=["订单号", "状态"],
    max_rows=500,
)
selection = result["selection"]
values = [["已处理"] for _ in selection["rows"]]
changed = apply_spreadsheet_changes(
    file_path="outputs/orders.xlsx",
    expected_version=result["content_version"],
    operations=[{
        "kind": "write",
        "sheet": "订单",
        "selection": selection,
        "values": values,
    }],
)
print(changed["receipt"])
```

只有筛选结果实际包含的行才是证据；结果被截断时先分页或缩小任务，不按 `values` 的第几行猜 Excel 地址。

## 3. 质量检查和汇总

- 先 `analyze_spreadsheet(mode="profile")` 了解列类型、行数和缺失；再用 `mode="quality"` 检查异常。
- 求和、计数、均值、TopN 用 `mode="aggregate"`，传 `group_by`、`aggregations`、`sort_by` 和 `max_rows`。
- 重复键先 `mode="distinct", dup_only=true`；确认后才用 `apply_spreadsheet_changes` 的 `transform`/`dedupe`。
- 透视分析用 `mode="pivot"`；需要落地汇总表时再用 `edit.kind="pivot"` 的当前合同，静态矩阵不要声称是原生 PivotTable。
- SQL 或多文件连接用 `query_spreadsheet`；只允许 SELECT/WITH，输出上限和源版本依赖要写进交付说明。

## 4. 公式和重算

1. 观察目标公式区域，必要时 `trace_spreadsheet_formulas` 看先例和影响。
2. 用 `cells.patch` 或 `write` 写公式，保留 `expected_version`。
3. 调用 `calculate_spreadsheet(file_path=..., expected_version=...)`。默认遇到公式错误不发布。
4. 用 `validate_spreadsheet` 的 `formula_errors` 和业务规则复核；对缺缓存返回 `partial`，不能把缺失值当 0。
5. 回读代表性输入、公式和输出单元格，必要时 `preview_spreadsheet`。

把税率、汇率或阈值等业务假设放在明确的输入单元格或参数区域，公式引用这些单元格；不要把关键假设散落在公式常量中。

## 5. 样式、布局和图表

先观察 `presentation` 和 `geometry`，在同一 ChangeSet 中提交格式、尺寸、冻结、合并、条件格式和图表。固定尺寸用 `size.column_widths`/`row_heights`；按内容适配显式传 `auto_fit=true` 与 `axis`；比例调整用 `geometry.scale`，不要覆盖用户已指定的尺寸。

原生图表示例：

```python
apply_spreadsheet_changes(
    file_path="outputs/summary.xlsx",
    expected_version=seen["content_version"],
    operations=[{
        "kind": "chart",
        "sheet": "汇总",
        "chart_type": "bar",
        "data_range": "B1:B12",
        "categories_range": "A2:A12",
        "target_cell": "E2",
        "title": "按区域汇总",
    }],
)
```

`sheet` 是数据源表，`target_sheet` 是图表所在表，默认与数据源相同。创建时把图表、尺寸和覆盖图表的打印区域一起规划；小修使用 operations，不删除重建整份已提交工作簿。

写入后先观察对象，再用 `preview_spreadsheet(surface="print")` 预览；“有 chart 对象”不等于标签、尺寸和打印布局可读。

## 6. 大表和分批

能用 native `analyze_spreadsheet` 表达的筛选、聚合、去重不要改写成 Python 全表读取。必须逐行自定义计算时，在 `run_code` 中按窗口读取，每批只保留必要列，下一批使用上一批返回的版本。批量写入要有稳定业务键或明确的目标 selection，并使用唯一的任务内操作顺序；网络超时后先检查版本和已提交范围。

## 7. 预览与交付

- 工作台视觉检查：`preview_spreadsheet(surface="workbench")`。
- 打印/PDF 视觉检查：`preview_spreadsheet(surface="print")` 或 `render_spreadsheet(format="pdf")`。
- PNG 产物：`render_spreadsheet(format="png", dpi=...)`，核对分页文件列表；`max_pages` 是总输出页数上限，不是选取前 N 页。
- 交付只列实际文件、版本、校验/预览范围和限制；不要把临时脚本、spill 句柄或探测工作簿当作产物。
