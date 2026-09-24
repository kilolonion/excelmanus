# V2 读取、计算与写回

大批次可用 Code Mode。SDK 与直接工具使用同一合同、选择坐标和版本。

```python
from em import analyze_spreadsheet, apply_spreadsheet_changes
selected = analyze_spreadsheet(file_path="outputs/book.xlsx", sheet="Sheet1", mode="filter",
                               column="状态", operator="eq", value="待处理", columns=["状态"])
selection = selected["selection"]
values = [["已处理"] for _ in selection["rows"]]
changed = apply_spreadsheet_changes(file_path="outputs/book.xlsx", expected_version=selected["content_version"],
    operations=[{"kind": "write", "sheet": "Sheet1", "selection": selection, "values": values}])
print(changed["receipt"])
```

区域观察返回 `regions`；每项 `cells` 的键为原始 `row,column`，`selection` 带源坐标。不要把投影后的第一列当成 A 列。
小改动直接 `kind=cells.patch`；表格操作包括 write、copy、insert、delete_rows、delete_columns、sheet、pivot、transform。
覆盖写不等于删除：需要清空内容时使用显式 `clear` ChangeSet（值使用 `null`），并先确认目标范围。
复杂字段通过 `introspect_capability(query_type="tool_detail", query="apply_spreadsheet_changes.operations.<kind>")` 查合同。
已有文件必须提供 expected_version。工作区写入使用 em.*，不直接保存 openpyxl/pandas 工作簿。
