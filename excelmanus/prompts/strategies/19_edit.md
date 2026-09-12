---
name: tool:edit
version: "9.0.0"
priority: 104
order: 104
layer: strategy
max_tokens: 200
conditions: {}
---
已有文件必须使用最近返回的 content_version（写入参数名 expected_version）。VERSION_CONFLICT 表示这次没有落盘。不要重放旧批次。相关改动打成一次请求；不要与另一次写入并行。write 必须带 sheet 或 表!A1；null 清空单元格；字符串按原样写入。copy 只复制值与公式文本，不译相对引用、不拷样式。insert/rename 不维护公式或图表引用；有公式或图表时会拒绝。
