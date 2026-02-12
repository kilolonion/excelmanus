# 需求文档：ExcelManus v2

## 简介

ExcelManus v2 是一个基于大语言模型的 Excel 智能代理框架，支持独立 Agent 模式和 MCP Server 双模式运行。用户通过自然语言描述 Excel 任务，系统自动拆解意图并调用工具完成操作。新版本采用原生 OpenAI Responses API + tool calling 实现，支持 Skills 动态加载，提供 CLI 和 REST API 双入口。

## 术语表

- **Agent_Engine**：核心代理引擎，负责 LLM 交互、Tool Calling 循环、对话记忆和错误恢复
- **Skill**：按职责分组的工具集合（如数据操作 Skill、可视化 Skill），可动态注册和加载
- **Skill_Registry**：Skill 注册中心，管理所有已注册 Skill 的元数据和加载状态
- **MCP_Server**：基于 MCP 协议的服务端，将 Skills 暴露为 MCP 工具供外部 AI 调用
- **CLI_Interface**：基于 Rich 的命令行交互界面，提供对话式 Excel 操作体验
- **API_Server**：基于 FastAPI 的 REST API 服务，提供 HTTP 接口供程序化调用
- **Config_Manager**：配置管理模块，处理多模型配置、环境变量和运行时参数
- **Tool_Schema**：工具的 JSON Schema 定义，用于 tool calling 参数描述和 MCP 工具暴露
- **Conversation_Memory**：对话记忆组件，维护多轮对话的上下文信息
- **Tool_Calling_Loop**：Agent 的核心执行循环，包括 LLM 推理、工具调用、结果反馈的迭代过程

## 需求

### 需求 1：Agent 核心引擎

**用户故事：** 作为开发者，我希望有一个可靠的 Agent 引擎来驱动 LLM 与工具之间的交互循环，以便系统能自动完成复杂的 Excel 任务。

#### 验收标准

1. WHEN 用户提交一条自然语言指令, THE Agent_Engine SHALL 将指令连同可用工具的 Tool_Schema 发送给 LLM 并获取响应
2. WHEN LLM 响应包含一个或多个 tool_calls, THE Agent_Engine SHALL 解析每个工具名称和参数，调用对应的 Skill 工具函数，并将执行结果作为 tool message 反馈给 LLM
3. WHEN LLM 响应为纯文本（无 tool_calls）, THE Agent_Engine SHALL 将该文本作为最终回复返回给用户
4. WHILE Tool_Calling_Loop 执行中, THE Agent_Engine SHALL 维护不超过 20 轮的迭代上限，达到上限时返回当前已有结果并提示用户
5. WHEN 工具函数执行抛出异常, THE Agent_Engine SHALL 捕获异常信息，将错误描述作为 tool message 反馈给 LLM，由 LLM 决定下一步操作
6. WHEN 连续 3 次工具调用均失败, THE Agent_Engine SHALL 终止当前循环并向用户返回错误摘要
7. WHEN 用户发送新指令, THE Conversation_Memory SHALL 将之前的对话历史包含在发送给 LLM 的消息列表中
8. WHEN 对话历史的 token 数量接近模型上下文窗口限制, THE Conversation_Memory SHALL 截断最早的对话记录以保持在限制范围内
9. THE Agent_Engine SHALL 使用 OpenAI Responses API 的工具调用语义，并兼容处理单轮内多个 tool_calls
10. WHEN 运行在 API 模式, THE Agent_Engine SHALL 通过异步客户端发起模型调用，且对阻塞型工具执行使用线程池隔离，避免阻塞事件循环

### 需求 2：Skills 系统

**用户故事：** 作为开发者，我希望工具按职责分组为 Skill 并支持动态加载，以便系统功能可以灵活扩展。

#### 验收标准

1. THE Skill_Registry SHALL 提供 register 方法，接受 Skill 名称、描述和工具函数列表作为参数完成注册
2. WHEN 一个 Skill 被注册, THE Skill_Registry SHALL 验证 Skill 名称唯一性，重复注册时返回明确的错误信息
3. WHEN Agent_Engine 初始化时, THE Skill_Registry SHALL 自动扫描并加载已安装包内 skills 命名空间下所有符合约定的 Skill 模块
4. THE Skill SHALL 通过标准化的 Python 模块结构定义，每个 Skill 模块导出名称、描述和工具函数列表
5. WHEN Agent_Engine 需要构建 Tool_Schema 列表时, THE Skill_Registry SHALL 返回所有已加载 Skill 中工具函数的 JSON Schema 定义
6. THE 数据操作 Skill（MVP）SHALL 提供 read_excel、write_excel、analyze_data、filter_data 和 transform_data 工具函数
7. THE 可视化 Skill（MVP）SHALL 提供 create_chart 工具函数，支持柱状图、折线图、饼图、散点图和雷达图类型
8. THE 格式化 Skill（MVP）SHALL 提供 format_cells 和 adjust_column_width 工具函数

