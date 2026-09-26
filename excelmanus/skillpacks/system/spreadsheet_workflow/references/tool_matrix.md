# ExcelManus 表格工具矩阵

这是导航，不是脱离运行时的固定 schema。字段、枚举和操作种类变动时，调用 `introspect_capability` 获取当前版本；以下名称与 ExcelManus V2 的工具注册保持一致。

## 读取与证据

| 目的 | 工具 | 使用方式 | 关键证据 |
| --- | --- | --- | --- |
| 发现工作表和使用范围 | `observe_spreadsheet` | `mode="overview"` | `sheets`、使用范围、对象计数、`content_version` |
| 读取有限区域 | `observe_spreadsheet` | `mode="range"` + `sheet` + `range` | `regions[].cells` 使用原始行列坐标；`coverage` 说明是否完整 |
| 查找值或公式 | `observe_spreadsheet` | `mode="search"` + `query` | `matches` 和版本 |
| 看版式和对象 | `observe_spreadsheet` | `facets=["presentation","geometry","objects"]` | 样式、合并、尺寸、隐藏轴、对象和打印设置 |
| 看图像 | `preview_spreadsheet` | 指定 `sheet`、`range`、`surface` | 图像、几何、渲染器信息；不修改文件 |
| 统计或筛选 | `analyze_spreadsheet` | `mode="profile"/"quality"/"filter"/"aggregate"/"distinct"/"pivot"` | 结果、选择句柄、覆盖和版本 |
| 比较 | `compare_spreadsheets` | 有业务键用 `alignment="key"` | 摘要、样本差异、对齐方式和覆盖 |
| 公式依赖 | `trace_spreadsheet_formulas` | 指定工作表/范围/目标 | 先例、依赖、影响范围和不支持项 |

`regions[].cells` 的键是原始 `row,column`，不是投影后的数组索引。`selection`、`spill:` 句柄和 `content_version` 都是不透明值，原样传递，不自行拼接、截断或改名。

## 变更与引擎

| 目的 | 工具/操作 | 规则 |
| --- | --- | --- |
| 创建工作簿 | `apply_spreadsheet_changes(workbook_spec=...)` | 先定义文档目的、工作表、值块和公式；需要字段时查询 `workbook_spec` schema。工作区只有 CSV、尚无 xlsx 时该工具仍可用：新建不依赖已有 xlsx，直接 `file_path=outputs/<名>.xlsx` + `workbook_spec` 产出交付物；`convert_spreadsheet` 只用于真正的格式转换，不是前置步骤 |
| 写入矩阵 | `operations=[{"kind":"write", ...}]` | 新区域用 `start_cell`；筛选回写用观察返回的 `selection` |
| 小范围改值/样式 | `kind="cells.patch"` | 每个 cell 保留真实地址；值和 style 按 schema 传 |
| 工作表维护 | `kind="sheet"` | create/rename/copy/delete 等字段以当前 schema 为准；不能删除唯一工作表 |
| 变换 | `kind="transform"` | 去重、分列、规范化等先看 `action` 合同；需要保留公式/对象时让工具拒绝不安全变换 |
| 样式和布局 | `format`、`size`、`geometry.scale`、`geometry.resize`、`merge`、`freeze` | 同一批提交，先观察 presentation/geometry；不要用比例覆盖用户指定尺寸 |
| 条件格式/验证 | `conditional_format`、`data_validation` | 规则字段从实时 schema 查询；公式相对行号以范围左上角为基准 |
| 图表/表格/对象 | `chart`、`table` 以及注册的对象 kind | 原生图表数据范围应含表头；对象边界以 observe/preview 复核 |
| 公式重算 | `calculate_spreadsheet` | 绑定源版本；错误默认不发布，除非用户明确允许并能解释 |
| 规则校验 | `validate_spreadsheet` | 结果可能是 `partial`，同时报告 `uncalculated_cells` |
| 输出渲染 | `render_spreadsheet` | `format="pdf"` 或 `"png"`；输出到新文件，核对页数和引擎 |
| 版本管理 | `manage_spreadsheet_versions` | restore 必须带当前 `expected_version`；恢复后重新观察 |

写入成功的回执是提交证据；`applied` 只是已应用操作摘要，不能替代回读。`observation` 若出现在回执中仍要看 `coverage` 和 `visual_observed`。

## `run_code` 边界

代码中使用：

```python
from em import observe_spreadsheet, apply_spreadsheet_changes

seen = observe_spreadsheet(
    file_path="outputs/book.xlsx",
    sheet="明细",
    mode="range",
    range="A1:H1000",
    facets=["data"],
)
changed = apply_spreadsheet_changes(
    file_path="outputs/book.xlsx",
    expected_version=seen["content_version"],
    operations=[{
        "kind": "write",
        "sheet": "明细",
        "start_cell": "H2",
        "values": [["已处理"]],
    }],
)
print({"status": changed.get("status"), "version": changed.get("content_version")})
```

循环读取时用有界 `range` 或 `offset`，每批记录版本并检查 `status`；中断后先观察已提交范围，不重放全部批次。代码只输出判断所需的摘要。
