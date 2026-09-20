---
name: tool:inspect
version: "12.0.0"
priority: 100
order: 100
layer: strategy
max_tokens: 170
conditions:
  tool: inspect_spreadsheet
---
overview 看结构，range 读窗口，search 定位。截断/采样不是全表事实；分页检查 content_version，变化就重读。range/filter 的 selection 保留原表 `source_cols`，写回直接消费，不要按投影列重编号；大 selection 用 `spill:` 句柄传给 edit。默认读缓存值；null 且带 formula 是未计算。range 仅支持 include=formulas；多表省略 sheet 仅在证据唯一时允许。
