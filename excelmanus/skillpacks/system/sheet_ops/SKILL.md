---
name: sheet_ops
description: 工作表管理与跨表操作。
file_patterns:
  - "*.xlsx"
  - "*.xlsm"
version: "1.1.0"
---
单表的创建、复制、重命名、删除走 `apply_spreadsheet_changes` 的 `operations=[{"kind":"sheet","action":...}]`。已有文件需带 `expected_version`。

删除工作表直接执行（不能删唯一表）；不要传 `confirm`。文件删除才是 `delete_file` + `confirm=true`。跨表写入同样走意图提交，脚本里直接 `wb.save` 会失败。
