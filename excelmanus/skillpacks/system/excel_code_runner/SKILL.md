---
name: excel_code_runner
description: 需要自定义 Python 计算、跨工具组合或跨文件循环时的分批处理；现有分析工具能表达的聚合和筛选直接调用即可。
file_patterns:
  - "*.xlsx"
  - "*.xlsm"
  - "*.xls"
  - "*.csv"
  - "*.tsv"
resources:
  - references/largefile_code_workflow.md
version: "2.1.0"
---
同一任务可交替直接调用工具与 `run_code`，无需切换模式。单次业务操作或一次批量参数能表达的工作优先直接调用；本技能用于程序编排。

`import em` 提供当前授权目录中的同名工具。未知参数和返回字段先直接调用 `introspect_capability(query_type="tool_detail", query="工具名")`，阅读详情后再编写程序。程序经 SDK 分批读取和写入，只输出需要判断的结果。不要把临时脚本当作用户产物。不要创建 `_probe_*.xlsx`。
