---
name: excel_code_runner
description: 已有大表的分批计算与写入。
file_patterns:
  - "*.xlsx"
  - "*.xlsm"
  - "*.xls"
resources:
  - references/largefile_code_workflow.md
version: "2.1.0"
---
`run_code` 经 SDK 分批处理大表。不要把临时脚本当作用户产物。不要创建 `_probe_*.xlsx`。
