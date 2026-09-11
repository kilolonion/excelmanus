# DSH Agent 设计对照与改进计划

> **施工顺序已作废。** Agent 循环与提示词工程以 [agent-prompt-full-migration-20260911.md](./agent-prompt-full-migration-20260911.md) 为准（完整 Driver / 注册表 / 流水线，而不是「第一期不做 inbox」）。本文件 §1 机制表仍可参考。

日期：2026-09-10（§2 已按当前工作树更新）  
对照：`/Users/jiangwenxuan/Desktop/excel/dsh-excel` 的 harness（只读）  
对象：当前 ExcelManus 工作树（已收敛八类意图与提示词段序）  
可视化：[dsh-agent-design-comparison.canvas.tsx](/Users/jiangwenxuan/.cursor/projects/Users-jiangwenxuan-Desktop-excelmanus/canvases/dsh-agent-design-comparison.canvas.tsx)

## 立场

吸收 **DSH 的机制**，不搬 **DSH 的框架**。产品继续留在 ExcelManus。模型拥有策略，执行器拥有不变量。不把 P3/P4/P6 标完成，也不写「写入契约已闭环」。

不搬：Cordis、waterfall、complete 段、`toolOrder` 其余项标记、完整 `session/event` 目录、多后端 subagent。

## 1. DSH 实际在做什么

依据 `docs/agent-lifecycle.zh.md`、`docs/tool-execution-pipeline.zh.md`、`docs/subsystems/system-prompt.zh.md`、`docs/subsystems/compaction.zh.md`、`docs/subsystems/plan.zh.md`、`docs/defensive-patterns.zh.md`。

| 机制 | 含义 |
|---|---|
| Turn / Step / Inbox | 用户插话进入 inbox；driver 认领一批后开 turn；每步是 pre-step → 组装提示词 → 模型流 → 工具批；可再认领下一步；自然停止时走 turn-stopping |
| 提示词组装 | 每步 `system-prompt/assemble`。段按 `order`：identity(-100)、persona(0)、工具引导 100–199。动态上下文单独成 user-role 快照，变化或压缩时才记账 |
| 工具流水线 | pre-execute → 单调守卫 → 一次性审批 → execute（超时/重试包一层）→ post-execute → `finalizeContent` → 冻结的 `tools/result` |
| 并行 | 有 barrier 与有界滚动池；启动前重新分类。mutation 不与另一 mutation 并行 |
| Code Mode | `presentAs('code')`。子调用走同一流水线，带父 token，记 `tool/code-dispatch`，子调用不加 additionalContexts |
| 压缩 | 步前 pressure；先剪工具结果再摘要；锁用 start/end；表面替换未推进则不新开重试轮 |
| Plan | 软性协作。激活时多一段 `plan:policy`（order 50），**不改工具目录**。沙箱和审批各自强制 |
| 审批 | 按调用 one-shot；通道缺失或不可答 = 拒绝 |
| 结果 | 一次执行拆成执行环境用的结构化值、给模型的摘要、给 UI 的小型事实 |
| 会话 | `session/event` 可回放；`agent/*` 只做实时协调 |
| 防御 | 超时 / 退出码 / 已提交写入分开报；dispose 等到停稳；监听器异常不能饿死后续 |

DSH **默认不做**：词法任务路由、隐藏 VLM、强制验收 Agent、每步自动扫工作簿、按标签注入补偿策略。

## 2. ExcelManus 现在停在哪

### 已对齐

- 模型面工具目录是八类意图；旧微工具（`read_excel`、`write_cells`、`scan_excel_snapshot` 等）不再注册。
- 八类意图工具在源头返回 `ToolResult`（`value` / `model_text` / `ui_meta`）；分派器、SSE、Code Mode 消费结构化结果，不再靠 `json.loads` 猜。
- 提示词段序按 dsh-excel：`harness:identity` / `deployment:persona` / `spreadsheet:workflow` / `tool:run_code`。
- 图片政策写在 workflow：主模型读图 → WorkbookSpec → `edit_spreadsheet`；无隐藏 VLM、无「将由视觉模型分析」占位。
- 问候不再走 chitchat 短路；纯文本结束本轮，没有 auto-continue。
- AUX 工具路由 LLM、词法 `task_tags` 路由、`route_tool_tags` 过滤已移除。
- 无自动预扫描、无 `explorer_reports` 隐式注入；需要概况时由模型调 `inspect_spreadsheet` / `analyze_spreadsheet`。
- 周期记忆提取已从主循环拿掉。
- 只读工具可并行；写入 / 审批 / `finish_task` 串行。
- 审批按调用；待审批不阻塞无关的新用户消息。

### 部分完成

- 主写入入口已迁 `workbook_commit`；版本冲突后的 rebase、人工/Agent 共编、回滚未验收。
- Code Mode 已生成八类 SDK，`execute_subcall` 进 `execute()`；SSE、取消、共享预算未验收。

### 剩余差距

- `engine.chat()` 仍是单体编排入口：技能匹配、`ContextBuilder` 拼接、`for iteration in range` 工具循环、compaction/trim 耦在同一函数族里；没有 inbox / step 边界 / turn-stopping。
- `engine.py` 体量仍大，是主要结构性债务。
- `ROUTE_START` / `ROUTE_END` 事件类型保留供历史 SSE 回放；CLI/SSE 文案不应再暗示系统已「分析完任务意图」。
- 写后 playbook reflector、部分 ContextBuilder 每步整包注入仍可进一步按 fingerprint 瘦身（见 §4 B/E）。

