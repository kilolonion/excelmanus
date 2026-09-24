---
name: spreadsheet:document
version: "2.0.0"
priority: 110
order: 110
layer: strategy
max_tokens: 210
conditions:
  catalog_mode: write
  tool: apply_spreadsheet_changes
---
WorkbookSpec V2 只用于新建，字段使用规范对象。purpose=data 提取数据可用默认布局；purpose=visual_replica 需完整列宽行高或 layout_reference（已观察图片、表格 bbox、相对行列边界、目标总宽）。尺寸由同一 ChangeSet 编译；像素不能直接当字符列宽或 pt。保留看不清的 uncertainties，必要时 read_image crop 看局部。复刻后用 preview_spreadsheet 对照源图，按需要修正；成功生成文件只证明提交，不证明视觉一致。
