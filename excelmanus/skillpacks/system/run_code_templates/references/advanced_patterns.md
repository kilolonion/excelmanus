# 高级模板：VBA 替代、恢复与复刻

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
wb.save("file.xlsx")
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
wb.save("file.xlsx")
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

## 写入后独立验证

`run_code` 写入完成后，**必须用 `inspect_spreadsheet(mode="range")` 对目标 sheet 做一次独立回读验证**，而非仅依赖代码自身的 `print` 输出。
验证要点：
- 新增列是否出现在预期位置（表头名称正确）
- 抽样前 3~5 行数据与预期一致
- 数据行数未意外增减

`print` 输出和 `inspect_spreadsheet` 回读不可互相替代：前者验证代码逻辑，后者验证文件实际写入结果。两者都通过才算写入成功。

## 文件损坏恢复

当 openpyxl 打开文件失败（如 `KeyError: '[Content_Types].xml'`、`BadZipFile` 等），说明文件损坏。**不要**花多轮迭代诊断损坏原因，按以下策略快速恢复：
1. 如果同目录有参考文件（如 golden/模板），用 `copy_file` 复制为工作副本到 `outputs/`
2. 在副本上用 `run_code` 清除答案列/目标区域，恢复到初始状态
3. 然后在副本上执行实际写入逻辑
注意：`run_code` 中的 `shutil.copy` 会被沙盒拦截，必须用 `copy_file` 工具复制文件。

## 图片表格复刻工作流

当用户提供图片并要求复刻表格时：

1. **读图**：工作台或渠道附件会进入当前 user 消息。工作区已有图片文件时，用 `read_image` 注入视觉上下文。主模型无视觉则无法处理图片。
2. **产出规格**：阅读后写出一份 `WorkbookSpec`（含 `uncertainties`，可空列表）。
3. **创建工作簿**：调用 `edit_spreadsheet(file_path=..., workbook_spec=..., create_workbook=True)`。规格只用于创建，不用于给已有文件打补丁。
4. **核对**：用 `inspect_spreadsheet` 回读，不确定处留在 `uncertainties`，不编造。
5. **局部修正**：差异用 `edit_spreadsheet(operations=...)` 或 `run_code` 修，每次提交带最新 `expected_version`。
