---
name: tool:preview
version: "2.0.0"
priority: 106
order: 106
layer: strategy
max_tokens: 140
conditions:
  tool: preview_spreadsheet
---
preview_spreadsheet 同次返回图像、区域坐标和版本；默认 auto 覆盖图表，workbench 不画图表，print 检查分页。核对 visual_coverage；裁切按 next_calls 修正范围。旧图不证明新改动。
