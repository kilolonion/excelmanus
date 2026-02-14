# 任务：核心引擎层休整（深度验证版）

> **类型**：refactor
> **优先级**：P1
> **负责人**：AreaSongWcc
> **状态**：✅ 已完成
> **开始时间**：2026-02-14
> **预计完成**：2026-02-14

## 🎯 目标
落实核心引擎层休整方案：模型切换一致性、system_message_mode 新版对齐、上下文硬约束、工具结果全链路硬截断、事件协议稳定化、测试补齐。

## 📊 进度仪表盘
| 阶段 | 状态 | 文档链接 |
|------|------|----------|
| R1 调研 | ✅ | [index.md](./index.md) |
| I 设计 | ✅ | [P_plan.md](./P_plan.md) |
| P 规划 | ✅ | [P_plan.md](./P_plan.md) |
| E 执行 | ✅ | [E_execution.md](./E_execution.md) |
| R2 验收 | ✅ | [R2_review.md](./R2_review.md) |

## 📝 关键决策
- `router_model` 未显式配置时，路由模型跟随 `/model` 切换。
- `system_message_mode` 标准化为 `auto|merge|replace`，兼容 `multi -> replace`。
- 上下文预算以“最终发送消息”计算并强制裁剪。
- 追加全局工具结果硬截断，防止 `max_result_chars=0` 绕过。

## 🚨 风险与问题
- 已通过兼容映射和新增回归测试消化主要风险。
- 当前仅保留环境级风险：本地 MCP 启动器异常会影响 API 并发类测试，测试桩已隔离该不稳定因子。
