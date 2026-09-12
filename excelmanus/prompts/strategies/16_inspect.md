---
name: tool:inspect
version: "9.0.0"
priority: 100
order: 100
layer: strategy
max_tokens: 300
conditions: {}
---
不要凭记忆编 sheet 或 range。range 可用 A1:F20 或 表名!A1:F20。header_row 从 0 起（Excel 第 1 行 = 0）。截断、采样、推断或缓存只是有条件证据。range 的行列是该窗口，不是整表。range 的 include 仅 formulas；其他值会 INVALID_ARGS。overview 的 include 被丢掉时以返回正文里的警告为准，不要当成「没有样式」。
