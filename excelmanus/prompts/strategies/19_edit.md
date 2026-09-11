---
name: tool:edit
version: "9.0.0"
priority: 104
order: 104
layer: strategy
max_tokens: 200
conditions: {}
---
已有文件必须使用最近返回的 content_version。VERSION_CONFLICT 表示这次没有落盘。不要重放旧批次。相关改动打成一次请求；不要与另一次写入并行。
