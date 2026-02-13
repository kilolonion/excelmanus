# Agent 任务清单工具设计研究：如何让 AI 积极调用 Todo/Task 工具

> **调研日期**：2026-02-13
> **调研对象**：Claude Code（TodoWrite → Tasks API）、OpenAI Codex、Windsurf Cascade
> **核心问题**：这些 Agent 通过什么机制让 AI 主动、频繁地使用任务清单工具？

---

## 一、三大 Agent 的任务清单设计对比

| 维度 | Claude Code (TodoWrite) | Claude Code (Tasks API v2.1.16+) | Windsurf Cascade | OpenAI Codex |
|------|------------------------|----------------------------------|-------------------|--------------|
| **工具形态** | TodoRead + TodoWrite（内存） | TaskCreate + TaskList + TaskGet + TaskUpdate（磁盘持久化） | todo_list（内存） | AGENT_TASKS.md（文件） |
| **持久化** | 会话内存，/compact 后丢失 | ~/.claude/tasks/ 磁盘文件，跨会话 | 会话内存 | 文件系统，天然持久 |
| **状态模型** | pending / in_progress / completed | pending / in_progress / completed / failed | pending / in_progress / completed | Markdown checkbox |
| **依赖追踪** | ❌ | ✅ blocks / blockedBy | ❌ | ❌（靠文本描述） |
| **跨会话协作** | ❌ | ✅ CLAUDE_CODE_TASK_LIST_ID 环境变量 | ❌ | ✅（文件天然共享） |

---

## 二、Claude Code 的核心策略（最完善的参考）

Claude Code 是目前公开可见的**最积极引导 AI 使用任务清单**的 Agent。它通过 **五层机制** 驱动调用：

### 2.1 工具描述层：在 Tool Description 中反复强调

**TodoRead 工具描述**中直接列举了 6 种"应该主动调用"的场景：

```
Use this tool to read the current to-do list for the session.
This tool should be used proactively and frequently to ensure that
you are aware of the status of the current task list.

You should make use of this tool as often as possible, especially:
- At the beginning of conversations to see what's pending
- Before starting new tasks to prioritize work
- When the user asks about previous tasks or plans
- Whenever you're uncertain about what to do next
- After completing tasks to update your understanding of remaining work
- After every few messages to ensure you're on track
```

**关键词**：`proactively`、`frequently`、`as often as possible`、`after every few messages`

**TodoWrite 工具描述**中明确区分了"何时用"和"何时不用"：

```
## When to Use This Tool
Use this tool proactively in these scenarios:
1. Complex multi-step tasks - 3+ distinct steps
2. Non-trivial and complex tasks
3. User explicitly requests todo list
4. User provides multiple tasks
5. After receiving new instructions - Immediately capture
6. When you start working on a task - Mark in_progress BEFORE beginning
7. After completing a task - Mark completed + add follow-up tasks

## When NOT to Use This Tool
1. Single, straightforward task
2. Trivial task with no organizational benefit
3. Less than 3 trivial steps
4. Purely conversational or informational
```

**设计要点**：
- **正面引导 + 反面排除**，让模型清楚边界
- 强调 `BEFORE beginning work`（先标记再干活，而非干完再标记）
- 最后兜底：`When in doubt, use this tool.`

### 2.2 系统提示层：Task Management 专节

Claude Code 在系统提示中有独立的 `# Task Management` 章节：

```
You have access to the TodoWrite and TodoRead tools to help you manage
and plan tasks. Use these tools VERY frequently to ensure that you are
tracking your tasks and giving the user visibility into your progress.

These tools are also EXTREMELY helpful for planning tasks, and for
breaking down larger complex tasks into smaller steps. If you do not
use this tool when planning, you may forget to do important tasks
- and that is unacceptable.

It is critical that you mark todos as completed as soon as you are
done with a task. Do not batch up multiple tasks before marking
them as completed.
```

**关键手法**：
- **VERY frequently** — 大写强调频率
- **EXTREMELY helpful** — 大写强调价值
- **"you may forget... and that is unacceptable"** — 用后果恐惧驱动行为
- **"Do not batch"** — 禁止批量完成，要求实时更新

### 2.3 工作流程层：Doing Tasks 中内嵌 Todo 步骤

在 `# Doing tasks` 章节，把 Todo 作为工作流第一步：

```
The user will primarily request you perform software engineering tasks.
For these tasks the following steps are recommended:
1. Use the TodoWrite tool to plan the task if required  ← 第一步！
2. Use the available search tools to understand the codebase
3. Implement the solution using all tools available
4. Verify the solution if possible with tests
5. Run lint and typecheck commands
```

**设计要点**：将 Todo 工具嵌入标准工作流，而非作为可选项。

### 2.4 Few-shot 示例层：提供完整的调用范式

