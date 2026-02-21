---
name: formula_vs_value
version: "1.0.0"
priority: 45
layer: strategy
max_tokens: 150
conditions:
  write_hint: "may_write"
---
## 公式与值的选择

### 公式模式分析（写公式前必做）
当任务要求写入公式时，先用 `run_code` 检查目标区域是否已有公式样本：
```python
from openpyxl import load_workbook
wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]
# 读取目标列已有公式（取前几个非空单元格）
for row in range(2, min(20, ws.max_row + 1)):
    val = ws.cell(row, col).value
    if val and str(val).startswith("="):
        print(f"Row {row}: {val}")
        break
```
分析已有公式的**引用列、计算逻辑、绝对/相对引用模式**，以此为蓝本构建新公式。已有公式是理解正确列映射的关键线索——不要凭猜测编写公式。

### 适配实际数据格式（禁止过度防御）
生成公式或代码前，**必须引用探查阶段获得的实际数据格式**来决定处理逻辑的复杂度：
- 若探查发现参与率列公式为 `=E2/B2`（返回 0~1 小数），直接用 `>=0.8` 比较，**禁止**额外兼容整数百分比和字符串百分比格式。
- 若探查发现日期列已是 Excel 日期序列号，直接用日期函数，不要再做字符串解析。
- **原则**：只为探查中实际观察到的数据格式编写处理逻辑。未观察到的格式不做防御——过度嵌套的公式会降低可读性和可维护性。

### 写公式的场景
- 用户**明确要求公式**（如"写一个 SUM 公式"）。
- 简单单 sheet 内公式（SUM、AVERAGE、COUNT、IF 等），且引用范围在同一 sheet 内。

### 写计算值的场景（优先）
- **跨 sheet 查找**（INDEX/MATCH/VLOOKUP 引用其他 sheet）→ 必须先 read_excel 读取源数据，计算匹配结果后用 `run_code` + openpyxl 写入具体值。
- **数组公式**（CSE 公式）→ openpyxl 无法缓存计算值，写入后外部读取为空。
- **批量填充计算列**（如"利润 = 收入 × 30%"覆盖数百行）→ 读取源列数据，Python 层面计算后批量写入值，比逐行写公式更高效且可靠。
- 用户未明确要求公式，只要求"结果"或"数据"时。

### 警告处理
- openpyxl 写入的公式无缓存计算值，外部读取时显示为空。应优先在 Python 层面计算后写入具体值。
