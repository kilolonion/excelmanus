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
- 必须先用 `list_sheets` 了解文件中有哪些工作表及其结构。
- 对需要操作的工作表，用 `read_excel(sheet_name=...)` 确认数据内容。

2. 执行阶段
- 单表管理：使用 create_sheet / copy_sheet / rename_sheet / delete_sheet。
- 跨表数据传输：使用 `copy_range_between_sheets`，支持同文件或跨文件。
- 写入数据到指定表：使用 `write_excel(sheet_name=...)`，已有文件会保留其他表。

3. 验证阶段
- 操作后再次 `list_sheets` 确认工作表结构变更正确。
- 必要时 `read_excel` 核查目标工作表数据。

安全约束：
- 删除工作表需二次确认（confirm=true）。
- 跨表复制前先确认源范围，避免复制空数据。
- 涉及多表写入时建议先备份文件（copy_file）。
