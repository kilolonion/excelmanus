# 需求文档：MCP Client 集成

## 简介

为 ExcelManus Agent 框架添加 MCP（Model Context Protocol）Client 支持。Agent 作为 MCP Client 连接外部 MCP Server，发现并调用远程工具，将这些工具注册到现有的 ToolRegistry 中，供 Agent 的 Tool Calling 循环使用。这不是让 ExcelManus 对外提供 MCP 服务，而是让 ExcelManus 消费外部 MCP Server 提供的工具能力。

## 术语表

- **MCP_Client**：MCP 协议的客户端实现，负责连接 MCP Server、发现工具列表、转发工具调用请求
- **MCP_Server**：外部 MCP 协议服务端，提供工具定义和工具执行能力
- **ToolRegistry**：ExcelManus 现有的工具注册中心，管理工具定义、schema 输出和调用执行
- **ToolDef**：工具定义数据类，包含 name、description、input_schema、func 字段
- **MCP_Config**：MCP Server 连接配置，定义服务器名称、传输方式、启动命令等
- **Tool_Prefix**：远程工具名称前缀，格式为 `mcp_{server_name}_`，用于避免与内置工具冲突
- **stdio_Transport**：基于标准输入输出的 MCP 传输方式，通过子进程通信
- **SSE_Transport**：基于 Server-Sent Events 的 MCP 传输方式，通过 HTTP 连接
- **AgentEngine**：ExcelManus 核心代理引擎，驱动 LLM 与工具之间的 Tool Calling 循环

## 需求

### 需求 1：MCP 配置管理

**用户故事：** 作为开发者，我希望通过配置文件定义要连接的 MCP Server 列表，以便灵活管理外部工具来源。

#### 验收标准

1. THE MCP_Config SHALL 支持通过 JSON 配置文件（`mcp.json`）定义 MCP Server 列表
2. WHEN 配置文件路径未指定时，THE MCP_Client SHALL 按以下顺序搜索配置文件：项目根目录 `mcp.json`、用户目录 `~/.excelmanus/mcp.json`
3. THE MCP_Config SHALL 为每个 MCP Server 定义以下字段：服务器名称（name）、传输方式（transport，值为 "stdio" 或 "sse"）、启动命令或 URL
4. WHEN 配置文件中存在 stdio 类型的 MCP Server 时，THE MCP_Config SHALL 包含 command（启动命令）和可选的 args（命令参数列表）、env（环境变量字典）字段
5. WHEN 配置文件中存在 SSE 类型的 MCP Server 时，THE MCP_Config SHALL 包含 url（SSE 端点 URL）字段
6. IF 配置文件格式不合法或缺少必填字段，THEN THE MCP_Client SHALL 记录错误日志并跳过该配置项，继续处理其余配置
7. WHEN 环境变量 `EXCELMANUS_MCP_CONFIG` 被设置时，THE MCP_Client SHALL 优先使用该路径作为配置文件位置
8. IF 配置文件不存在，THEN THE MCP_Client SHALL 静默跳过 MCP 初始化，不影响 Agent 正常启动

### 需求 2：MCP Server 连接管理

**用户故事：** 作为开发者，我希望 Agent 启动时自动连接配置的 MCP Server，以便无需手动干预即可使用远程工具。

#### 验收标准

1. WHEN AgentEngine 初始化时，THE MCP_Client SHALL 异步连接所有已配置的 MCP Server
2. THE MCP_Client SHALL 支持 stdio 传输方式，通过启动子进程并使用标准输入输出通信
3. THE MCP_Client SHALL 支持 SSE 传输方式，通过 HTTP 连接到指定 URL
4. IF 某个 MCP Server 连接失败，THEN THE MCP_Client SHALL 记录错误日志并继续连接其余 Server，不影响 Agent 正常运行
5. WHEN AgentEngine 关闭时，THE MCP_Client SHALL 优雅关闭所有 MCP Server 连接，释放子进程和网络资源
6. WHILE MCP Server 连接处于活跃状态，THE MCP_Client SHALL 维护连接的健康状态

### 需求 3：远程工具发现

**用户故事：** 作为开发者，我希望 Agent 能自动发现 MCP Server 提供的工具列表，以便无需手动配置每个远程工具。

#### 验收标准

1. WHEN 成功连接 MCP Server 后，THE MCP_Client SHALL 调用 `tools/list` 获取该 Server 的全部可用工具
2. THE MCP_Client SHALL 将每个远程工具的名称、描述和输入 schema 转换为 ToolDef 格式
3. THE MCP_Client SHALL 为远程工具名称添加 Tool_Prefix（格式：`mcp_{server_name}_{original_name}`），确保与内置工具名称不冲突
4. IF 远程工具的 input_schema 不符合 JSON Schema 规范，THEN THE MCP_Client SHALL 跳过该工具并记录警告日志

### 需求 4：远程工具注册

**用户故事：** 作为开发者，我希望远程工具被自动注册到 ToolRegistry 中，以便 LLM 能像使用内置工具一样使用远程工具。

#### 验收标准

1. WHEN 远程工具发现完成后，THE MCP_Client SHALL 将所有有效的远程工具批量注册到 ToolRegistry
2. THE ToolRegistry SHALL 对远程工具和内置工具使用统一的 `to_openai_schema()` 输出格式
3. IF 远程工具名称（含前缀）与已注册工具冲突，THEN THE MCP_Client SHALL 跳过该工具并记录警告日志
4. WHEN 远程工具注册成功后，THE AgentEngine SHALL 在 Tool Calling 循环中将远程工具纳入可用工具范围

### 需求 5：远程工具调用

**用户故事：** 作为开发者，我希望 Agent 调用远程工具时能透明地转发请求到对应的 MCP Server，以便实现无缝的工具调用体验。

#### 验收标准

1. WHEN LLM 选择调用远程工具时，THE MCP_Client SHALL 将调用请求通过 MCP 协议转发到对应的 MCP Server
2. THE MCP_Client SHALL 在转发请求前将带前缀的工具名还原为原始工具名
3. WHEN MCP Server 返回工具执行结果时，THE MCP_Client SHALL 将结果转换为字符串格式返回给 ToolRegistry
4. IF MCP Server 工具调用超时（默认 30 秒），THEN THE MCP_Client SHALL 返回超时错误信息
5. IF MCP Server 工具调用返回错误，THEN THE MCP_Client SHALL 将错误信息包装为 ToolExecutionError 抛出

### 需求 6：依赖管理

**用户故事：** 作为开发者，我希望 MCP Client 功能使用官方 MCP SDK，以便获得协议兼容性和维护性保障。

#### 验收标准

1. THE MCP_Client SHALL 使用 `mcp` Python 包（官方 MCP SDK）作为核心依赖
2. THE MCP_Client SHALL 将 `mcp` 包添加到 `pyproject.toml` 的 dependencies 列表中
3. THE MCP_Client SHALL 使用 MCP SDK 提供的 `ClientSession`、`StdioServerParameters` 等标准接口

### 需求 7：可观测性

**用户故事：** 作为开发者，我希望 MCP Client 的连接和调用过程有清晰的日志输出，以便排查问题。

#### 验收标准

1. WHEN MCP Server 连接成功时，THE MCP_Client SHALL 记录 INFO 级别日志，包含服务器名称和发现的工具数量
2. WHEN 远程工具被调用时，THE MCP_Client SHALL 记录 DEBUG 级别日志，包含工具名称和参数摘要
3. IF MCP 操作发生错误，THEN THE MCP_Client SHALL 记录 ERROR 级别日志，包含错误详情和上下文信息
