---
name: run_code_patterns
version: "1.0.0"
priority: 35
layer: core
max_tokens: 600
---
## run_code 常用代码模板

`run_code` 是主力写入工具。以下模板覆盖最常见操作，直接复制修改参数即可。

### 写入数据
```python
import pandas as pd
from openpyxl import load_workbook

# 读取 → 修改 → 写回（保留其他 sheet）
df = pd.read_excel("file.xlsx", sheet_name="Sheet1")
df["新列"] = df["金额"] * 0.3  # 计算列
# 写入时保留原文件其他 sheet
with pd.ExcelWriter("file.xlsx", engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
    df.to_excel(w, sheet_name="Sheet1", index=False)
```

### 单元格级写入
```python
from openpyxl import load_workbook
wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]
ws["A1"] = "新值"
ws["B2"] = 100
wb.save("file.xlsx")
```

### 插入行
```python
from openpyxl import load_workbook
wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]
ws.insert_rows(5, amount=3)  # 在第5行前插入3行
wb.save("file.xlsx")
```

### 格式化样式
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

### 批量格式化（区域）
```python
from openpyxl.utils import get_column_letter
for row in ws.iter_rows(min_row=1, max_row=1, min_col=1, max_col=10):
    for cell in row:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="4472C4")
```

### 合并单元格
```python
ws.merge_cells("A1:D1")
ws.unmerge_cells("A1:D1")
```

### 条件格式
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

### 图表
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

### Sheet 管理
```python
wb.create_sheet("新表")              # 新建
wb.copy_worksheet(wb["Sheet1"])      # 复制
wb["Sheet1"].title = "新名称"         # 重命名
del wb["要删除的表"]                   # 删除
```

### 描述性统计分析
```python
import pandas as pd
df = pd.read_excel("file.xlsx", sheet_name="Sheet1")
print(df.describe(include="all").to_string())
print(f"缺失值:\n{df.isnull().sum().to_string()}")
print(f"总行数: {len(df)}, 总列数: {len(df.columns)}")
```

### 分组聚合
```python
import pandas as pd
df = pd.read_excel("file.xlsx", sheet_name="Sheet1")
result = df.groupby("部门").agg({"金额": ["sum", "mean", "count"]})
result.columns = ["总金额", "平均金额", "订单数"]
print(result.sort_values("总金额", ascending=False).to_string())
```

### 跨表键匹配分析
```python
import pandas as pd
left = pd.read_excel("file.xlsx", sheet_name="源表")
right = pd.read_excel("file.xlsx", sheet_name="目标表")
matched = left["键列"].isin(right["键列"])
print(f"匹配率: {matched.mean():.1%} ({matched.sum()}/{len(left)})")
print(f"未匹配样本: {left[~matched]['键列'].head(10).tolist()}")
```

### 打印设置
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

### 条件删除行
```python
import pandas as pd
df = pd.read_excel("file.xlsx", sheet_name="Sheet1")
df = df[df["状态"] != "已取消"]  # 删除满足条件的行
with pd.ExcelWriter("file.xlsx", engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
    df.to_excel(w, sheet_name="Sheet1", index=False)
```

### 数据验证（下拉列表）
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

### 读取文本文件
```python
from pathlib import Path
content = Path("data.csv").read_text(encoding="utf-8")
print(content[:2000])
```

### 跨表匹配写回（VLOOKUP 等价）
```python
import pandas as pd
src = pd.read_excel("file.xlsx", sheet_name="源表")
tgt = pd.read_excel("file.xlsx", sheet_name="目标表")
merged = tgt.merge(src[["键列","值列"]], on="键列", how="left")
with pd.ExcelWriter("file.xlsx", engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
    merged.to_excel(w, sheet_name="目标表", index=False)
```

### VBA 等价操作（用户要求 VBA/宏时的 Python 替代）
当用户要求 VBA 宏或提到 VBA 时，用以下 openpyxl/pandas 模式实现同等效果。

