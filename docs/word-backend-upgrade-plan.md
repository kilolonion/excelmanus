# Word 后端升级计划（按当前代码现状刷新）

> 刷新日期: 2026-03-06
> 关联主文档: `docs/plans/2026-03-06-word-agent-rollout-plan.md`
> 本文用途: 说明旧“纯后端升级”假设为什么已经过时，以及当前 Word rollout 应如何理解后端范围

---

## 先说结论

这份文档不再代表“Word 还需要先补完一整轮后端内核升级”。

按当前代码检查结果，Word 后端核心链路已经基本具备：

- 已有 `read_word` / `write_word` / `inspect_word` / `search_word`
- 已有 Word API:
  - `GET /api/v1/files/word`
  - `GET /api/v1/files/word/snapshot`
  - `POST /api/v1/files/word/write`
- 已有策略、路由、上下文、验证门、调度器中的 Word 接入
- 已有 `write_word` 后的 checkpoint / `files_changed` 路径
- 已有前端 Word 组件与 `word-store` 雏形

因此，**当前 Word rollout 的主阻塞点已经不是“先把后端补齐”**，而是：

1. 让前端壳层和文件入口真正接入 Word
2. 把 `.doc` 的错误暗示清干净
3. 明确里程碑边界:
   - `M1 = shell parity`
   - `M2 = true persistence`

---

## 当前代码现状

### 已经落地的部分

以下能力已经能在代码中看到，不应再作为“待从零实现”的后端大项重复分发：

- Word 工具集已存在并注册
- `insert_after` 的底层实现已修成正确的 `makeelement(...) + addnext(...)` 形式
- Word 策略层已注册到 `policy.py`
- 路由器已包含 `.docx` 路径提取 / Word 写意图判断
- 上下文构建器已能对 Word 写后给出回读提示
- 验证门已把 `write_word` 视作写操作
- 调度器已包含 `write_word` 摘要与写后检查

### 仍然不一致或未完成的部分

这些才是 rollout 阶段仍需要处理的真实缺口：

1. **前端壳层未真正挂上 Word**
   - `web/src/app/client-layout.tsx` 仍只挂 `ExcelSidePanel`
   - `web/src/app/page.tsx` 与 `web/src/app/chat/[sessionId]/page.tsx` 仍只切 Excel full view / compare view

2. **工作区文件入口仍只把 Excel 当成“可打开面板/全屏”的文件**
   - `web/src/components/sidebar/ExcelFilesBar.tsx` 单击/双击仍只 special-case Excel
   - Word 文件当前更接近“普通下载文件”，不是主路径可达功能

3. **`files_changed` 只驱动 Excel 侧状态**
   - `web/src/lib/sse-event-handler.ts` 在 `files_changed` 分支里只更新 Excel store
   - 当前没有把 Word 活跃文档刷新接上去

4. **Word viewer 目前是“快照渲染器”，不是“已完成持久化编辑器”**
   - `web/src/components/word/UniverDoc.tsx` 通过 `fetchWordSnapshot(...)` 加载文档
   - 前端 API 已有 `writeWordContent(...)`
   - 但当前没有 UI 把编辑事件接到保存调用上
   - 也没有明确的 Save / Reset / dirty / conflict 语义

5. **Word 下载调用参数顺序仍有 bug**
   - `web/src/components/word/WordSidePanel.tsx`
   - `web/src/components/word/WordFullView.tsx`
   - 当前把 `sessionId` 传到了 `filename` 位置

---

## 必须明确的里程碑边界

### M1 = shell parity

`M1` 的定义是：**Word 达到当前 Excel 壳层级别的可达性和刷新能力**，不是“前端编辑已持久化”。

`M1` 应包含：

- 只以 `.docx` 作为支持格式
- 从工作区文件树进入 Word side panel
- 从工作区文件树进入 Word full view
- 正确刷新
- 正确下载
- 后端 AI 写入后通过 `files_changed` 驱动 Word 视图刷新
- UI 文案和交互不暗示“前端打字已保存”

