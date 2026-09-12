---
name: format_basic
description: 已有工作表上的样式（颜色、字体、边框、填充、合并、行列尺寸）。
file_patterns:
  - "*.xlsx"
resources:
  - references/color_palette.md
  - references/aesthetic_guide.md
version: "4.1.0"
---
样式走 `format_spreadsheet`。合并区写锚点。工作区 xlsx 直接 `wb.save` 会失败。

用户明确要求美化时，可再读 `references/aesthetic_guide.md`。

```
format_spreadsheet(
  file_path="book.xlsx",
  expected_version=...,
  operations=[{
    "kind": "format",
    "sheet": "区域汇总",
    "range": "A5:C5",
    "fill": {"color": "FFCC00"}
  }]
)
```

`range` 写 A1:C5，也接受 `区域汇总!A5:C5`。多表时请带 `sheet`。列宽用 `kind=size` 的 `columns={"A":18}`，不要把 inspect 的列宽字典塞进 WorkbookSpec 时再换成另一种形状——两种都接受。
