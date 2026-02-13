# 需求文档：Agent Task List

## 简介

为 ExcelManus Agent 添加任务清单（Task List）功能，使 Agent 在处理复杂 Excel 任务时能够自动将任务拆解为子任务清单，实时追踪每个子任务的执行状态，并在 CLI 和 API 端展示进度。该功能参考 Claude Code 的 Task 系统设计理念，但遵循 KISS/YAGNI 原则，仅实现当前所需的核心能力。

## 术语表

- **TaskList**：任务清单，包含一组有序的 TaskItem，代表一次复杂操作的完整执行计划
- **TaskItem**：任务项，TaskList 中的单个子任务，具有标题、状态和可选的结果描述
- **TaskStatus**：任务状态枚举，包含 pending（待执行）、in_progress（执行中）、completed（已完成）、failed（失败）四种状态
- **AgentEngine**：ExcelManus 核心代理引擎，驱动 LLM 与工具之间的 Tool Calling 循环
- **ToolRegistry**：工具注册中心，管理工具定义、schema 输出与调用执行
- **EventCallback**：事件回调函数类型，接收 ToolCallEvent 并由 StreamRenderer 消费渲染
- **StreamRenderer**：流式事件渲染器，将 AgentEngine 事件渲染为 Rich 终端组件

## 需求

### 需求 1：任务清单数据模型

**用户故事：** 作为开发者，我希望有一个结构清晰的任务清单数据模型，以便 Agent 能够创建和管理子任务。

#### 验收标准

1. THE TaskList SHALL contain an ordered list of TaskItem instances, a creation timestamp, and a human-readable title
2. THE TaskItem SHALL contain a title, a TaskStatus, and an optional result description
3. THE TaskStatus SHALL support exactly four states: pending, in_progress, completed, and failed
4. WHEN a new TaskList is created, THE TaskList SHALL initialize all TaskItem instances with pending status
5. WHEN a TaskItem status transitions, THE TaskItem SHALL only allow valid transitions: pending → in_progress, in_progress → completed, in_progress → failed
6. THE TaskList SHALL provide a progress summary containing the count of items in each status

### 需求 2：任务清单工具注册

**用户故事：** 作为 Agent，我希望通过 Tool Calling 机制创建和更新任务清单，以便在执行复杂任务时自主管理进度。

#### 验收标准

1. THE ToolRegistry SHALL register a task_create tool that accepts a title and a list of subtask titles, and returns the created TaskList
2. THE ToolRegistry SHALL register a task_update tool that accepts a task index and a new status, and updates the corresponding TaskItem
3. WHEN the task_create tool is called, THE ToolRegistry SHALL create a new TaskList and associate it with the current conversation
4. WHEN the task_update tool is called with an invalid task index, THE task_update tool SHALL return a descriptive error message
5. WHEN the task_update tool is called with an invalid status transition, THE task_update tool SHALL return a descriptive error message
6. THE task_create tool and task_update tool SHALL conform to the existing ToolDef schema format used by ToolRegistry

### 需求 3：任务清单事件集成

**用户故事：** 作为开发者，我希望任务清单的状态变更能通过现有事件系统传播，以便 CLI 和 API 端能实时感知进度。

#### 验收标准

1. WHEN a TaskList is created, THE AgentEngine SHALL emit a TASK_LIST_CREATED event containing the full TaskList data
2. WHEN a TaskItem status changes, THE AgentEngine SHALL emit a TASK_ITEM_UPDATED event containing the task index, new status, and optional result
3. THE EventType enum SHALL include TASK_LIST_CREATED and TASK_ITEM_UPDATED event types
4. THE ToolCallEvent SHALL carry task-related fields for task list events
5. WHEN a task event is emitted, THE event SHALL include a timestamp consistent with existing event conventions

### 需求 4：CLI 任务进度渲染

**用户故事：** 作为用户，我希望在 CLI 终端中看到任务清单的实时进度，以便了解 Agent 当前的执行状态。

#### 验收标准

1. WHEN a TASK_LIST_CREATED event is received, THE StreamRenderer SHALL display the task list title and all subtask items with pending status indicators
2. WHEN a TASK_ITEM_UPDATED event is received, THE StreamRenderer SHALL update the corresponding task item display with the new status indicator
3. THE StreamRenderer SHALL use distinct visual indicators for each TaskStatus: pending (⬜), in_progress (🔄), completed (✅), failed (❌)
4. WHEN all tasks in a TaskList are completed or failed, THE StreamRenderer SHALL display a summary line showing the final counts
5. WHILE the terminal width is less than 60 characters, THE StreamRenderer SHALL render task items in a compact single-line format

### 需求 5：API 任务进度端点

**用户故事：** 作为前端开发者，我希望通过 API 获取任务清单的实时状态，以便在 Web 界面中展示进度。

#### 验收标准

1. WHEN a task event occurs during SSE streaming, THE API SHALL include the task event in the SSE event stream with event type "task_update"
2. THE API task event payload SHALL include the task list title, all task items with their current statuses, and the progress summary
3. THE ToolCallEvent.to_dict method SHALL serialize task-related fields into the event dictionary

### 需求 6：任务清单序列化与反序列化

**用户故事：** 作为开发者，我希望任务清单能被序列化为字典和从字典反序列化，以便在事件传递和 API 响应中使用。

#### 验收标准

1. THE TaskList SHALL serialize to a Python dictionary containing title, items, created_at timestamp, and progress summary
2. THE TaskList SHALL deserialize from a Python dictionary back to an equivalent TaskList instance
3. FOR ALL valid TaskList instances, serializing then deserializing SHALL produce an equivalent TaskList (round-trip property)
4. THE TaskItem SHALL serialize to a Python dictionary containing title, status string, and optional result

