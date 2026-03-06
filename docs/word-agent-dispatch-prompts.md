# Word Agent 分发提示词（按当前 rollout 状态刷新）

> 用途: 为当前 Word rollout 继续分发工作流
> 基准里程碑:
> - `M1 = shell parity`
> - `M2 = true persistence`
> 关联文档:
> - `docs/plans/2026-03-06-word-agent-rollout-plan.md`
> - `docs/word-backend-upgrade-plan.md`

---

## 分发前统一口径

开始任何子任务前，先让执行者知道这 5 点：

1. 当前主目标是 `M1`，不是 `M2`
2. `M1` 只要求 Word 达到当前 Excel 壳层级别的可达性、刷新和下载体验
3. `M2` 才是“前端编辑真正持久化”
4. 当前说明统一按 `.docx` 支持，不要继续暗示 `.doc` 已完成支持
5. 不要把“Univer 文档可编辑”误写成“前端保存已完成”

所有执行者都应按下面格式回报：

```text
Workstream:
Files changed:
Verification run:
Result:
Open questions:
Risks introduced:
```

---

## 现状摘要

按当前代码检查结果：

- 后端 Word 工具、路由、策略、验证门、调度器主链路已基本存在
- 前端有 `UniverDoc` / `WordSidePanel` / `WordFullView` / `word-store`
- 但主壳层、页面切换、文件树入口、`files_changed` 到 Word store 仍未真正接好
- Word 下载调用参数顺序仍有 bug
- 前端 API 已有 `writeWordContent(...)`，但当前 UI 没有把编辑事件接到持久化
- `.doc` 仍被多处错误宣传，实际 rollout 应按 `.docx` 处理

这意味着：

- **不要再按旧版 T1-T12“后端大升级”模板继续派工**
- **优先派 W1-W4/W6**
- **W5 只有在明确批准 M2 后再派**

---

## 批次建议

### 批次 A：可并行启动

- `W1` backend contract / file-type policy cleanup
- `W2` frontend shell entry wiring
- `W7` docs / dispatch refresh

### 批次 B：依赖前端入口基本完成

- `W3` Word store + `files_changed` integration
- `W4` Word container stabilization

### 批次 C：仅在明确进入 M2 时开启

- `W5` true persistence bridge

### 批次 D：最后执行

- `W6` verification and release gating

---

## 对话 1: W1 — Backend Contract 与 `.docx` 策略收口

```text
你负责 Word rollout 的 W1：backend contract cleanup 与 file-type policy 收口。

先阅读：
- docs/plans/2026-03-06-word-agent-rollout-plan.md
- docs/word-backend-upgrade-plan.md

任务目标：
- 让 Word 后端与文件类型口径统一按 `.docx`
- 去掉或收口 `.doc` 的错误暗示
- 不要重做已经存在的 Word 工具/策略/路由/验证门/调度器主链路

你应重点检查：
- excelmanus/api.py
- excelmanus/file_registry.py
- excelmanus/tools/word_tools.py
- 任何仍在把 `.doc` 当成“像是已支持”的后端入口

完成标准：
- `GET /api/v1/files/word`
- `GET /api/v1/files/word/snapshot`
- `POST /api/v1/files/word/write`
- FileRegistry 与工具文案
在支持边界上不再自相矛盾

关键约束：
- 目标是明确 `.docx` only，或在边界处一致拒绝 `.doc`
- 不要顺手扩 scope 去做前端壳层或 persistence
- 不要把 `.doc` 继续写成已支持格式

回报格式：
Workstream:
Files changed:
Verification run:
Result:
Open questions:
Risks introduced:
```

---

## 对话 2: W2 — Frontend Shell Entry Wiring

```text
你负责 Word rollout 的 W2：前端壳层入口接线。

先阅读：
- docs/plans/2026-03-06-word-agent-rollout-plan.md
- docs/word-backend-upgrade-plan.md

任务目标：
- 让 `.docx` 从正常工作区导航路径进入 Word UI
- 保持 Excel 现有路径不回归

你应重点检查：
- web/src/components/sidebar/ExcelFilesBar.tsx
- web/src/app/client-layout.tsx
- web/src/app/page.tsx
- web/src/app/chat/[sessionId]/page.tsx
- web/src/components/ui/file-type-icon.tsx
- web/src/lib/file-preview.ts

完成标准：
- 单击 `.docx` 可打开 Word side panel
- 双击 `.docx` 可打开 Word full view
- 主壳层真正挂上 `WordSidePanel`
- 页面切换能渲染 `WordFullView`
- 文件类型 helper 统一按 `.docx` 口径，不继续暗示 `.doc` 已完成支持

关键约束：
- `M1 = shell parity`
- 不要把 Word 状态硬塞进 Excel store
- 不要在这个任务里承诺前端编辑保存

回报格式：
Workstream:
Files changed:
Verification run:
Result:
Open questions:
Risks introduced:
```

---

## 对话 3: W3 — Word Store 与 `files_changed` Integration

