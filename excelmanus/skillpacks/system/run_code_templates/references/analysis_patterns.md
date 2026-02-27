# 智能读取与分析统计模板

## 智能读取（处理合并标题行）

当工作表可能有合并单元格标题行时，裸 `pd.read_excel()` 会产生 `Unnamed:*` 列名。使用内置工具自动检测真实表头：

```python
from excelmanus.utils import smart_read_excel
df, header_row = smart_read_excel("file.xlsx", sheet="Sheet1")
print(f"检测到表头行: {header_row}")
print(f"数据行数: {len(df)}（不含表头）")
print(df.head().to_string())
```

## 隐私脱敏输出

输出含个人信息的数据样本时，使用内置脱敏工具：

```python
from excelmanus.utils import mask_pii
for _, row in df.head(5).iterrows():
    print(mask_pii(str(row.to_dict())))
```

## 描述性统计分析

```python
import pandas as pd
df = pd.read_excel("file.xlsx", sheet_name="Sheet1")
print(df.describe(include="all").to_string())
print(f"缺失值:\n{df.isnull().sum().to_string()}")
print(f"数据行数: {len(df)}（不含表头），列数: {len(df.columns)}")
```

## 分组聚合

```python
import pandas as pd
df = pd.read_excel("file.xlsx", sheet_name="Sheet1")
result = df.groupby("部门").agg({"金额": ["sum", "mean", "count"]})
result.columns = ["总金额", "平均金额", "订单数"]
print(result.sort_values("总金额", ascending=False).to_string())
```

## 跨表键匹配分析

```python
import pandas as pd
left = pd.read_excel("file.xlsx", sheet_name="源表")
right = pd.read_excel("file.xlsx", sheet_name="目标表")
matched = left["键列"].isin(right["键列"])
print(f"匹配率: {matched.mean():.1%} ({matched.sum()}/{len(left)})")
print(f"未匹配样本: {left[~matched]['键列'].head(10).tolist()}")
```
