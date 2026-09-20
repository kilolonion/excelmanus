# 图表与表格 run_code 模板

工作区 xlsx 的改写必须走 SDK。原生图优先 `manage_spreadsheet_objects`。下面片段不要 `wb.save`。

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

## Excel Table 对象

当前 native 工具没有创建、更新或删除 Excel Table 对象的保存通路。不要在 run_code 中用 openpyxl `ws.add_table()` 后声称已经写回；这类内存修改不会自动提交。需要表格样式时使用 `format_spreadsheet` 的直接样式/条件格式；需要原生 Table 时明确告知暂不支持。