```text
你负责 Word rollout 的 W3：Word store / SSE / affected-file 集成。

先阅读：
- docs/plans/2026-03-06-word-agent-rollout-plan.md
- docs/word-backend-upgrade-plan.md

任务目标：
- 让 Word 活跃文档能响应通用 `files_changed`
- 让 M1 的刷新链路成立

你应重点检查：
- web/src/stores/word-store.ts
- web/src/lib/sse-event-handler.ts
- 必要时相关共享类型文件

完成标准：
- `files_changed` 能驱动当前 Word 文档刷新
- Word 活跃文档 / full view 路径与 refresh state 明确
- 不凭空发明新的 Word SSE 事件类型作为 M1 前提

关键约束：
- M1 优先走现有 `files_changed`
- 不要把“有 refresh”写成“有 persistence”
- Excel 现有 SSE 行为不能被破坏

回报格式：
Workstream:
Files changed:
Verification run:
Result:
Open questions:
Risks introduced:
```

---

## 对话 4: W4 — Word Container Stabilization

```text
你负责 Word rollout 的 W4：Word 容器稳定化。

先阅读：
- docs/plans/2026-03-06-word-agent-rollout-plan.md
- docs/word-backend-upgrade-plan.md

任务目标：
- 修正 Word side/full view 的下载与刷新体验
- 把 `UniverDoc` 明确定义为 M1 阶段的 snapshot-driven renderer
- 去掉任何“看起来像已经保存”的误导

你应重点检查：
- web/src/components/word/UniverDoc.tsx
- web/src/components/word/WordSidePanel.tsx
- web/src/components/word/WordFullView.tsx
- web/src/lib/api.ts

完成标准：
- Word 下载调用参数正确
- 刷新行为稳定
- 加载/错误状态清晰
- M1 阶段不暗示前端编辑已落盘

关键约束：
- 不要把 `onContentEdit` 的存在等同于 persistence 已完成
- 如果要进入保存语义，必须升级为 W5 / M2 任务

回报格式：
Workstream:
Files changed:
Verification run:
Result:
Open questions:
Risks introduced:
```

---

## 对话 5: W5 — Optional True Persistence Bridge

```text
你负责 Word rollout 的 W5：true persistence bridge。

只有在 hub 明确批准 `M2` 后才能开始。

先阅读：
- docs/plans/2026-03-06-word-agent-rollout-plan.md
- docs/word-backend-upgrade-plan.md

任务目标：
- 让前端编辑真正写回 `.docx`
- 明确保存模型，而不是做“看起来能改”的半成品

你应重点检查：
- web/src/components/word/UniverDoc.tsx
- web/src/lib/api.ts
- 相关后端 `/api/v1/files/word/write` 契约

开始编码前必须先明确：
- 保存触发方式：显式 Save 还是别的
- 变更模型：段落级还是更细粒度
- dirty 状态怎么表示
- 冲突怎么处理

完成标准：
- 前端有清晰的保存语义
- UI 调用 `writeWordContent(...)`
- reload 后能确认内容已持久化

关键约束：
- 不要把 W5 混入 M1
- 第一版优先显式 Save / Reset，不要直接做隐式自动保存

回报格式：
Workstream:
Files changed:
Verification run:
Result:
Open questions:
Risks introduced:
```

---

## 对话 6: W6 — Verification 与 Release Gating

```text
你负责 Word rollout 的 W6：verification / release gating。

先阅读：
- docs/plans/2026-03-06-word-agent-rollout-plan.md
- docs/word-backend-upgrade-plan.md

任务目标：
- 按当前 M1 边界做真实的集成验收
- 明确哪些结论是已验证，哪些只是代码推断

你应覆盖：
- 目标 Python 测试
- frontend build
- 关键手工路径:
  - `.docx` 出现在工作区
  - 单击打开 side panel
  - 双击打开 full view
  - refresh 生效
  - download 生效
  - backend 写入后 `files_changed` 能触发 Word 刷新
  - Excel 路径不回归

关键约束：
- 不要把 repo-wide lint 当成当前 M1 唯一放行门槛，除非 hub 明确要求
- 如果没有运行某项验证，必须明确写未运行

回报格式：
Workstream:
Files changed:
Verification run:
Result:
Open questions:
Risks introduced:
```

---

## 对话 7: W7 — 后续文档刷新

```text
你负责 Word rollout 的 W7：文档与分发提示刷新。

先阅读：
- docs/plans/2026-03-06-word-agent-rollout-plan.md
- docs/word-backend-upgrade-plan.md
- docs/word-agent-dispatch-prompts.md

任务目标：
- 让旧 Word 文档与当前代码现状一致
- 明确 M1 = shell parity，M2 = true persistence
- 去掉对 `.doc` 和“前端已完成编辑保存”的错误暗示
- 产出后续可继续分发的最新说明

关键约束：
- 只改文档，不改代码
- 不要复制旧版 T1-T12 后端派工模板
- 以当前代码现状为准，不以上一版假设为准

回报格式：
Workstream:
Files changed:
Verification run:
Result:
Open questions:
Risks introduced:
```

---

## Hub 使用提醒

在汇总多位执行者结果时，保持这 4 条一致：

1. `M1 = shell parity`
2. `M2 = true persistence`
3. 当前统一按 `.docx` 口径推进
4. 没有真正接上 `writeWordContent(...)` 之前，不要说“前端保存已完成”

