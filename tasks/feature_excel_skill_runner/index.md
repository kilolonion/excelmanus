# 任务：Excel 大文件可执行 Skill

> **类型**：feature
> **优先级**：P1
> **负责人**：AreaSongWcc
> **状态**：✅ 已完成
> **开始时间**：2026-02-12
> **预计完成**：2026-02-12
> **实际完成**：2026-02-12

## 🎯 目标
设计并落地一个新 skill，让 AI 能在大文件 Excel 场景下采用“先轻量探查、再生成脚本并执行”的方式完成任务，避免全量加载文件内容到上下文。

## 📊 进度仪表盘
| 阶段 | 状态 | 文档链接 |
|------|------|----------|
| R1 调研 | ✅ | 本文件 |
| I 设计 | ✅ | 本文件 |
| P 规划 | ✅ | [P_plan.md](./P_plan.md) |
| E 执行 | ✅ | [E_execution.md](./E_execution.md) |
| R2 验收 | ✅ | [R2_review.md](./R2_review.md) |

## 📝 关键决策
- 使用 `openpyxl` 的 `read_only=True` 做结构探查与抽样，避免一次性加载整表。
- 使用可复用脚本执行器运行 AI 生成的 Python 代码，输出到文件而不是回填全部原始数据。
- 通过 `SKILL.md` 强制流程：先 profile，再编码，再执行，再验证。
- 为实现“内置 Skill”目标，新增 `code_tools` 并在 `excelmanus/skillpacks/system` 增加 `excel_code_runner`。

## 🚨 风险与问题
- 风险：错误脚本可能读取过大范围导致内存压力。
- 应对：已在 skill 中固化“先 profile，再按需读取，再执行器运行”的流程。
