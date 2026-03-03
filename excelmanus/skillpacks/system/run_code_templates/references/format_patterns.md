# 格式化、图表与打印模板

## 格式化样式

```python
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment, numbers

wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]

# 字体
ws["A1"].font = Font(name="微软雅黑", bold=True, color="FF0000", size=12)
# 填充
ws["A1"].fill = PatternFill("solid", fgColor="FFFF00")
# 边框
thin = Side(style="thin", color="000000")
ws["A1"].border = Border(left=thin, right=thin, top=thin, bottom=thin)
# 对齐
ws["A1"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
# 数字格式
ws["B1"].number_format = '#,##0.00'
# 列宽
ws.column_dimensions["A"].width = 20
# 行高
ws.row_dimensions[1].height = 30

wb.save("file.xlsx")
```

## 批量格式化（区域）

```python
from openpyxl.utils import get_column_letter
for row in ws.iter_rows(min_row=1, max_row=1, min_col=1, max_col=10):
    for cell in row:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="4472C4")
```

## 合并单元格

```python
ws.merge_cells("A1:D1")
ws.unmerge_cells("A1:D1")
```

## 条件格式

```python
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, DataBarRule
# 值高亮
ws.conditional_formatting.add("B2:B100",
    CellIsRule(operator="greaterThan", formula=["1000"],
              fill=PatternFill("solid", fgColor="C6EFCE")))
# 色阶
ws.conditional_formatting.add("C2:C100",
    ColorScaleRule(start_type="min", start_color="F8696B",
                   end_type="max", end_color="63BE7B"))
# 数据条
ws.conditional_formatting.add("D2:D100",
    DataBarRule(start_type="min", end_type="max", color="638EC6"))
```

## 图表

```python
from openpyxl.chart import BarChart, Reference
chart = BarChart()
chart.title = "销售额"
data = Reference(ws, min_col=2, min_row=1, max_row=10)
cats = Reference(ws, min_col=1, min_row=2, max_row=10)
chart.add_data(data, titles_from_data=True)
chart.set_categories(cats)
ws.add_chart(chart, "E2")
wb.save("file.xlsx")
```

## 打印设置

```python
from openpyxl import load_workbook
wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]
ws.print_area = "A1:H50"
ws.sheet_properties.pageSetUpPr.fitToPage = True
ws.page_setup.fitToWidth = 1
ws.page_setup.fitToHeight = 0  # 0=不限高度
ws.page_setup.orientation = "landscape"
ws.print_title_rows = "1:1"  # 每页重复表头
wb.save("file.xlsx")
```

## 数据验证（下拉列表）

```python
from openpyxl import load_workbook
from openpyxl.worksheet.datavalidation import DataValidation
wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]
dv = DataValidation(type="list", formula1='"选项A,选项B,选项C"', allow_blank=True)
dv.add("B2:B100")
ws.add_data_validation(dv)
wb.save("file.xlsx")
```

## 专业对齐（按列数据类型）

```python
from openpyxl import load_workbook
from openpyxl.styles import Alignment
wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]
max_row = ws.max_row
# 表头居中
for cell in ws[1]:
    cell.alignment = Alignment(horizontal="center", vertical="center")
# 文本列左对齐（A、B列）
for row in ws.iter_rows(min_row=2, max_row=max_row, min_col=1, max_col=2):
    for cell in row:
        cell.alignment = Alignment(horizontal="left", vertical="center")
# 数字列右对齐（C、D列）
for row in ws.iter_rows(min_row=2, max_row=max_row, min_col=3, max_col=4):
    for cell in row:
        cell.alignment = Alignment(horizontal="right", vertical="center")
wb.save("file.xlsx")
```

## auto_fit 收尾（写入/格式化后必做）

```python
# 写入或格式化完成后，在 run_code 中用 openpyxl 自动适配列宽和行高
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]

# 自动适配列宽（遍历每列取最大内容宽度）
for col_idx in range(1, ws.max_column + 1):
    max_len = 0
    col_letter = get_column_letter(col_idx)
    for row_idx in range(1, ws.max_row + 1):
        cell = ws.cell(row=row_idx, column=col_idx)
        if cell.value is not None:
            # CJK 字符按 2 倍宽度估算
            text = str(cell.value)
            length = sum(2 if ord(c) > 0x7F else 1 for c in text)
            max_len = max(max_len, length)
    ws.column_dimensions[col_letter].width = min(max_len + 2, 60)

# 自动适配行高（默认行高 * 1.2）
for row_idx in range(1, ws.max_row + 1):
    ws.row_dimensions[row_idx].height = 15 * 1.2

wb.save("file.xlsx")
```
