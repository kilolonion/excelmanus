# 高级模板：VBA 替代与恢复

工作区 xlsx 的改写必须走 SDK。下面循环只演示怎么算；写回不要 `wb.save`。

## 读取文本文件

```python
from pathlib import Path
content = Path("data.csv").read_text(encoding="utf-8")
print(content[:2000])
```

## VBA 等价操作（用户要求 VBA/宏时的 Python 替代）

### 遍历单元格并条件写入（替代 VBA 的 For Each / Range.Cells 循环）

```python
from openpyxl import load_workbook
wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]
for row in range(2, ws.max_row + 1):
    val = ws.cell(row, 3).value  # C 列
    if val and str(val).strip().upper() == "MATCH":
        ws.cell(row, 4).value = "Found"  # 写入 D 列
# 收集变更后走 edit_spreadsheet，不要 wb.save
```

### 跨 Sheet 查找填充（替代 VBA 的 Worksheets().Range 引用）

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
# 收集变更后走 edit_spreadsheet，不要 wb.save
```

### 按区块重复填充值（替代 VBA 的动态范围 + INVOICE 块模式）

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

### openpyxl 不支持的操作

以下操作无法通过 openpyxl 实现，需告知用户限制：
- **创建数据透视表**（Pivot Table）— openpyxl 不支持，建议用 pandas pivot_table() 计算后写入新 sheet
- **ActiveX 控件 / UserForm** — 无 Python 等价方案
- **事件驱动宏**（Workbook_Open 等）— 无法模拟 Excel 事件模型

## 文件损坏

openpyxl 打开失败（如 `KeyError: '[Content_Types].xml'`、`BadZipFile`）表示文件损坏。`run_code` 里的 `shutil.copy` 会被沙盒拦截；复制工作区文件用 `copy_file`。

规格只用于创建新簿。工作区 xlsx 的改写走 SDK，不要 `wb.save`。
