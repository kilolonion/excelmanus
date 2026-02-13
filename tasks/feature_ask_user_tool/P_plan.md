# P 阶段执行计划

## WBS
1. 新增 `question_flow` 模块（模型、校验、解析、队列）。
2. 改造 `engine`：元工具定义、挂起恢复、队列消费、阻塞 slash。
3. 扩展事件与输出：`events`、`api` SSE、`renderer`、CLI 多行输入。
4. 补齐测试：`question_flow` 新增，`engine/events/api/renderer/cli` 更新。
5. 回归测试与验收文档更新。

## DoD
- `ask_user` 能触发并挂起，用户回答后自动恢复原任务。
- 支持同轮多问题 FIFO。
- 多选题 CLI 多行输入可解析。
- `safe_mode=true` 下 SSE 仍发送 `user_question`。
- 受影响测试全部通过。
