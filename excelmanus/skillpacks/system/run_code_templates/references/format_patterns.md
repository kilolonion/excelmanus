# 格式化、图表与打印模板

工作区 xlsx 的改写必须走 SDK（`format_spreadsheet` / `edit_spreadsheet` / `split_spreadsheet` / `manage_spreadsheet_objects`）。不要 `wb.save`，也不要指望 `Font(color="red")` 在 openpyxl 里识别中文/英文颜色名。

## 格式化样式

```python
from em import format_spreadsheet, inspect_spreadsheet

version = inspect_spreadsheet(file_path="file.xlsx", mode="overview")["content_version"]

format_spreadsheet(
    file_path="file.xlsx",
    expected_version=version,
    operations=[{
        "kind": "format",
        "sheet": "Sheet1",
        "range": "A1",
        "font": {"name": "微软雅黑", "bold": True, "color": "红色", "size": 12},
        "fill": {"color": "黄色", "type": "solid"},
        "alignment": {"horizontal": "center", "vertical": "center", "wrap_text": True},
        "number_format": "#,##0.00",
    }, {
        "kind": "size",
        "sheet": "Sheet1",
        "columns": {"A": 20},
        "rows": {"1": 30},
    }],
)
```

颜色名只走 format helper（见 format_basic/color_palette.md），不要写 `Font(color="red")`。

## 批量格式化（区域）

```python
from em import format_spreadsheet, inspect_spreadsheet

version = inspect_spreadsheet(file_path="file.xlsx", mode="overview")["content_version"]

format_spreadsheet(
    file_path="file.xlsx",
    expected_version=version,
    operations=[{
        "kind": "format",
        "sheet": "Sheet1",
        "range": "A1:J1",
        "font": {"bold": True},
        "fill": {"color": "4472C4", "type": "solid"},
    }],
)
```

## 合并单元格

默认保留所有值；非锚点有值时先整理内容。只有明确要舍弃非锚点值时才传 allow_data_loss=true。

```python
from em import format_spreadsheet, inspect_spreadsheet

version = inspect_spreadsheet(file_path="file.xlsx", mode="overview")["content_version"]

format_spreadsheet(
    file_path="file.xlsx",
    expected_version=version,
    operations=[{"kind": "merge", "sheet": "Sheet1", "range": "A1:D1"}],
)
```

## 条件格式 / 打印 / 数据验证

条件格式和数据验证已经可用，使用 `format_spreadsheet` 的 `operations`，先查询
`format_spreadsheet.operations.rule` 的字段合同。不要用 openpyxl 改完再期望 SDK
提交能带上。打印设置是否可用以当前能力目录为准，不把它与条件格式混为一谈。

## 图表

```python
from em import manage_spreadsheet_objects, inspect_spreadsheet

version = inspect_spreadsheet(file_path="file.xlsx", mode="overview")["content_version"]

manage_spreadsheet_objects(
    file_path="file.xlsx",
    expected_version=version,
    operations=[{
        "kind": "chart",
        "sheet": "Sheet1",
        "chart_type": "bar",
        "data_range": "B1:B10",
        "categories_range": "A2:A10",
        "target_cell": "E2",
        "title": "销售额",
    }],
)
```

## 专业对齐（按列数据类型）

```python
from em import format_spreadsheet, inspect_spreadsheet

version = inspect_spreadsheet(file_path="file.xlsx", mode="overview")["content_version"]

format_spreadsheet(
    file_path="file.xlsx",
    expected_version=version,
    operations=[
        {"kind": "format", "sheet": "Sheet1", "range": "A1:D1", "alignment": {"horizontal": "center"}},
        {"kind": "format", "sheet": "Sheet1", "range": "A2:B100", "alignment": {"horizontal": "left"}},
        {"kind": "format", "sheet": "Sheet1", "range": "C2:D100", "alignment": {"horizontal": "right"}},
    ],
)
```

## auto_fit 收尾

```python
from em import format_spreadsheet, inspect_spreadsheet

version = inspect_spreadsheet(file_path="file.xlsx", mode="overview")["content_version"]

format_spreadsheet(
    file_path="file.xlsx",
    expected_version=version,
    operations=[{"kind": "size", "sheet": "Sheet1", "auto_fit": True}],
)
```
