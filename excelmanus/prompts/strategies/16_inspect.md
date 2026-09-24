---
name: tool:observe
version: "2.0.1"
priority: 100
order: 100
layer: strategy
max_tokens: 230
conditions:
  tool: observe_spreadsheet
---
observe_spreadsheet 读取同版本事实：overview 定位，range 限定区域，facets 组合 data/presentation/geometry/objects/dependencies。区分未查询、空、部分和不支持；分页固定 content_version。版式看 geometry/presentation，图形可超出数据边界。写回保留 regions[].selection 坐标。公式与缓存分开，缺缓存不是空白。preview_spreadsheet 提供版本绑定图像。
