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
preview_spreadsheet 同次返回图像、区域坐标和版本，read/plan 也可用。workbench 为工作台图，print 为打印页，按任务选取；limitations 中的缺失对象、未求值规则和字体替换不能当成已验证。大表分范围查看，放大细节用裁剪。旧版本图像不能作为新改动的证据；模型不支持视觉时只报告结构观察范围。