#### 遍历单元格并条件写入（替代 VBA 的 For Each / Range.Cells 循环）
```python
from openpyxl import load_workbook
wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]
for row in range(2, ws.max_row + 1):
    val = ws.cell(row, 3).value  # C 列
    if val and str(val).strip().upper() == "MATCH":
        ws.cell(row, 4).value = "Found"  # 写入 D 列
wb.save("file.xlsx")
```

#### 跨 Sheet 查找填充（替代 VBA 的 Worksheets().Range 引用）
```python
from openpyxl import load_workbook
wb = load_workbook("file.xlsx")
# 从源 sheet 构建查找字典
src = wb["源表"]
lookup = {}
for r in range(2, src.max_row + 1):
    key = src.cell(r, 1).value
    lookup[key] = src.cell(r, 2).value
# 填充目标 sheet
tgt = wb["目标表"]
for r in range(2, tgt.max_row + 1):
    k = tgt.cell(r, 1).value
    if k in lookup:
        tgt.cell(r, 3).value = lookup[k]
wb.save("file.xlsx")
```

#### 按区块重复填充值（替代 VBA 的动态范围 + INVOICE 块模式）
```python
from openpyxl import load_workbook
wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]
# 扫描 INVOICE NO 块：header 行 → 值行 → 明细行直到 TOTAL
blocks = []  # [(invoice_value, detail_count), ...]
r = 1
while r <= ws.max_row:
    if str(ws.cell(r, 4).value or "").strip().upper() == "INVOICE NO":
        inv = ws.cell(r + 1, 4).value
        # 找 TOTAL 行确定明细行数
        rr = r + 3
        while rr <= ws.max_row and str(ws.cell(rr, 7).value or "").strip().upper() != "TOTAL":
            rr += 1
        blocks.append((inv, max(1, rr - r - 2)))
        r = rr + 1
    else:
        r += 1
# blocks 现在包含 [(invoice, count), ...] 可用于填充其他 sheet
```

#### openpyxl 不支持的操作
以下操作无法通过 openpyxl 实现，需告知用户限制：
- **创建数据透视表**（Pivot Table）— openpyxl 不支持，建议用 pandas pivot_table() 计算后写入新 sheet
- **ActiveX 控件 / UserForm** — 无 Python 等价方案
- **事件驱动宏**（Workbook_Open 等）— 无法模拟 Excel 事件模型

### 文件损坏恢复
当 openpyxl 打开文件失败（如 `KeyError: '[Content_Types].xml'`、`BadZipFile` 等），说明文件损坏。**不要**花多轮迭代诊断损坏原因，按以下策略快速恢复：
1. 如果同目录有参考文件（如 golden/模板），用 `copy_file` 复制为工作副本到 `outputs/`
2. 在副本上用 `run_code` 清除答案列/目标区域，恢复到初始状态
3. 然后在副本上执行实际写入逻辑
注意：`run_code` 中的 `shutil.copy` 会被沙盒拦截，必须用 `copy_file` 工具复制文件。

## 图片表格复刻工作流

当用户提供图片并要求复刻表格时，遵循以下流水线：

1. **读取图片**：若图片未在聊天中附带，先用 `read_image` 查看
2. **结构化提取**：调用 `extract_table_from_image` 生成 ReplicaSpec
3. **检查不确定项**：如 spec 中 uncertainties 数量 > 0，先汇报关键不确定项给用户
4. **生成草稿**：调用 `rebuild_excel_from_spec` 编译为 Excel
5. **验证**：调用 `verify_excel_replica` 生成差异报告
6. **交付**：将 diff_report 中的问题项汇总到 finish_task.report 中

交付物固定为两份：
- `draft.xlsx`（可直接打开的 Excel 文件）
- `replica_diff_report.md`（告诉用户哪些地方不确定/还原不完整）