### 需求 3：MCP Server 模式

**用户故事：** 作为 AI 工具用户，我希望 ExcelManus 能作为 MCP Server 运行，以便 Kiro、Claude Desktop 等 AI 客户端可以直接调用 Excel 操作能力。

#### 验收标准

1. WHEN MCP_Server 启动时, THE MCP_Server SHALL 读取 Skill_Registry 中所有已注册的工具，并将每个工具转换为 MCP 协议的工具定义
2. WHEN 外部 AI 客户端调用一个 MCP 工具, THE MCP_Server SHALL 将 MCP 请求参数映射为对应 Skill 工具函数的参数并执行
3. WHEN Skill 工具函数执行完成, THE MCP_Server SHALL 将执行结果按 MCP 协议格式返回给调用方
4. IF Skill 工具函数执行失败, THEN THE MCP_Server SHALL 返回包含错误类型和错误描述的 MCP 错误响应
5. THE MCP_Server SHALL 通过 stdio 传输方式与客户端通信，符合 MCP SDK 标准实现
6. WHEN 新的 Skill 被注册到 Skill_Registry, THE MCP_Server SHALL 在下次启动时自动包含新 Skill 的工具定义
7. THE MCP_Server SHALL 提供标准 stdio 启动入口（命令行可直接启动），并在启动日志中输出已注册工具数量

### 需求 4：CLI 交互模式

**用户故事：** 作为终端用户，我希望通过命令行与 ExcelManus 进行对话式交互，以便在终端环境中完成 Excel 操作任务。

#### 验收标准

1. WHEN CLI_Interface 启动时, THE CLI_Interface SHALL 使用 Rich 库渲染欢迎信息和可用命令提示
2. WHEN 用户输入自然语言指令, THE CLI_Interface SHALL 将指令传递给 Agent_Engine 并将返回结果渲染到终端
3. WHILE Agent_Engine 正在处理请求, THE CLI_Interface SHALL 显示加载动画指示处理进行中
4. WHEN Agent_Engine 返回结果, THE CLI_Interface SHALL 使用 Rich Markdown 渲染格式化输出
5. WHEN 用户输入 "exit" 或 "quit" 或按下 Ctrl+C, THE CLI_Interface SHALL 优雅地终止会话并显示告别信息
6. WHEN 用户输入 "/history" 命令, THE CLI_Interface SHALL 显示当前会话的对话历史摘要
7. WHEN 用户输入 "/clear" 命令, THE CLI_Interface SHALL 清除当前对话历史并确认操作
8. WHEN 用户输入 "/help" 命令, THE CLI_Interface SHALL 显示所有可用命令和使用说明

### 需求 5：API 服务模式

**用户故事：** 作为应用开发者，我希望通过 REST API 调用 ExcelManus 的能力，以便将 Excel 智能处理集成到自己的应用中。

#### 验收标准

1. WHEN API_Server 启动时, THE API_Server SHALL 在配置的端口上监听 HTTP 请求，并提供 /docs 路径的 OpenAPI 文档页面
2. WHEN 客户端发送 POST /api/v1/chat 请求（包含 message 字段）, THE API_Server SHALL 创建或复用会话，将消息传递给 Agent_Engine，并返回包含 session_id 和 reply 的 JSON 响应
3. WHEN 客户端发送带有 session_id 的 POST /api/v1/chat 请求, THE API_Server SHALL 在对应会话的 Conversation_Memory 上下文中处理消息
4. WHEN 客户端发送 DELETE /api/v1/sessions/{session_id} 请求, THE API_Server SHALL 清除对应会话的对话历史并释放资源
5. WHEN 客户端发送 GET /api/v1/health 请求, THE API_Server SHALL 返回服务状态信息，包括版本号和已加载的 Skill 列表
6. IF 请求处理过程中发生未预期的异常, THEN THE API_Server SHALL 返回 HTTP 500 响应，包含错误标识符但不暴露内部堆栈信息
7. WHEN 客户端发送 POST /api/v1/chat 请求, THE API_Server SHALL 使用异步处理方式执行 Agent_Engine 调用，避免阻塞事件循环
8. THE API_Server SHALL 实现会话资源治理：会话空闲超过 TTL（默认 30 分钟）自动清理
9. THE API_Server SHALL 限制最大会话数量（默认 1000），超过上限时拒绝新会话并返回明确错误
10. THE API_Server SHALL 对会话容器的读写提供并发安全机制，避免并发请求下的竞态条件

