# 实现计划：Agent Task List

## 概述

将设计文档中的任务清单功能分解为增量式编码任务。每个任务构建在前一个任务之上，最终将所有组件连接到现有架构中。

## 任务

- [x] 1. 实现任务清单数据模型
  - [x] 1.1 创建 `excelmanus/task_list.py`，实现 TaskStatus 枚举、TaskItem 数据类、TaskList 数据类、TaskStore 类
    - TaskStatus: pending, in_progress, completed, failed 四种状态
    - TaskItem: title, status, result 字段，transition() 方法执行合法状态转换，to_dict()/from_dict() 序列化
    - TaskList: title, items, created_at 字段，progress_summary() 方法，to_dict()/from_dict() 序列化
    - TaskStore: create(), update_item(), clear() 方法，current 属性
    - VALID_TRANSITIONS 字典定义合法状态转换
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 6.1, 6.2, 6.3, 6.4_

  - [x] 1.2 编写属性测试 `tests/test_pbt_task_list.py`（Property 1-6）
    - **Property 1: TaskList 序列化往返一致性**
    - **Validates: Requirements 6.3, 6.1, 6.2, 6.4, 1.1, 1.2**
    - **Property 2: 新建 TaskList 所有项初始为 pending**
    - **Validates: Requirements 1.4**
    - **Property 3: 状态转换合法性**
    - **Validates: Requirements 1.5**
    - **Property 4: 进度摘要不变量**
    - **Validates: Requirements 1.6**
    - **Property 5: task_create 工具产生有效 TaskList**
    - **Validates: Requirements 2.3**
    - **Property 6: 越界索引返回错误**
    - **Validates: Requirements 2.4**

  - [x] 1.3 编写单元测试 `tests/test_task_list.py`（数据模型部分）
    - 测试 TaskStatus 枚举包含恰好四种状态
    - 测试非法状态转换抛出 ValueError
    - 测试 TaskStore 无活跃 TaskList 时 update_item 报错
    - _Requirements: 1.3, 1.5_

- [x] 2. 实现任务清单工具并注册
  - [x] 2.1 创建 `excelmanus/tools/task_tools.py`，实现 task_create 和 task_update 工具函数及 get_tools() 返回 ToolDef 列表
    - task_create(title, subtasks) → 创建 TaskList 并返回描述字符串
    - task_update(task_index, status, result) → 更新 TaskItem 并返回描述字符串
    - init_store(store) 注入 TaskStore 实例
    - get_tools() 返回符合 ToolDef schema 格式的工具定义
    - 所有错误通过返回描述性字符串处理，不抛出异常
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [x] 2.2 编写单元测试（工具部分，追加到 `tests/test_task_list.py`）
    - 测试 get_tools() 返回的 ToolDef schema 格式合规
    - 测试 task_update 传入无效状态字符串返回错误
    - 测试 task_create 空子任务列表正常工作
    - _Requirements: 2.4, 2.5, 2.6_

- [x] 3. 扩展事件系统
  - [x] 3.1 修改 `excelmanus/events.py`，在 EventType 枚举中新增 TASK_LIST_CREATED 和 TASK_ITEM_UPDATED，在 ToolCallEvent 中新增 task_list_data、task_index、task_status、task_result 字段
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 3.2 编写属性测试（Property 8，追加到 `tests/test_pbt_task_list.py`）
    - **Property 8: ToolCallEvent 任务字段序列化完整性**
    - **Validates: Requirements 5.3, 3.4**

  - [x] 3.3 编写单元测试（事件部分，追加到 `tests/test_task_list.py`）
    - 测试 EventType 包含 TASK_LIST_CREATED 和 TASK_ITEM_UPDATED
    - 测试 ToolCallEvent.from_dict 能正确反序列化任务字段
    - _Requirements: 3.3, 3.4_

- [x] 4. 检查点 — 确保所有测试通过
  - 运行 `pytest tests/test_task_list.py tests/test_pbt_task_list.py`，确保所有测试通过，如有问题请询问用户。

- [x] 5. 集成到 AgentEngine
  - [x] 5.1 修改 `excelmanus/engine.py`，在 AgentEngine.__init__ 中创建 TaskStore 实例，初始化 task_tools，在 register_builtin_tools 中注册任务工具，在 _execute_tool_call 成功执行 task_create/task_update 后发射对应事件
    - 导入 TaskStore 和 task_tools
    - __init__ 中创建 self._task_store = TaskStore()
    - 调用 task_tools.init_store(self._task_store)
    - 在 ToolRegistry.register_builtin_tools 中注册 task_tools.get_tools()
    - _execute_tool_call 中检测 task_create/task_update 成功后 emit TASK_LIST_CREATED/TASK_ITEM_UPDATED 事件
    - _Requirements: 2.3, 3.1, 3.2_

- [x] 6. CLI 渲染集成
  - [x] 6.1 修改 `excelmanus/renderer.py`，在 StreamRenderer 中新增 _render_task_list_created 和 _render_task_item_updated 方法，在 handle_event 的 handlers 映射中注册
    - 使用状态图标映射：pending→⬜, in_progress→🔄, completed→✅, failed→❌
    - 任务清单创建时显示标题和所有子任务
    - 任务项更新时显示更新后的状态
    - 全部完成时显示摘要行
    - 支持窄终端紧凑格式
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 6.2 编写属性测试（Property 7，追加到 `tests/test_pbt_task_list.py`）
    - **Property 7: 渲染输出包含正确状态图标**
    - **Validates: Requirements 4.1, 4.2, 4.3**

  - [x] 6.3 编写单元测试（渲染部分，追加到 `tests/test_task_list.py`）
    - 测试全部完成时显示摘要行
    - 测试窄终端（宽度 < 60）渲染紧凑格式
    - _Requirements: 4.4, 4.5_

- [x] 7. API SSE 集成
  - [x] 7.1 修改 `excelmanus/api.py`，在 _sse_event_to_sse 函数中新增对 TASK_LIST_CREATED 和 TASK_ITEM_UPDATED 事件的处理，SSE 事件类型为 "task_update"，payload 包含 task_list 数据和进度摘要
    - _Requirements: 5.1, 5.2_

  - [x] 7.2 编写单元测试（API 部分，追加到 `tests/test_task_list.py`）
    - 测试 _sse_event_to_sse 对任务事件返回正确格式的 SSE 文本
    - _Requirements: 5.1, 5.2_

- [x] 8. 最终检查点 — 确保所有测试通过
  - 运行 `pytest tests/test_task_list.py tests/test_pbt_task_list.py`，确保所有测试通过，如有问题请询问用户。

## 备注

- 标记 `*` 的子任务为可选测试任务，可跳过以加速 MVP
- 每个任务引用了具体的需求编号，确保可追溯性
- 属性测试验证通用正确性属性，单元测试验证具体示例和边界情况
- 检查点确保增量验证
