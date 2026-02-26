# 图表与表格 run_code 模板

## 1. PNG 图表导出（matplotlib）

### 柱状图

```python
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use("Agg")

df = pd.read_excel("file.xlsx", sheet_name="Sheet1")
fig, ax = plt.subplots(figsize=(10, 6))
ax.bar(df["类别"].astype(str), df["数值"])
ax.set_xlabel("类别")
ax.set_ylabel("数值")
ax.set_title("柱状图标题")
plt.xticks(rotation=45, ha="right")
fig.tight_layout()
fig.savefig("outputs/chart.png", dpi=150, bbox_inches="tight")
plt.close(fig)
print("已保存: outputs/chart.png")
```

### 折线图

```python
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use("Agg")

df = pd.read_excel("file.xlsx", sheet_name="Sheet1")
fig, ax = plt.subplots(figsize=(10, 6))
ax.plot(df["月份"], df["销售额"], marker="o", label="销售额")
# 多系列：ax.plot(df["月份"], df["成本"], marker="s", label="成本")
ax.set_xlabel("月份")
ax.set_ylabel("金额")
ax.set_title("趋势图")
ax.legend()
fig.tight_layout()
fig.savefig("outputs/trend.png", dpi=150, bbox_inches="tight")
plt.close(fig)
print("已保存: outputs/trend.png")
```

### 饼图

```python
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use("Agg")

df = pd.read_excel("file.xlsx", sheet_name="Sheet1")
fig, ax = plt.subplots(figsize=(8, 8))
ax.pie(df["数值"], labels=df["类别"].astype(str), autopct="%1.1f%%")
ax.set_title("占比分布")
ax.set_aspect("equal")
fig.tight_layout()
fig.savefig("outputs/pie.png", dpi=150, bbox_inches="tight")
plt.close(fig)
print("已保存: outputs/pie.png")
```

### 中文字体处理

```python
from matplotlib import font_manager
import matplotlib.pyplot as plt

# 自动选择可用中文字体
cjk_candidates = ["PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", "SimHei", "STHeiti"]
available = {f.name for f in font_manager.fontManager.ttflist}
cjk_font = next((f for f in cjk_candidates if f in available), None)
if cjk_font:
    plt.rcParams["font.sans-serif"] = [cjk_font, "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
```

## 2. Excel 表格对象（Table）

### 基本表格创建

```python
from openpyxl import load_workbook
from openpyxl.worksheet.table import Table, TableStyleInfo

wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]

# 确定数据范围（含表头）
max_row = ws.max_row
max_col = ws.max_column
from openpyxl.utils import get_column_letter
end_col = get_column_letter(max_col)
ref = f"A1:{end_col}{max_row}"

# 创建表格（名称必须在整个工作簿内唯一）
table = Table(displayName="SalesTable", ref=ref)
style = TableStyleInfo(
    name="TableStyleMedium9",  # 预定义样式
    showFirstColumn=False,
    showLastColumn=False,
    showRowStripes=True,       # 交替行色
    showColumnStripes=False,
)
table.tableStyleInfo = style
ws.add_table(table)
wb.save("file.xlsx")
print(f"已创建表格 'SalesTable'，范围: {ref}")
```

### 带筛选的表格

```python
from openpyxl import load_workbook
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter

wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]

ref = f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"
table = Table(displayName="FilterTable", ref=ref)
table.tableStyleInfo = TableStyleInfo(
    name="TableStyleLight1",
    showRowStripes=True,
)
# autoFilter 默认启用（Table 自带）
ws.add_table(table)
wb.save("file.xlsx")
print(f"已创建筛选表格，范围: {ref}")
```

### 指定范围创建表格

```python
from openpyxl import load_workbook
from openpyxl.worksheet.table import Table, TableStyleInfo

wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]

# 只对 A1:D20 范围创建表格
table = Table(displayName="PartialTable", ref="A1:D20")
table.tableStyleInfo = TableStyleInfo(
    name="TableStyleMedium2",
    showRowStripes=True,
    showFirstColumn=True,  # 首列加粗
)
ws.add_table(table)
wb.save("file.xlsx")
print("已创建表格 'PartialTable'，范围: A1:D20")
```

### 常用表格样式参考

| 样式名 | 风格 |
|--------|------|
| `TableStyleLight1` ~ `Light21` | 浅色系，适合简洁报表 |
| `TableStyleMedium1` ~ `Medium28` | 中等色深，**推荐默认用 Medium9**（蓝色专业风） |
| `TableStyleDark1` ~ `Dark11` | 深色系，适合仪表盘风格 |

### 注意事项

- **表名唯一性**：同一工作簿中不能有重名 Table，否则文件损坏。创建前可检查 `ws.tables` 确认无冲突。
- **范围必须含表头**：`ref` 的第一行将被当作列标题。
- **避免与合并单元格重叠**：Table 范围内不能有合并单元格。
- **已有表格检查**：`ws.tables` 返回当前 sheet 的所有表格对象。