### 需求 6：配置管理

**用户故事：** 作为开发者，我希望通过统一的配置管理支持多种 LLM 模型和运行参数，以便灵活切换模型和调整系统行为。

#### 验收标准

1. THE Config_Manager SHALL 支持通过环境变量设置 API Key（EXCELMANUS_API_KEY）、Base URL（EXCELMANUS_BASE_URL）和模型名称（EXCELMANUS_MODEL）
2. WHEN 环境变量未设置时, THE Config_Manager SHALL 从项目根目录的 .env 文件中读取配置
3. WHEN 环境变量和 .env 文件均未提供 API Key, THE Config_Manager SHALL 在启动时抛出明确的配置缺失错误，指明需要设置的变量名
4. THE Config_Manager SHALL 提供默认的 Base URL（https://dashscope.aliyuncs.com/compatible-mode/v1）和默认模型名称（qwen-max-latest），仅 API Key 为必填项
5. WHEN 配置加载完成, THE Config_Manager SHALL 验证 Base URL 格式为合法的 HTTP/HTTPS URL
6. THE Config_Manager SHALL 支持通过配置设置 Agent_Engine 的最大迭代次数（默认 20）和最大连续失败次数（默认 3）
7. THE Config_Manager SHALL 支持会话治理配置项：SESSION_TTL_SECONDS（默认 1800）与 MAX_SESSIONS（默认 1000）
8. THE Config_Manager SHALL 支持文件访问白名单根目录配置项：WORKSPACE_ROOT（默认当前工作目录）

### 需求 7：项目工程化

**用户故事：** 作为开发者，我希望项目具备规范的工程化结构，以便于维护、测试和分发。

#### 验收标准

1. THE 项目 SHALL 使用 pyproject.toml 作为包管理配置文件，定义项目元数据、依赖和入口点
2. THE 项目 SHALL 定义 CLI 入口点（excelmanus 命令）和 API 入口点（excelmanus-api 命令）
3. THE 项目 SHALL 使用 Python 标准 logging 模块，提供可配置的日志级别（DEBUG、INFO、WARNING、ERROR）
4. WHEN 日志级别设置为 DEBUG, THE 日志系统 SHALL 输出 Agent_Engine 的每一轮 Tool Calling 详情，包括工具名称、参数和返回值
5. WHEN 日志级别设置为 INFO, THE 日志系统 SHALL 仅输出用户指令摘要和最终结果
6. THE 项目 SHALL 使用 pytest 作为测试框架，测试文件组织在 tests/ 目录下
7. THE 项目 SHALL 提供 Dockerfile，支持容器化部署 API 服务模式
8. THE 项目 SHALL 对日志中的敏感信息（API Key、Authorization、Cookie、绝对本地路径）进行脱敏或截断
9. THE 项目 SHALL 将测试相关依赖（如 hypothesis）放入开发依赖组而非生产运行时依赖

### 需求 8：安全与文件访问边界

**用户故事：** 作为开发者和部署者，我希望系统对文件访问和工具执行有明确边界，以便避免越权读写和敏感信息泄露。

#### 验收标准

1. WHEN 工具请求读取或写入文件, THE 系统 SHALL 将路径规范化并校验必须位于 WORKSPACE_ROOT 之下，越界路径应被拒绝
2. IF 输入路径包含路径穿越特征（如 `..`、符号链接越界）, THEN 系统 SHALL 返回可审计的安全错误，不执行文件操作
3. THE 工具执行日志 SHALL 记录操作摘要（工具名、耗时、成功/失败），但不记录完整敏感内容
4. WHEN 日志级别为 DEBUG, THE 系统 MAY 记录参数结构，但 SHALL 对 API Key、Token、Cookie、文件绝对路径进行脱敏
