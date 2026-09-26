---
name: spreadsheet_workflow
description: 使用 ExcelManus V2 工具完成 Excel/CSV/TSV 的读取、分析、编辑、校验、重算、渲染与交付；适用于需要可靠版本绑定和可核验结果的表格任务。
file_patterns:
  - "*.xlsx"
  - "*.xlsm"
  - "*.xls"
  - "*.xlsb"
  - "*.csv"
  - "*.tsv"
resources:
  - references/tool_matrix.md
  - references/recipes.md
  - references/quality_security.md
version: "1.0.0"
---

# ExcelManus 表格工作流

这是 ExcelManus 的统一表格入口。加载本技能后，可以参考下面的闭环处理任务；工具合同和当前工作区事实决定具体路线。

## 先判断任务边界

区分四件事：目标文件、目标工作表/范围、是查看还是写入、交付是原文件还是新文件。目标不明确时先用 `list_directory` 或 `observe_spreadsheet` 找证据；只有多个候选都合理时才询问用户。只读请求保持只读模式，不为了“顺手整理”修改文件。

### 图片或扫描件还原的可选路线

用户要求把收据、发票或截图还原成可编辑工作簿时，可以把原图加载到视觉上下文，并选择 `read_image(analyze_layout=true)` 获取有界的行列线候选、墨迹边界和源尺寸。常见的后续组合是结合原图整理文字、明细、合计、合并区与布局，再用 `apply_spreadsheet_changes(workbook_spec=...)` 创建 `purpose=visual_replica` 的工作簿，随后用 `calculate_spreadsheet`、`validate_spreadsheet` 和 `preview_spreadsheet` 做核验。候选不足时可以使用 `read_image(crop={x,y,width,height,zoom})`；同一附件同一 detail/crop 在本轮只读取一次，小字或局部不清时一次裁剪放大，只有换区域或放大倍数时才再次取图。也可以用 `run_code` 做自定义测量或计算；工具返回的候选、原图观察和 uncertainties 都可作为路线判断依据。

## 常见闭环

1. **观察**：已有工作簿先调用 `observe_spreadsheet(mode="overview")`。需要具体数据时调用 `mode="range"`，并按需选择 `facets=["data", "presentation", "geometry", "objects", "dependencies"]`。记录返回的 `file_path`、`sheet`、`content_version`、`observation_id` 和 `coverage`。大表只读需要的窗口，不把整个工作簿倾倒进上下文。
2. **分析或预览**：筛选、分组、质量检查和重复键用 `analyze_spreadsheet`；公式关系用 `trace_spreadsheet_formulas`；两个版本或文件用 `compare_spreadsheets`。涉及样式、尺寸、合并、图表或打印效果时，用 `preview_spreadsheet` 取得绑定版本的图像和几何证据。
3. **选择最小变更**：单个或少量单元格用 `apply_spreadsheet_changes` 的 `cells.patch`；连续矩阵、筛选结果或追加数据用 `write`/`append`；工作表、格式、尺寸、冻结、条件格式、验证、图表和打印设置也都放在同一个 `operations` ChangeSet。复杂字段先用 `introspect_capability(query_type="tool_detail", query="apply_spreadsheet_changes.operations.<kind>")` 查当前合同。
4. **绑定版本并提交**：修改已有文件必须把最近一次成功读取或写入的 `content_version` 传为 `expected_version`。筛选后写回必须原样使用返回的 `selection`，不能按投影列或排序后的序号重算行号。新建文件使用 `apply_spreadsheet_changes(workbook_spec=...)`，图片/版式还原显式传 `purpose=visual_replica`；输出新文件时保留源文件并写入 `outputs/`。工作区只有 CSV、尚无 xlsx 时该工具仍可用（新建不依赖已有 xlsx）：直接用 `file_path=outputs/<名>.xlsx` + `workbook_spec=...` 产出交付物，不要用 openpyxl/pandas 直存。`convert_spreadsheet` 只用于真正的格式转换（xls/xlsb 等转 xlsx，或 `mode="data_only"` 仅迁移值），不是新建工作簿的前置步骤；它的产物工作表名是 `input`（不是 CSV 文件名，引用前先 `observe_spreadsheet(overview)` 确认），不要在导入件上做保真假设。依赖已有工作簿的 `trace_spreadsheet_formulas` 在 CSV-only 下仍被目录门控，等 `outputs/`（或工作区顶层）出现 xlsx 后下一轮目录自动解锁。
5. **提交后核验**：检查工具返回的 `status`、`committed`、`receipt`、`applied` 和新的 `content_version`。受影响区域用 `observe_spreadsheet` 回读；公式或计算输入变更后调用 `calculate_spreadsheet`（成功重算即包含全簿公式错误检查）；业务规则用 `validate_spreadsheet`。写入回执 `formula_cache=preserved_unchanged_calculation_inputs` 表示计算输入未变、缓存原样保留，无需重算。单元格版式用 `preview_spreadsheet(surface="workbench")`；图表使用默认 `surface="auto"` 完整预览，核对 `visual_coverage` 和裁切提示；实际打印分页用 `surface="print"`，工作台不渲染图表。需要 PDF/PNG 交付时才调用 `render_spreadsheet`；仅检查单页使用 `preview_spreadsheet(page=...)`，无需自行启动 `pdftoppm` 子进程。
6. **交付证据**：说明实际输出路径、变更范围、版本/校验结果、覆盖范围和无法验证的限制。只有工具成功提交并且回读/预览支持时，才声称“已完成”。失败或版本冲突时不要重放旧 ChangeSet，先重新观察再生成新计划。

