# 任务：ExcelManus v3 Tools + Skillpacks 重构

> **历史文档声明（Skillpack 协议）**：本文为历史设计/执行记录，可能包含已过时术语（如 `hint_direct`、`confident_direct`、`llm_confirm`、`fork_plan`、`Skillpack.context`）。现行规则请以 [`../../docs/skillpack_protocol.md`](../../docs/skillpack_protocol.md) 为准。

> **类型**：refactor
> **优先级**：P1
> **负责人**：AreaSongWcc
> **状态**：✅ 已完成
> **开始时间**：2026-02-12
> **完成时间**：2026-02-12

## 🎯 目标
将旧 `skills` 主链路重构为 `tools + skillpacks` 双层架构，移除 MCP 依赖并完成 v3 破坏性升级。

## 📊 进度仪表盘
| 阶段 | 状态 | 文档链接 |
|------|------|----------|
| R1 调研 | ✅ | 本文 |
| I 设计 | ✅ | [P_plan.md](./P_plan.md) |
| P 规划 | ✅ | [P_plan.md](./P_plan.md) |
| E 执行 | ✅ | [E_execution.md](./E_execution.md) |
| R2 验收 | ✅ | [R2_review.md](./R2_review.md) |

## 📝 关键决策
- 主链路由 `ToolRegistry + SkillpackLoader + SkillRouter + AgentEngine` 组成。
- 保留 `excelmanus/skills` 兼容层，仅转发到 `tools`，不再参与主链路自动发现。
- 路由采用 `hint_direct / confident_direct / llm_confirm` 三段策略。
- `allowed_tools` 采用 Loader 软校验 + Engine 硬校验。

## 🚨 风险与问题
- 已解决：API/CLI 行为破坏性变更引发旧测试不兼容。
- 当前阻塞：无。
