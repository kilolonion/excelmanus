---
name: tool:changes
version: "2.0.0"
priority: 104
order: 104
layer: strategy
max_tokens: 230
conditions:
  catalog_mode: write
  tool: apply_spreadsheet_changes
---
apply_spreadsheet_changes 用同一批 operations 修改值、公式、格式、尺寸和对象；新建可用 workbook_spec。已有文件必填 observed content_version 作为 expected_version。查 operations.<kind> 获取真实字段。geometry.scale 的 x 是横向、y 是纵向；size 用 column_widths（字符）与 row_heights（pt）。列宽行高影响整轴，注意同轴其他区域。auto_fit 只用于按内容适配，不覆盖明确的比例目标。receipt 表示提交状态；observation 给出落盘核对和覆盖。提交后观察失败不能重放；VERSION_CONFLICT 后重新读取再判断。uploads/ 只读。
