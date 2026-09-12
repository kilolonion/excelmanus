# 数据写入与 Sheet 管理模板

工作区 xlsx 的改写必须走 SDK。下面片段只演示计算与提交形；不要 `wb.save` 或 `ExcelWriter` 覆盖工作区文件。

## 计算列后写回

```python
import pandas as pd
from em import edit_spreadsheet

df = pd.read_excel("file.xlsx", sheet_name="Sheet1")
df["新列"] = df["金额"] * 0.3
values = [list(df.columns)] + df.astype(object).where(pd.notnull(df), None).values.tolist()
edit_spreadsheet(
    file_path="file.xlsx",
    expected_version=version,
    operations=[{"kind": "write", "sheet": "Sheet1", "start_cell": "A1", "values": values}],
)
```

## 单元格级写入

```python
from em import edit_spreadsheet

edit_spreadsheet(
    file_path="file.xlsx",
    expected_version=version,
    operations=[{"kind": "write", "sheet": "Sheet1", "start_cell": "A1", "values": [["新值", 100]]}],
)
```

## 插入行

```python
from em import edit_spreadsheet

edit_spreadsheet(
    file_path="file.xlsx",
    expected_version=version,
    operations=[{"kind": "insert", "sheet": "Sheet1", "axis": "row", "at": 5, "count": 3}],
)
```

## 条件过滤后写回（覆盖写不等于删除）

`write` 只改传入矩形。更短的 `values` 不会清掉旧表尾部。没有删行操作。

先读出旧矩形尺寸，把需要丢掉的行写成 `null`，或写到新表。

```python
import pandas as pd
from em import edit_spreadsheet, inspect_spreadsheet

old = inspect_spreadsheet(mode="range", file_path="file.xlsx", sheet="Sheet1", range="A1:C20")
shape = old["shape"]  # rows / columns
df = pd.read_excel("file.xlsx", sheet_name="Sheet1")
kept = df[df["状态"] != "已取消"]
width = int(shape["columns"])
new_rows = [list(kept.columns)] + kept.astype(object).where(pd.notnull(kept), None).values.tolist()
# 把旧区里未被覆盖的行清空
while len(new_rows) < int(shape["rows"]):
    new_rows.append([None] * width)
edit_spreadsheet(
    file_path="file.xlsx",
    expected_version=version,
    operations=[{"kind": "write", "sheet": "Sheet1", "start_cell": "A1", "values": new_rows}],
)
```

## 跨表匹配写回

```python
import pandas as pd
from em import edit_spreadsheet

src = pd.read_excel("file.xlsx", sheet_name="源表")
tgt = pd.read_excel("file.xlsx", sheet_name="目标表")
merged = tgt.merge(src[["键列", "值列"]], on="键列", how="left")
values = [list(merged.columns)] + merged.astype(object).where(pd.notnull(merged), None).values.tolist()
edit_spreadsheet(
    file_path="file.xlsx",
    expected_version=version,
    operations=[{"kind": "write", "sheet": "目标表", "start_cell": "A1", "values": values}],
)
```

## Sheet 管理

```python
from em import edit_spreadsheet

edit_spreadsheet(
    file_path="file.xlsx",
    expected_version=version,
    operations=[
        {"kind": "sheet", "action": "create", "new_name": "新表"},
        {"kind": "sheet", "action": "rename", "sheet": "Sheet1", "new_name": "新名称"},
    ],
)
```

## 异常处理

`sys.exit()` / `exit()` / `os._exit()` 会触发安全拦截。错误 print 到 stderr 后正常结束即可：

```python
import sys
try:
    # ... 业务逻辑 ...
    pass
except FileNotFoundError as e:
    print(f"错误：文件不存在 - {e}", file=sys.stderr)
except PermissionError as e:
    print(f"错误：无权限访问文件 - {e}", file=sys.stderr)
except Exception as e:
    print(f"错误：{type(e).__name__}: {e}", file=sys.stderr)
```
