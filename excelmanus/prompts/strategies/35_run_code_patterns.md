---
name: tool:run_code
version: "6.0.0"
priority: 150
order: 150
layer: strategy
max_tokens: 400
conditions: {}
---
## Code Mode

`run_code` 用来组合已注册 SDK，或处理领域工具盖不住的批量变换。单格修改、加粗、增删工作表不要写 pandas / openpyxl 落盘脚本。

```python
from em import inspect_spreadsheet, edit_spreadsheet, format_spreadsheet
inspect_spreadsheet(mode="range", file_path="book.xlsx", sheet_name="Sheet1")
edit_spreadsheet(file_path="book.xlsx", operations=[
    {"kind": "write", "sheet": "Sheet1", "start_cell": "B2", "values": [[100]]},
])
```

写入必须串行。脚本含顶层 try/except，证据 print 到 stdout/stderr。stdout 和成功退出码不证明业务正确。

AST：**GREEN**（pandas/openpyxl/numpy）自动执行；**YELLOW** 网络被拦截；**RED**（subprocess/exec/eval）需 `/accept`。工作区外写入、socket、os.system 被禁止。复制文件用 `copy_file`。
