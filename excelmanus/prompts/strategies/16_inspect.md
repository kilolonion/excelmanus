---
name: tool:inspect
version: "12.0.0"
priority: 100
order: 100
layer: strategy
max_tokens: 120
conditions:
  tool: inspect_spreadsheet
---
按任务选择 overview 看结构，或 range 读精确窗口。截断、采样不是全表事实；coverage 与 spill 取回决定是否继续读。resolved_range 只说明本次解析范围，不能代替完整性判断。大结果可能外置为 spill: 句柄；需要原文时把句柄当作 file_path 传给 read_text_file。默认读缓存值；null 且带 formula 是未计算，不是空白。range 的 include 仅 formulas。单表可省略 sheet。
