# 工具能力地图注入设计

日期：2026-02-15
状态：已批准

## 背景

Bench 测试 #44628 暴露了一个问题：Agent 在 discovery 模式下（无技能激活）只有只读工具可用，面对写入需求时没有调用 `select_skill` 激活技能，而是直接回复"需要写入权限"放弃任务。

根因：LLM 不知道系统中还有哪些未激活的工具可用，也没有足够强的指引告诉它"功能不足时必须主动激活技能"。

## 方案

在 system prompt 中注入"工具能力地图"，让 LLM 在首轮就能看到完整的工具全景，并强化自主激活技能的行为指引。

### 改动 1：工具索引增加未激活工具区块

当 Agent 处于 discovery 模式（无技能激活）时，system prompt 的工具索引部分追加未激活工具列表：

```
## 工具索引
- 当前可用（只读探查）：read_excel, inspect_excel_files, analyze_data, filter_data, group_aggregate, list_sheets, list_directory, get_file_info, find_files, read_text_file, read_cell_styles
- 未激活（需 select_skill 激活）：
  · 数据写入：write_excel, write_cells, transform_data, insert_rows, insert_columns
  · 格式化：format_cells, adjust_column_width, adjust_row_height, merge_cells, unmerge_cells
  · 高级格式：apply_threshold_icon_format, add_color_scale, add_data_bar, add_conditional_rule, ...
  · 图表：create_chart, create_excel_chart
  · 工作表操作：create_sheet, copy_sheet, rename_sheet, delete_sheet, copy_range_between_sheets
  · 文件操作：copy_file, rename_file, delete_file
  · 代码执行：run_code, run_shell, write_text_file [需 fullAccess]
```

当技能已激活时，不注入此区块（避免冗余）。

### 改动 2：强化工具策略措辞

在 system prompt 的工具策略段落中，将现有的隐含指引替换为显式规则：

```
- **能力不足时自主激活**：当任务需要的工具不在当前可用列表中时，
  立即调用 select_skill 激活对应技能。
  禁止向用户请求权限、声称无法完成、或建议用户手动操作。
  参考"未激活工具"列表判断该激活哪个技能。
```

### 改动 3：select_skill description 微调

在 `select_skill` 的 description 中追加一句：
"当你发现当前工具无法完成任务时，必须调用本工具激活技能，不要向用户请求权限。"

## 实现位置

- `excelmanus/engine.py`：
  - system prompt 构建逻辑中新增 `_build_inactive_tools_section()` 方法
  - 工具策略段落措辞更新
  - `_build_meta_tools()` 中 `select_skill` description 微调
- `excelmanus/tools/policy.py`：复用已有的 `TOOL_CATEGORIES` 和 `DISCOVERY_TOOLS`

## 不改动

- 路由逻辑（`router.py`）不变
- tool_scope 决策逻辑不变
- 工具注册和执行逻辑不变

## 验证

用同一道 bench 题 #44628 重新运行，预期：
- Agent 首轮应调用 `select_skill` 激活 general_excel 技能
- 获得 write_cells 后写入 SUMIF 公式到 D3:G3
- 不再出现"需要写入权限"的放弃行为