## 3. 改进经验（机制，不是口号）

1. **Turn 和 Step 分开。** 用户插话、模型步、工具批是三件不同的事。现在路由和扫描挂在 chat 入口上。
2. **首轮不要替模型探查。** 自动扫表、自动 explorer_reports、路由打开 xlsx，等于藏了一轮只读 Agent。
3. **动态上下文按变化记账。** 已有 fingerprint，却仍每步整包注入。变了才写，前缀才能稳。
4. **字段细节在 schema。** 全局段只写跨工具政策。提示词里不要再教旧工具名。
5. **一次执行，三路投影。** Code Mode 留 canonical；模型看有界摘要；UI 只拿版本/范围/警告。
6. **流水线是顺序。** 策略 → 一次性审批 → 执行 → finalize → 冻结结果。不要为了像 DSH 而做事件总线。
7. **写入事实和调用成败正交。** 后半失败不能说文件没变。超时、退出、已提交 revision 分开报。
8. **压缩在步前，且可检测。** 先剪枝再摘要；失败可见；表面没推进不开重试。
9. **审批按调用，计划按段。** 待确认不锁死会话。Plan 是 `plan:policy`，不改目录，不加标签策略。
10. **附属 LLM 必须具名。** 标题、手动 compact 可以留。记忆维护、路由 embedding、隐藏 VLM 移出默认路径。

## 4. 计划

按杠杆排序。A 不改循环骨架也能做。

### A. 拆掉首轮补偿

- 路由不再打开工作簿、不再建文件结构上下文。`sheet_count` 不再作为策略条件。
- 删除自动预扫描和 `explorer_reports` 隐式注入。需要概况时由模型调 `inspect_spreadsheet` / `analyze_spreadsheet`。
- 默认关闭语义记忆 / playbook / 技能提示注入；保留用户显式 `memory_*` 与 `/skill`。
- 无视觉且无用户显式 `use_aux_vlm`：拒绝图片，不要占位「将由视觉模型分析」。
- 审批改为针对当前调用；有 pending 时不要挡住无关的下一条用户消息（或只挡住同文件 mutation）。
- 关掉 chat 结束后的 playbook reflector；会话标题可以留。
- 删 `_auto_explore_after_scan` 与未再调用的 `_classify_tool_route_llm`。
- 进度事件可以留，但不要暗示系统已经「分析完意图」。

验收：同一条「看一下 sales.xlsx」首轮模型请求前，不再读 xlsx、不做 embedding、不注入 explorer 报告。

### B. 给 Step 明确边界

- 把 `for iteration` 当成 step：组装 → 模型 → 工具批 → 停或下一步。
- 动态上下文只在 fingerprint 变化时作为独立消息追加（已有快照逻辑）。
- `chat_mode=plan` 只多注入一段 `plan:policy`（order 50 一带），不改工具目录，不按 `plan_worthy` 分流。
- 删掉每会话仍生成、身份段已不再引用的能力图谱替换。
- 只读可并行，mutation 串行；与 Code Mode 子调用同一条规则。

验收：连续两步文件全景未变时，第二步请求不再重复那一大段 panorama。

### C. 真正消费三路结果

- 八类意图返回 `ToolResult`，不再让调用方 `json.loads` 猜。
- dispatcher / SSE / Code Mode 分别取 `value` / `model_text` / `ui_meta`。
- 已提交 revision 出现在 ui_meta 和 model_text；后续步骤失败不得抹掉。

验收：一次 `edit_spreadsheet` 成功后，脚本抛错，会话仍能读到 `content_version` 和路径。

### D. 继续 P3 / P4（进行中）

- `VERSION_CONFLICT` 后 inspect 再 rebase，不重放旧 operations。
- 人工编辑与 Agent 共用同一提交路径；冲突保留草稿。
- Code Mode：子调用事件、取消拒绝后续子调用、共享预算。不宣称沙盒已隔离，除非 Docker 真开着。

验收：现有写入/Code Mode 小批量 + 人工共编手工路径。不标完成。

### E. 压缩改到步前

- 在模型请求前判断 pressure，不在 iteration 中途吞异常。
- 先剪过长 tool result，再摘要。
- 摘要没替换 surface 就不要当成功，也不要因此重跑失败步。

### F. 工作台（依赖 D）

- 编辑状态栏、检查器、差异网格。版本字段来自提交契约，不来自 UI 快照。

## 5. 不在本计划内

- 迁往 DSH 或改 dsh-excel-plugin。
- 重写会话存储为完整事件日志。
- 恢复按 `task_tags` 注入的策略文件。
- 把旧微工具重新暴露给模型。
- 用更多正则/LLM 分类器替换已删除的路由。

## 6. 建议的下手顺序

先做 A（首轮补偿），再做 C 里意图工具的返回值（和现有测试面重叠小、收益直接），P3/P4 按冲突出现继续修。B 的 fingerprint 注入可以跟 A 同一批做完 panorama / explorer 删除后自然变小。E、F 不要插到 A 前面。
