# V2 格式与布局

读取目标版本后，把格式、尺寸和对象放在同一个 ChangeSet。

```python
from em import observe_spreadsheet, apply_spreadsheet_changes, preview_spreadsheet
seen = observe_spreadsheet(file_path="outputs/book.xlsx", sheet="Sheet1", mode="range", range="A1:D20",
                           facets=["data", "presentation", "geometry"])
changed = apply_spreadsheet_changes(file_path="outputs/book.xlsx", expected_version=seen["content_version"], operations=[
    {"kind": "format", "sheet": "Sheet1", "range": "A1:D1", "font": {"bold": True}, "fill": {"color": "4472C4"}},
    {"kind": "geometry.scale", "sheet": "Sheet1", "range": "A1:D20", "x": 1.25, "y": 0.8},
])
print(changed["receipt"], changed["observation"])
preview_spreadsheet(file_path="outputs/book.xlsx", sheet="Sheet1", range="A1:D20", expected_version=changed["content_version"])
```

比例只是示例，按用户目标选择。固定尺寸用 `kind=size` 的 column_widths 和 row_heights，单位分别为字符、pt。
按内容适配须显式指定 axis 与 range；不得用 auto_fit 覆盖已指定的比例。
条件格式规则查询 `apply_spreadsheet_changes.operations.conditional_format.rule`；验证规则查询 `.operations.data_validation.rule`。
循环写入串行。工具成功是提交状态，视觉覆盖与未支持项以预览和回读的证据为准。
