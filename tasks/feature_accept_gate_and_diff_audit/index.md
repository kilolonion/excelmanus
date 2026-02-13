# 任务：全局 Accept 门禁 + Diff 审计 + Undo 回滚

> **类型**：feature  
> **优先级**：P1  
> **负责人**：AreaSongWcc  
> **状态**：✅ 已完成  
> **开始时间**：2026-02-13  
> **预计完成**：2026-02-13

## 🎯 目标
实现高风险写操作统一 `accept` 门禁，支持 `reject/undo`，并为每次执行落盘 diff 与审计记录，`/fullAccess` 可旁路确认。

## 📊 进度仪表盘
| 阶段 | 状态 | 文档链接 |
|------|------|----------|
| R1 调研 | ✅ | 本文件 |
| I 设计 | ✅ | 本文件 |
| P 规划 | ✅ | [P_plan.md](./P_plan.md) |
| E 执行 | ✅ | [E_execution.md](./E_execution.md) |
| R2 验收 | ✅ | [R2_review.md](./R2_review.md) |

## 📝 关键决策
- 覆盖全部高风险写工具，单队列待确认。
- 二进制文件使用快照回滚，文本文件使用 unified diff。
- `run_python_script` 需要 accept 与审计，但默认不自动回滚副作用。

## 🚨 风险与问题
- `run_python_script` 可能修改未知路径，已按决策仅提供审计不提供自动回滚。
