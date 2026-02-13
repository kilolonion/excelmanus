# 任务：ask_user 结构化澄清工具接入

> **类型**：feature
> **优先级**：P1
> **负责人**：AreaSongWcc
> **状态**：✅ 已完成
> **开始时间**：2026-02-13
> **预计完成**：2026-02-13

## 🎯 目标
在不修改 `frontend/src` 的前提下，新增 `ask_user` 元工具，支持单选/多选/Other、会话挂起恢复、FIFO 多问题队列与 CLI 多行输入。

## 📊 进度仪表盘
| 阶段 | 状态 | 文档链接 |
|------|------|----------|
| R1 调研 | ✅ | 本文 |
| I 设计 | ✅ | 本文 |
| P 规划 | ✅ | [P_plan.md](./P_plan.md) |
| E 执行 | ✅ | [E_execution.md](./E_execution.md) |
| R2 验收 | ✅ | [R2_review.md](./R2_review.md) |

## 📝 关键决策
- 元工具方式接入 `ask_user`（不注册为 ToolRegistry 普通工具）。
- 队列策略采用 FIFO，默认上限 8。
- `safe_mode=true` 下仍透出 `user_question` SSE 事件。
- 前端代码保持零改动，Web 通过现有输入框回答。
- CLI 为多选题增加多行输入（空行提交）。

## 🚨 风险与问题
- 已收敛：`ask_user` 延迟写入 tool result，已通过恢复链路与测试验证。
- 已收敛：同轮多个 tool_call 里出现 `ask_user` 后，后续非 `ask_user` 会被可预测跳过。