`M1` 不应承诺：

- 前端直接编辑已写回文件
- 自动保存
- 解决并发编辑冲突

### M2 = true persistence

`M2` 才表示：**前端编辑真的可以持久化回 `.docx` 文件**。

`M2` 至少需要：

- 编辑事件采集
- 变更模型设计
- 明确 Save / Reset 交互
- 调用 `/api/v1/files/word/write`
- dirty 状态管理
- 后端 AI 写入与前端本地编辑的冲突策略

**不要把 M2 内容混进 M1 任务描述里。**

---

## `.doc` 的当前问题与统一口径

当前代码里，`.doc` 仍被多处当成“像是支持的”：

- `excelmanus/file_registry.py`
- `excelmanus/tools/word_tools.py` 的扩展名集合
- `web/src/components/ui/file-type-icon.tsx`
- `web/src/lib/file-preview.ts`
- `web/src/components/sidebar/ExcelFilesBar.tsx` 的上传 accept
- `GET /api/v1/files/word` 二进制读取端点
- `excelmanus/skillpacks/system/word_basic/SKILL.md`

但真正依赖 `python-docx` 的核心读写/快照链路仍是 **`.docx` 语义**：

- `GET /api/v1/files/word/snapshot` 只接受 `.docx`
- `write_word` / `read_word` / `inspect_word` / `search_word` 的实际实现与报错文案也都指向 `.docx`

因此，当前 rollout 文档和分发提示必须统一按下面口径执行：

- **M1 / M2 一律按 `.docx` 处理**
- **不要再写“支持 `.doc`”**
- 如果分发 `.doc` 相关清理任务，目标应是“移除错误暗示”或“统一拒绝提示”，而不是默认宣称 `.doc` 已可用

---

## 对旧后端任务列表的处理原则

旧版本把 Word 工作拆成 T1-T12 的“后端升级包”。这个拆法现在已经不适合作为主分发入口。

### 不应再作为默认派发项的内容

以下类型不要再按“主线待办”重复分发，除非发现新的具体 bug：

- 策略层注册 Word 工具
- 路由器识别 `.docx`
- 上下文构建器感知 Word
- 验证门接入 `write_word`
- 调度器接入 `write_word`
- `insert_after` 基础修复

### 当前仍值得派发的后端工作

如果还要派发后端相关工作，重点应收敛为：

1. **W1: backend contract / file-type policy cleanup**
   - 核心不是“从零补后端”
   - 而是把 `.doc` 错误宣传收口成一致的 `.docx` 策略

2. **M2 持久化相关后端配合**
   - 只在明确进入 `M2` 后开启
   - 需要与前端保存模型一起设计

---

## 当前推荐执行顺序

基于当前代码现状，推荐顺序是：

1. `W1` 收口 `.docx` 支持边界
2. `W2` 打通前端壳层与文件树入口
3. `W3` 接上 Word store 与 `files_changed`
4. `W4` 稳定 Word 容器和下载/刷新行为
5. `W6` 做集成验证
6. 只有在 `M1` 稳定后，才决定是否开启 `W5 / M2`

`W7` 的职责是持续刷新这些说明，避免团队继续沿用“后端还没做完”或“前端已支持保存”的旧口径。

---

## 分发时必须保持一致的决策

1. `M1 = shell parity`，不是 persistence
2. `M2 = true persistence`，必须单独立项
3. 当前说明统一按 `.docx` 支持，不按 `.doc` 支持
4. 不要再暗示“前端已完成编辑保存”
5. `files_changed` 是 M1 刷新主路径；不要凭空设计新的 Word SSE 事件作为先决条件

---

## 给后续阅读者的简短指引

如果你正在接手 Word rollout：

- 用 `docs/plans/2026-03-06-word-agent-rollout-plan.md` 作为主执行计划
- 把本文当成“旧后端计划的刷新说明”
- 用 `docs/word-agent-dispatch-prompts.md` 作为当前可复制分发的任务提示词来源

