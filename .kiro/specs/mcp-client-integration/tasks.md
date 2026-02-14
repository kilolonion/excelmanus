# 实现计划：MCP Client 集成

## 概述

为 ExcelManus 添加 MCP Client 支持，使 Agent 能连接外部 MCP Server 并使用远程工具。实现按模块自底向上推进：配置层 → 客户端封装 → 管理器 → 引擎集成。

## 任务

- [x] 1. 添加依赖和创建模块结构
  - 在 `pyproject.toml` 的 dependencies 中添加 `mcp>=1.0.0`
  - 创建 `excelmanus/mcp/` 包目录和 `__init__.py`
  - 创建 `excelmanus/mcp/config.py`、`excelmanus/mcp/client.py`、`excelmanus/mcp/manager.py` 空文件
  - _Requirements: 6.1, 6.2_

- [x] 2. 实现 MCP 配置层
  - [x] 2.1 实现 MCPServerConfig 数据模型和 MCPConfigLoader
    - 在 `excelmanus/mcp/config.py` 中定义 `MCPServerConfig` dataclass
    - 实现 `MCPConfigLoader.load()` 方法：环境变量优先 → 参数路径 → workspace_root/mcp.json → ~/.excelmanus/mcp.json
    - 实现 `_parse_config()` 和 `_validate_server_config()` 方法
    - 配置文件不存在时返回空列表，格式错误时记录日志并跳过
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8_

  - [x] 2.2 编写配置解析属性测试
    - **Property 1: 配置解析 round-trip**
    - **Validates: Requirements 1.1, 1.3, 1.4, 1.5**

  - [x] 2.3 编写非法配置过滤属性测试
    - **Property 2: 非法配置过滤**
    - **Validates: Requirements 1.6, 3.4**

  - [x] 2.4 编写配置层单元测试
    - 测试配置文件搜索优先级（1.2, 1.7）
    - 测试配置文件不存在时静默跳过（1.8）
    - 测试 JSON 格式错误处理
    - _Requirements: 1.2, 1.7, 1.8_

- [x] 3. 检查点 - 确保配置层测试通过
  - 确保所有测试通过，如有问题请询问用户。

- [x] 4. 实现工具名映射和转换工具函数
  - [x] 4.1 实现工具名前缀添加和还原函数
    - 在 `excelmanus/mcp/manager.py` 中实现 `add_tool_prefix(server_name, tool_name)` 和 `parse_tool_prefix(prefixed_name)` 函数
    - server_name 中的 `-` 替换为 `_`
    - 格式：`mcp_{normalized_server_name}_{original_tool_name}`
    - _Requirements: 3.3, 5.2_

  - [x] 4.2 实现 MCP 工具定义到 ToolDef 的转换函数
    - 实现 `make_tool_def(server_name, client, mcp_tool)` 函数
    - 映射 name（含前缀）、description（含 `[MCP:{server}]` 标记）、input_schema
    - func 为异步调用闭包（同步包装）
    - _Requirements: 3.2, 3.3, 4.1_

  - [x] 4.3 实现 MCP 工具结果到字符串的转换函数
    - 实现 `format_tool_result(mcp_result)` 函数
    - 提取 content 列表中所有 text 类型的文本并拼接
    - _Requirements: 5.3_

  - [x] 4.4 编写工具定义转换属性测试
    - **Property 3: 工具定义转换正确性**
    - **Validates: Requirements 3.2, 3.3, 4.1**

  - [x] 4.5 编写工具名前缀 round-trip 属性测试
    - **Property 4: 工具名前缀 round-trip**
    - **Validates: Requirements 5.2**

  - [x] 4.6 编写工具结果转换属性测试
    - **Property 5: 工具结果字符串转换**
    - **Validates: Requirements 5.3**

- [x] 5. 实现 MCPClientWrapper
  - [x] 5.1 实现 MCPClientWrapper 类
    - 在 `excelmanus/mcp/client.py` 中实现
    - `connect()`: 根据 transport 类型使用 MCP SDK 的 stdio_client 或 sse_client 建立连接
    - `discover_tools()`: 调用 session.list_tools() 获取工具列表
    - `call_tool()`: 调用 session.call_tool() 执行远程工具，处理超时
    - `close()`: 关闭 session 和传输层资源
    - _Requirements: 2.1, 2.2, 2.3, 3.1, 5.1, 5.4, 5.5_

  - [x] 5.2 编写 MCPClientWrapper 单元测试
    - 使用 mock 模拟 MCP SDK 接口
    - 测试工具调用超时处理（5.4）
    - 测试工具调用错误处理（5.5）
    - _Requirements: 5.4, 5.5_

- [x] 6. 实现 MCPManager
  - [x] 6.1 实现 MCPManager 类
    - 在 `excelmanus/mcp/manager.py` 中实现
    - `initialize()`: 加载配置 → 逐个连接 Server（失败跳过）→ 发现工具 → 批量注册到 ToolRegistry
    - `shutdown()`: 关闭所有连接
    - 工具名冲突时跳过并记录警告
    - _Requirements: 2.1, 2.4, 2.5, 4.1, 4.3, 7.1, 7.3_

  - [x] 6.2 编写连接故障隔离属性测试
    - **Property 6: 连接故障隔离**
    - **Validates: Requirements 2.4**

  - [x] 6.3 编写 MCPManager 单元测试
    - 测试工具名冲突处理（4.3）
    - 测试日志输出（7.1, 7.3）
    - 测试资源清理（2.5）
    - _Requirements: 4.3, 7.1, 7.3, 2.5_

- [x] 7. 检查点 - 确保 MCP 模块测试通过
  - 确保所有测试通过，如有问题请询问用户。

- [x] 8. 集成到 AgentEngine
  - [x] 8.1 在 AgentEngine 中集成 MCPManager
    - 在 `engine.py` 的 `AgentEngine.__init__()` 中创建 `MCPManager` 实例
    - 添加 `initialize_mcp()` 和 `shutdown_mcp()` 异步方法
    - _Requirements: 2.1, 4.4_

  - [x] 8.2 在 CLI 和 API 入口调用 MCP 初始化
    - 在 `cli.py` 的启动流程中调用 `engine.initialize_mcp()`
    - 在 `api.py` 的启动流程中调用 `engine.initialize_mcp()`
    - 在退出流程中调用 `engine.shutdown_mcp()`
    - _Requirements: 2.1, 2.5_

  - [x] 8.3 编写集成单元测试
    - 测试 MCP 工具注册后出现在 tool scope 中（4.4）
    - 测试无配置时 Agent 正常启动（1.8）
    - _Requirements: 4.4, 1.8_

- [x] 9. 更新模块导出
  - 在 `excelmanus/mcp/__init__.py` 中导出 `MCPManager`、`MCPConfigLoader`、`MCPServerConfig`
  - _Requirements: 6.3_

- [x] 10. 最终检查点 - 确保所有测试通过
  - 确保所有测试通过，如有问题请询问用户。

## 备注

- 标记 `*` 的任务为可选测试任务，可跳过以加速 MVP
- 每个任务引用了具体的需求编号以确保可追溯性
- 属性测试验证通用正确性属性，单元测试验证具体示例和边界情况
- 检查点确保增量验证
