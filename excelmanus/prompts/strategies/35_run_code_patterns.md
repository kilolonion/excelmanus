---
name: run_code_patterns
version: "3.1.0"
priority: 35
layer: strategy
max_tokens: 500
conditions:
  chat_mode: "write"
---
## run_code 使用原则

`run_code` 是主力写入工具。遵循以下原则：

1. **所有写入操作通过 run_code 完成**（pandas/openpyxl）
2. **包含顶层 try/except 异常处理**，print 到 stderr
3. 仅使用数据处理代码（pandas/openpyxl/numpy）
4. **写入后在 stdout 打印关键验证数据**（行数、列名、抽样值），作为核心法则 2（验证闭环）的验证依据

### 核心代码模板

**读→改→写回（保留其他 sheet）**：
```python
import sys
try:
    import pandas as pd
    df = pd.read_excel("file.xlsx", sheet_name="Sheet1")
    df["新列"] = df["金额"] * 0.3
    with pd.ExcelWriter("file.xlsx", engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
        df.to_excel(w, sheet_name="Sheet1", index=False)
    print(f"写入完成: {len(df)} 行, 列: {list(df.columns)}")
except Exception as e:
    print(f"错误：{type(e).__name__}: {e}", file=sys.stderr)
```

**跨表匹配写回（VLOOKUP 等价）**：
```python
src = pd.read_excel("file.xlsx", sheet_name="源表")
tgt = pd.read_excel("file.xlsx", sheet_name="目标表")
merged = tgt.merge(src[["键列","值列"]], on="键列", how="left")
with pd.ExcelWriter("file.xlsx", engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
    merged.to_excel(w, sheet_name="目标表", index=False)
```

### 更多模板

格式化、图表、条件格式、VBA 替代、文件恢复等高级场景，激活 `run_code_templates` 技能获取完整代码：

| 类别 | 覆盖模板 |
|------|---------|
| 格式样式 | 字体/填充/边框/对齐、批量格式化、合并单元格、条件格式 |
| 图表与布局 | 图表创建、打印设置、数据验证（下拉列表） |
| 分析统计 | 描述性统计、分组聚合、跨表键匹配 |
| VBA 替代 | 遍历条件写入、按区块填充、openpyxl 不支持的操作说明 |
| 恢复与复刻 | 文件损坏恢复流程、图片表格复刻工作流 |

### 文件损坏快速恢复

当 openpyxl 打开失败（`KeyError`/`BadZipFile` 等）：
1. 用 `copy_file` 从参考文件复制工作副本到 `outputs/`
2. 在副本上清除目标区域，恢复初始状态
3. 在副本上执行写入逻辑