系统提示中包含 **2 个完整的 Few-shot 示例**，展示理想的 Todo 调用模式：

**示例1（错误修复场景）**：
```
user: Run the build and fix any type errors
assistant: I'm going to use the TodoWrite tool to write items to the todo list:
- Run the build
- Fix any type errors

I'm now going to run the build using Bash.

Looks like I found 10 type errors. I'm going to use the TodoWrite tool
to write 10 items to the todo list.

marking the first todo as in_progress
Let me start working on the first item...
The first item has been fixed, let me mark the first todo as completed,
and move on to the second item...
```

**示例2（新功能场景）**：
```
user: Help me write a usage metrics tracking feature
assistant: I'll help you implement this. Let me first use the TodoWrite
tool to plan this task.
Adding the following todos:
1. Research existing metrics tracking in the codebase
2. Design the metrics collection system
3. Implement core metrics tracking functionality
4. Create export functionality for different formats

Let me start by researching the existing codebase...
[continues implementing step by step, marking todos as they go]
```

**设计要点**：
- 示例展示了**动态拆解**（发现10个错误后追加10个todo）
- 示例展示了**实时标记**（每完成一个立刻标记）
- 示例展示了**先规划后执行**的模式

### 2.5 状态管理规则层：严格的完成性检查

```
Task Completion Requirements:
- ONLY mark a task as completed when you have FULLY accomplished it
- If you encounter errors, blockers, keep the task as in_progress
- When blocked, create a new task describing what needs to be resolved
- Never mark as completed if:
  - Tests are failing
  - Implementation is partial
  - You encountered unresolved errors
  - You couldn't find necessary files or dependencies

Task Management:
- Only have ONE task in_progress at any time
- Complete current tasks before starting new ones
- Remove tasks that are no longer relevant entirely
```

### 2.6 全局兜底层：系统提示末尾的最终强调

在系统提示的**最末尾**（最后被模型看到的位置），Claude Code 放置了：

```
IMPORTANT: Always use the TodoWrite tool to plan and track tasks
throughout the conversation.
```

**这是 recency bias 的巧妙利用**——放在最后的指令对模型的影响最大。

---

## 三、Claude Code Tasks API（v2.1.16+ 新版演进）

2026年1月，Claude Code 将 TodoWrite 升级为 Tasks API：

### 3.1 演进动机

- **Opus 4.5 自治能力增强**：简单任务不再需要显式 Todo 追踪
- **多会话协作需求**：Subagent 之间需要共享任务状态
- **依赖追踪**：真实项目有任务间依赖关系

### 3.2 新 API 设计

| 工具 | 用途 |
|------|------|
| **TaskCreate** | 创建任务（含 subject / description / activeForm） |
| **TaskList** | 列出所有任务（轻量，不含 description） |
| **TaskGet** | 获取单个任务完整信息 |
| **TaskUpdate** | 更新状态、设置依赖（blocks/blockedBy）、删除 |

### 3.3 关键创新：activeForm 字段

```
- subject: "Run tests"           ← 祈使句（做什么）
- activeForm: "Running tests"    ← 进行时（正在做什么，显示给用户）
```

这让用户能在 UI 上实时看到"AI 正在做什么"的自然语言描述。

### 3.4 文件系统持久化

```
~/.claude/tasks/<TASK_LIST_ID>/
├── task_001.json
├── task_002.json
└── ...
```

通过 `CLAUDE_CODE_TASK_LIST_ID` 环境变量，多个 Claude 会话可共享同一个任务列表。

---

## 四、OpenAI Codex 的方式：文件即任务

Codex 不提供专门的 Todo 工具，而是通过**文件系统**管理任务：

### 4.1 AGENT_TASKS.md 模式

Project Manager Agent 创建 `AGENT_TASKS.md` 文件：
```markdown
# Agent Tasks

## Frontend
- [ ] Implement login page
- [ ] Add form validation
- [x] Setup routing

## Backend
- [ ] Create API endpoints
- [ ] Setup database schema
```

### 4.2 AGENTS.md 指令驱动

在 `AGENTS.md` 中写明任务管理规范：
```markdown
## Task Management
- Always read AGENT_TASKS.md before starting work
- Update task status after completing each item
- Create TEST.md for test plans
```

### 4.3 优缺点

- **优点**：天然持久化、易于版本控制、多 Agent 共享
- **缺点**：没有结构化状态追踪、依赖 Agent 自律更新文件

---

## 五、Windsurf Cascade 的方式

Windsurf 的 todo_list 工具设计相对简洁：

### 5.1 系统提示中的引导

```
Use update_plan to manage work. Limit plans to concise steps which
you execute one at a time, mark them as done as soon as you complete
them, and update them when new information arrives.
```

### 5.2 工具描述

