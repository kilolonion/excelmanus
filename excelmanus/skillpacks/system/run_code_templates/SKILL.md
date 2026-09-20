---
name: run_code_templates
description: 已有大表的 run_code 模板（批量写入、格式、图表、跨表）。常规聚合、去重、筛选直接用 analyze_spreadsheet 的 aggregate/distinct/filter，不必加载本技能。
file_patterns:
  - "*.xlsx"
  - "*.xlsm"
  - "*.xls"
  - "*.csv"
  - "*.tsv"
resources:
  - references/write_patterns.md
  - references/format_patterns.md
  - references/analysis_patterns.md
  - references/advanced_patterns.md
version: "2.1.0"
---
单次业务操作优先直接调用；循环、跨工具组合和自定义计算使用 `run_code`，同一任务可交替执行。程序通过 `import em` 使用当前授权目录中的工具；未知签名或返回字段先直接查询 `introspect_capability` 的 `tool_detail`，阅读详情后再写程序。

程序内工作区 xlsx 的改写必须走 SDK。stdout 或成功退出码不证明业务正确；程序失败不表示先前写入已回滚，不要重放已提交部分。

参考文档里是可替换的模板，不是必须按顺序执行的流程。
