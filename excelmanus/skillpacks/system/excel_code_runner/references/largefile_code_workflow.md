# 大文件 Excel 代码执行参考

## 快速模板

```python
from pathlib import Path
import pandas as pd

input_file = Path("input.xlsx")
output_file = Path("outputs/result.csv")
output_file.parent.mkdir(parents=True, exist_ok=True)

df = pd.read_excel(
    input_file,
    sheet_name=0,
    usecols=None,
    nrows=200000,
)

result = (
    df.groupby("月份", dropna=False)["销售额"]
    .sum()
    .reset_index()
    .sort_values("销售额", ascending=False)
)
result.to_csv(output_file, index=False)
print(f"rows={len(df)} output={output_file}")
```

## 建议参数
- `usecols`：限制列范围，优先读取业务相关列。
- `nrows`：先读小样本验证逻辑，再逐步放大。
- `sheet_name`：显式指定目标 sheet，避免读错表。

## 执行建议
- 首次执行先用 1k~10k 行验证。
- 稳定后再执行全量或分批。
- 输出尽量写文件，返回摘要而非全量明细。
