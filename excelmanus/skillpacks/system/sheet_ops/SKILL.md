---
name: sheet_ops
description: 工作表管理与跨表操作技能包，覆盖工作表查看、创建、复制、重命名、删除和跨表数据传输
file_patterns:
  - "*.xlsx"
  - "*.xlsm"
version: "1.0.0"
---
工作表管理与跨表操作的标准流程：

1. 探查阶段
- 必须先用 `inspect_spreadsheet(mode="overview")` 了解文件中有哪些工作表及其结构。
- 对需要操作的工作表，用 `inspect_spreadsheet(mode="range", sheet_name=...)` 确认数据内容。

2. 执行阶段
- 单表管理：使用 `edit_spreadsheet(kind="sheet")` 创建/复制/重命名/删除工作表，已有文件必须带 `expected_version`。
- 跨表数据传输：使用 `edit_spreadsheet` 或 `run_code` 经 SDK 意图提交，禁止脚本里直接 `wb.save`。
- 写入数据到指定表：使用 `edit_spreadsheet`，已有文件必须带 `expected_version`。

3. 验证阶段
- 操作后再次 `inspect_spreadsheet(mode="overview")` 确认工作表结构变更正确。
- 必要时 `inspect_spreadsheet(mode="range")` 核查目标工作表数据。

安全约束：
- 删除工作表需二次确认（confirm=true）。
- 跨表复制前先确认源范围，避免复制空数据。
- 涉及多表写入时建议先备份文件（copy_file）。
