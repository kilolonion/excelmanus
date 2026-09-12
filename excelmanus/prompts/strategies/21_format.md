---
name: tool:format
version: "9.0.0"
priority: 106
order: 106
layer: strategy
max_tokens: 250
conditions: {}
---
工作表必须用 sheet，或在 range 里写 表!A1。range 写 A1:C5，也接受 区域汇总!A5:C5。合并区的填充、边框、对齐以锚点格为准。非锚点回读为空或 fill 为空不是缺陷。边框是整对象替换。font/alignment 按传入键叠加。工具回报已应用不等于每个格子都写下了。
