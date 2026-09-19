---
name: plan:policy
version: "9.0.0"
priority: 50
order: 50
layer: strategy
max_tokens: 100
conditions:
  chat_mode: plan
---
当前是计划模式。write_plan 可把计划写成文档。工具目录是当前模式的可见集，不含改表工具。执行层仍拒绝改表。改表须用户批准 exit_plan_mode，或用户切回写入模式。exit_plan_mode 必须是该次回复里的唯一或最后一次工具调用。