## 工具路由

多步骤报表/看板可先 `introspect_capability(query_type="knowledge_workflow", query="spreadsheet-report")` 获取当前可执行的组合示例和版本依赖；不必逐个查询所有样式类型。

销售看板等小型分析任务可以先确认年份、月份、区域和指标，按“年份×月份×区域”得到最细结果，再复用它计算区域同比、月度趋势及结论。aggregate、pivot 和全量读取可依据数据规模与目标选择；同比保持年份维度，月份按数值排序；联合唯一键通常包含年份、月份、区域。静态结论中的数字从已校验结果生成；异常高值可以标为待核实，并结合业务信息判断。

- 结构、表头、值、样式、尺寸、对象：`observe_spreadsheet`。
- profile、quality、filter、aggregate、distinct、pivot、relationships、files：`analyze_spreadsheet`。
- 公式依赖：`trace_spreadsheet_formulas`；文件/版本差异：`compare_spreadsheets`。
- 创建或修改：`apply_spreadsheet_changes`，工作簿意图的提交入口（只有 CSV 的工作区里也能直接新建，按「必经闭环」第 4 步用 `workbook_spec` 产出第一个 xlsx，不要直接放弃）。
- 重算：`calculate_spreadsheet`；确定性规则校验：`validate_spreadsheet`。
- 预览：`preview_spreadsheet`；导出 PDF/PNG：`render_spreadsheet`；格式转换：`convert_spreadsheet`。
- 大结果或 SQL 汇总：`query_spreadsheet`；按列拆分：`split_spreadsheet`；检查点/恢复：`manage_spreadsheet_versions`。
- 循环、跨文件编排或自定义计算：`run_code` 中 `import em`，仍然通过 `em.*` 工具读写，不能调用 `wb.save()`、`df.to_excel()` 或绕过版本检查。

## 字段合同速查

猜参数前先对合同。三份合同互不通用：创建（workbook_spec）用 workbook_spec 键名，改样式（operations）用 operations 键名，校验/分析各有自己的 rules/aggregations 形状；除下述同义键外混用键名会被拒（SPEC_VALIDATION_FAILED / INVALID_ARGS）。

**创建 workbook_spec（`apply_spreadsheet_changes(workbook_spec=...)`）**

- 样式 `fill` 只收 `{type, color, end_color}`：`type` 默认 `solid`；`color`/`end_color` 写十六进制（`"FFC7CE"` 或 `"#FFC7CE"`）。
- `font` 收 `{name, size, bold, italic, color, underline, strike}`。
- `uncertainties` 每项收 `{location, reason, candidate_values?, confidence?}`；`uncertainties` 字段必填，没有不确定项时传 `[]`。
- 与 ChangeSet operations 样式键名对照：`patternType`/`fill_type`/`pattern`→`type`，`fgColor`/`fg_color`/`fgcolor`/`start_color`→`color`，`strikethrough`→`strike`。创建用 workbook_spec 键名（`type`/`color`/`end_color`），改已有工作表样式用 operations 键名（`patternType`/`fgColor`）；混用只有这些同义键会被归一，其余混入的键名会被拒。

**validate_spreadsheet 的 rules**

每条规则 `kind` 必填；可选 `sheet`（多表工作簿必须指定，`formula_errors` 除外）与 `header_row`（默认 1）：

