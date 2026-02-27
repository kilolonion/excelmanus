# 数据写入与 Sheet 管理模板

## 写入数据（pandas 读→改→写回，保留其他 sheet）

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

## 单元格级写入

```python
from openpyxl import load_workbook
wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]
ws["A1"] = "新值"
ws["B2"] = 100
wb.save("file.xlsx")
```

## 插入行

```python
from openpyxl import load_workbook
wb = load_workbook("file.xlsx")
ws = wb["Sheet1"]
ws.insert_rows(5, amount=3)  # 在第5行前插入3行
wb.save("file.xlsx")
```

## 条件删除行

```python
import pandas as pd
df = pd.read_excel("file.xlsx", sheet_name="Sheet1")
df = df[df["状态"] != "已取消"]  # 删除满足条件的行
with pd.ExcelWriter("file.xlsx", engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
    df.to_excel(w, sheet_name="Sheet1", index=False)
```

## 跨表匹配写回（VLOOKUP 等价）

```python
import pandas as pd
src = pd.read_excel("file.xlsx", sheet_name="源表")
tgt = pd.read_excel("file.xlsx", sheet_name="目标表")
merged = tgt.merge(src[["键列","值列"]], on="键列", how="left")
with pd.ExcelWriter("file.xlsx", engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
    merged.to_excel(w, sheet_name="目标表", index=False)
```

## Sheet 管理

```python
wb.create_sheet("新表")              # 新建
wb.copy_worksheet(wb["Sheet1"])      # 复制
wb["Sheet1"].title = "新名称"         # 重命名
del wb["要删除的表"]                   # 删除
```

## 标准异常处理

`run_code` 脚本必须包含顶层异常捕获，确保错误信息友好可读。
**禁止**使用 `sys.exit()`、`exit()` 或 `os._exit()`，会触发安全拦截。只需 print 到 stderr，脚本正常结束即可：

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
