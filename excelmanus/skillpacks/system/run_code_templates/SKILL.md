---
name: run_code_templates
description: 已有大表的 run_code 模板（批量写入、格式、图表、跨表）。
file_patterns:
  - "*.xlsx"
  - "*.xlsm"
  - "*.xls"
resources:
  - references/write_patterns.md
  - references/format_patterns.md
  - references/analysis_patterns.md
  - references/advanced_patterns.md
version: "2.1.0"
---
工作区 xlsx 的改写必须走 SDK。stdout 或成功退出码不证明业务正确。

参考文档里是可替换的模板，不是必须按顺序执行的流程。