```
Use this tool to create, update, or manage a todo list. This tool
helps you organize tasks with different statuses and priorities.
```

### 5.3 规划模式

Windsurf 有独立的 Planning Agent，在后台持续优化长期计划，主模型聚焦短期执行。

---

## 六、关键设计模式总结

### 模式1：多层次重复强调（Claude Code 核心策略）

```
工具描述 → 系统提示专节 → 工作流步骤 → Few-shot 示例 → 末尾兜底
     ↑                                                    ↑
  "proactively"                              "IMPORTANT: Always use"
  "frequently"                               (利用 recency bias)
  "as often as possible"
```

**原理**：LLM 对指令的遵从度与**重复次数**和**位置**正相关。Claude Code 在系统提示的 5 个不同位置反复强调 Todo 工具的使用。

### 模式2：恐惧驱动（Fear-Driven）

```
"If you do not use this tool when planning, you may forget to do
important tasks - and that is unacceptable."
```

用**负面后果**警告来增强行为动机。

### 模式3：正负边界定义（Positive/Negative Boundary）

```
When to Use:        3+ steps, complex, user provides multiple tasks...
When NOT to Use:    single trivial task, conversational, <3 steps...
```

明确告诉模型**什么时候用、什么时候不用**，避免过度调用（每个小问题都创建 Todo）或不足调用。

### 模式4：Few-shot 示范（Behavioral Anchoring）

提供 2-3 个完整的对话范式，展示：
- 先规划后执行
- 动态追加任务
- 实时标记状态
- 一次只做一个

### 模式5：实时性要求（Real-time Update）

```
"Mark tasks complete IMMEDIATELY after finishing"
"Do not batch up multiple tasks"
"Only have ONE task in_progress at any time"
```

强制要求实时更新，而非事后补记。

### 模式6：末尾锚定（Recency Anchoring）

将最重要的指令放在系统提示**最末尾**，利用 LLM 的 recency bias（近期偏好）确保被遵循。

---

## 七、对 ExcelManus 的借鉴建议

### 7.1 当前问题

ExcelManus 的系统提示中缺乏对任务清单工具的强引导。如果要让 AI 积极使用任务管理，需要在以下层面加强：

### 7.2 建议实施方案

#### 层级1：工具描述增强

为任务清单工具添加详细的使用指引，包含：
- `proactively` / `frequently` 等频率引导词
- 6+ 种"应该主动调用"的场景列举
- "When NOT to Use" 反面排除（避免过度调用）
- `When in doubt, use this tool.` 兜底

#### 层级2：系统提示专节

添加独立的 `# 任务管理` 章节：
- 强调 "VERY frequently" 使用
- 用恐惧驱动："如果不规划，你可能会遗漏重要步骤"
- 禁止批量完成
- 限制同时只有一个 in_progress

#### 层级3：工作流嵌入

在标准工作流中将任务清单作为**第一步**：
```
1. 使用任务清单工具规划步骤
2. 探索数据/文件
3. 执行操作
4. 验证结果
```

#### 层级4：Few-shot 示例

提供 2-3 个 Excel 操作场景的完整示例：
- 简单场景（读取分析 → 不使用 Todo）
- 复杂场景（多步骤数据处理 → 使用 Todo）
- 动态场景（发现新问题后追加 Todo）

#### 层级5：末尾锚定

在系统提示最后添加：
```
重要：在复杂任务中始终使用任务清单工具来规划和追踪进度。
```

### 7.3 效果预期

参考 Claude Code 的用户反馈，加入任务清单强引导后：
- **任务完成率提升**：AI 不再遗漏多步骤任务中的某些步骤
- **用户体验提升**：用户能实时看到 AI 的进度和计划
- **可审计性增强**：任务清单成为执行记录，便于回溯

---

## 八、参考资料

1. [Claude Code System Prompts (Piebald-AI)](https://github.com/Piebald-AI/claude-code-system-prompts) — 完整系统提示提取
2. [Claude Code TodoWrite Tool Description](https://gist.github.com/wong2/e0f34aac66caf890a332f7b6f9e2ba8f) — Wong2 提取的工具描述
3. [What are Tasks in Claude Code (ClaudeLog)](https://claudelog.com/faqs/what-are-tasks-in-claude-code/) — Tasks API 演进
4. [Tasks API vs TodoWrite (DeepWiki)](https://deepwiki.com/FlorianBruniaux/claude-code-ultimate-guide/7.1-tasks-api-vs-todowrite) — 对比分析
5. [Codex Agents SDK Guide](https://developers.openai.com/codex/guides/agents-sdk/) — Codex 多 Agent 任务管理
6. [Windsurf Cascade Docs](https://docs.windsurf.com/windsurf/cascade/cascade) — Windsurf 规划能力
