---
name: merged_cell_handling
version: "1.0.0"
priority: 51
layer: strategy
max_tokens: 450
conditions: {}
---
## 合并单元格处理策略

当 `scan_excel_snapshot` 或 `read_excel` 返回 `has_merged_cells: true` 或 `merged_cell_summary` 时，按以下流程处理：

### 1. 识别合并布局

先用 `run_code` 读取合并区域全貌：

```python
from openpyxl import load_workbook
wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]
for mr in ws.merged_cells.ranges:
    top_left = ws.cell(mr.min_row, mr.min_col).value
    print(f"{mr} → {top_left!r}  (rows {mr.min_row}-{mr.max_row}, cols {mr.min_col}-{mr.max_col})")
wb.close()
```

重点关注：
- **列组标头**（如"星期一"跨 D:E 列）→ 确定二维布局的列分组含义
- **数据区跨行合并**（如"1-2 节"跨 2 行）→ 这些区域在 pandas 中只有首格有值

### 2. 合并值传播（Forward-Fill）

对含跨行/跨列合并的数据区，用 openpyxl 做值传播后再处理：

```python
from openpyxl import load_workbook
from copy import copy
wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]
for mr in ws.merged_cells.ranges:
    top_left_value = ws.cell(mr.min_row, mr.min_col).value
    # 先取消合并，再填充所有单元格
    ws.unmerge_cells(str(mr))
    for row in range(mr.min_row, mr.max_row + 1):
        for col in range(mr.min_col, mr.max_col + 1):
            ws.cell(row=row, column=col, value=top_left_value)
# 现在可以安全用 pandas 读取，不会产生 NaN
```

### 3. 关键原则

- **不要直接用 pandas 读取高合并率表单**：合并区域中只有左上角有值，其余全是 NaN，会导致数据丢失。
- **表单类文档**（课表、收据、模板）：优先用 openpyxl 逐单元格读取，不依赖 pandas 的行列结构。
- **数据完整性校验**：处理后检查非空单元格数量是否符合预期（如 5 天 × 11 节次 = 55 格应有数据）。
