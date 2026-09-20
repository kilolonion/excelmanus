---
name: tool:split
version: "1.1.0"
priority: 107
order: 107
layer: strategy
max_tokens: 70
conditions:
  catalog_mode: write
  tool: split_spreadsheet
---
按列拆成多个 xlsx 时优先用 split_spreadsheet。产物只新建不覆盖：目标已存在会整批取消。不要用逐文件 copy+edit 循环替代已有拆分工具。