| kind | 需要的字段 |
| --- | --- |
| `total` | `column`（单列）+ `expected`，`tolerance?`（默认 1e-9） |
| `cell` | `cell`（A1 单格，如 `B7`；区域、纯列/行、`表名!B7`、越界坐标报 INVALID_ARGS）+ `expected`（数值/文本/布尔/null，`null` 断言该格为空），`tolerance?`（默认 1e-9，数值用）、`case_sensitive?`（默认 true，文本用） |
| `row_expression` | `expression` |
| `unique` / `required` | `columns`（单列可写 `column`） |
| `foreign_key` | `columns` + `reference`=`{file_path, columns, sheet?, header_row?, expected_version?}`（`sheet?` 省略时取参考簿活动表；`reference.columns` 同样支持列名、列字母、1-based 列号） |
| `formula_errors` | 无附加字段；不传 `sheet` 时检查全部工作表 |

- `column`/`columns` 支持表头列名、列字母（`A`/`AA`）或 1-based 列号；列名与列字母同名时列名优先。
- `expression` 是行内表达式语言，不是 Excel 公式：当前行的列名或列字母直接引用（`金额`、`E`；`{row}` 是可选占位符，`E{row}` 等价 `E`）；支持 `+ - * / %` 算术、`== != < <= > >=` 比较、`and/or/not` 与括号，以及大小写不敏感的 `abs/round/min/max` 函数。列名含空格、括号等不是合法标识符时改用列字母。
- 正确例子：`abs(金额-数量*单价)<0.01`；`ABS(E{row}+F{row}+G{row}-C{row})<0.05`。不要写 `=ABS(...)`、`SUMIF` 这类 Excel 公式。
- 断言具体单元格用 `cell`（`row_expression` 不支持 A1 引用，`total` 是整列求和）：`{"kind":"cell","sheet":"回归分析","cell":"B7","expected":6.707067669172933,"tolerance":0.0001}`；失败项为 `{rule,kind,sheet,cell,expected,actual,delta?}`，`actual` 是真实数值/文本/null，不是说明串。

**analyze_spreadsheet(mode="aggregate") 的 aggregations**

两种形状等价（也可传等价 JSON 字符串）：`{"销售额": ["sum", "mean"]}`（列名→函数或函数数组）与 `[{"column": "销售额", "func": "sum"}]`。函数固定为 `sum/count/mean/min/max/median/std/nunique/first/last`；`"*"` 表示行计数。TopN 不是函数：用 `sort_by`（聚合输出列名如 `销售额_sum`，或源列名自动映射）+ `max_rows` + `ascending=false`（V2 入口不接受 `limit` 别名，传了会被判为未知字段）。

## 不可破坏的约束

- 观察事实和工具返回是唯一证据；`partial`、`sampled`、`not_requested`、`unsupported` coverage 不能当作全表结论。
- 公式字符串和计算缓存是两件事。改动公式或任何计算输入后需要显式 `calculate_spreadsheet`；缺少缓存的单元格不能当作零或空值。纯格式/尺寸/合并等改动不改变计算输入，回执 `formula_cache=preserved_unchanged_calculation_inputs` 时缓存仍有效，不要为此重算。
- 改动要可重试且幂等：优先“确保某列/某工作表存在并更新到目标状态”，避免“在末尾再加一列”这类重复重试会叠加的动作。每批写入使用上批返回的版本。
- `dry_run=true` 可用于大范围或高风险变更；它只能验证计划，不能当作提交成功。删除行、列、工作表或覆盖数据前确认范围和影响。
- `data_only`、只读解析、临时副本和渲染产物不得回写源文件。宏文件保留原件；不执行宏，也不把对象损失隐藏在转换结果里。
- 用户输入若可能被解释为公式、外部链接或超链接，先按工具 schema 明确它是值还是公式，并在验证中检查异常引用；不要直接把未经判断的字符串批量写入公式区域。

## 何时读取补充资料

- 需要具体参数、操作种类或返回字段：读取 [references/tool_matrix.md](references/tool_matrix.md)，仍以 `introspect_capability` 的实时 schema 为准。
- 需要创建、编辑、分析、格式、图表或大表的可执行片段：读取 [references/recipes.md](references/recipes.md)。
- 需要质量、性能、安全或外部依据：读取 [references/quality_security.md](references/quality_security.md)。

交付前优先报告用户真正关心的结果，不输出大段原始单元格 JSON；必要时给出范围、版本、校验和预览证据。
