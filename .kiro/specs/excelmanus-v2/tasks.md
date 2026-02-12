# 实现计划：ExcelManus v2

## 概述

基于设计文档，将 ExcelManus v2 拆解为增量式编码任务。每个任务构建在前一个任务之上，确保无孤立代码。使用 Python 3.10+。

依赖分组：
- 运行时依赖：openai、pandas、openpyxl、matplotlib、fastapi、uvicorn、rich、pydantic、mcp、python-dotenv
- 开发测试依赖：pytest、pytest-asyncio、httpx、hypothesis

## 任务

- [x] 1. 项目脚手架与配置基础
  - [x] 1.1 创建项目结构和 `pyproject.toml`
    - 创建 `excelmanus/`：`__init__.py`、`config.py`、`logger.py`、`memory.py`、`engine.py`、`security.py`、`session.py`、`skills/`、`cli.py`、`api.py`、`mcp_server.py`
    - 编写 `pyproject.toml`：定义运行时依赖与 `dev` 可选依赖
    - 定义入口点：`excelmanus`、`excelmanus-api`、`excelmanus-mcp`
    - 创建 `tests/conftest.py` 与测试目录
    - _Requirements: 7.1, 7.2, 7.6, 7.9_

  - [x] 1.2 实现 Config Manager（`excelmanus/config.py`）
    - 实现 `ExcelManusConfig`（frozen=True）
    - 实现 `load_config()`：环境变量 > `.env` > 默认值
    - 支持并校验：`SESSION_TTL_SECONDS`、`MAX_SESSIONS`、`WORKSPACE_ROOT`
    - 缺失 API Key 时抛出 `ConfigError`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_

  - [x] 1.3 实现日志配置与脱敏（`excelmanus/logger.py`）
    - 支持 DEBUG/INFO/WARNING/ERROR
    - DEBUG 输出工具调用详情，INFO 输出摘要
    - 对 API Key、Token、Cookie、绝对路径做脱敏
    - _Requirements: 7.3, 7.4, 7.5, 7.8_

  - [x] 1.4 编写配置与日志测试
    - Property 16、17
    - 单元测试：缺失配置、默认值、`.env` 优先级、脱敏行为
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 7.8_

- [x] 2. Skill 系统与安全边界
  - [x] 2.1 实现 ToolDef 与 SkillRegistry（`excelmanus/skills/__init__.py`）
    - `ToolDef` 使用完整 `input_schema`（JSON Schema）
    - `SkillRegistry`：`register()`、`auto_discover()`、`get_all_tools()`、`get_openai_schemas()`、`call_tool()`
    - `auto_discover()` 使用包命名空间扫描（非硬编码磁盘路径）
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 2.2 实现文件访问守卫（`excelmanus/security.py`）
    - 实现 `FileAccessGuard.resolve_and_validate()`
    - 拒绝路径穿越、符号链接越界、工作目录外访问
    - _Requirements: 8.1, 8.2_

  - [x] 2.3 实现数据操作 Skill（`excelmanus/skills/data_skill.py`）
    - read_excel、write_excel、analyze_data、filter_data、transform_data
    - 文件路径统一接入 `FileAccessGuard`
    - _Requirements: 2.6, 8.1, 8.2_

  - [x] 2.4 实现可视化 Skill（`excelmanus/skills/chart_skill.py`）
    - create_chart（bar/line/pie/scatter/radar）
    - _Requirements: 2.7_

  - [x] 2.5 实现格式化 Skill（`excelmanus/skills/format_skill.py`）
    - format_cells、adjust_column_width
    - _Requirements: 2.8_

  - [x] 2.6 编写 Skill 与安全测试
    - Property 8、19
    - 单元测试：MVP 工具清单、读写往返、越界路径拒绝
    - _Requirements: 2.1, 2.2, 2.5, 2.6, 2.7, 2.8, 8.1, 8.2_

- [x] 3. Agent 引擎核心
  - [x] 3.1 实现 Conversation Memory（`excelmanus/memory.py`）
    - 消息管理、token 计数、截断策略
    - 始终保留 system prompt 与最近消息
    - _Requirements: 1.7, 1.8_

  - [x] 3.2 实现 AgentEngine（`excelmanus/engine.py`）
    - 使用 `AsyncOpenAI` 与 Responses API tools schema
    - 实现 Tool Calling 循环：解析 `tool_calls`、执行工具、反馈结果
    - 阻塞型工具调用通过 `asyncio.to_thread` 隔离
    - 实现迭代上限与连续失败熔断
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.9, 1.10_

  - [x] 3.3 编写 Engine/Memory 测试（核心必做）
    - Property 1、2、3、4、5、6、7、20
    - 覆盖多 tool_calls、纯文本终止、异常反馈、熔断、阻塞工具隔离执行
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 5.7_

- [x] 4. MCP Server 模式
  - [x] 4.1 实现 MCP Server（`excelmanus/mcp_server.py`）
    - 从 SkillRegistry 暴露工具到 MCP
    - 注册 `call_tool` handler
    - 实现 stdio 启动入口 `run_stdio_server()`
    - 启动日志输出已注册工具数量
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [x] 4.2 编写 MCP 测试（核心必做）
    - Property 9、10、11
    - 验证 stdio 模式可启动与工具映射正确
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 5. API 服务模式
  - [x] 5.1 实现 SessionManager（`excelmanus/session.py`）
    - 并发安全（锁保护）
    - TTL 清理、最大会话数限制
    - _Requirements: 5.8, 5.9, 5.10, 6.7_

  - [x] 5.2 实现 API Server（`excelmanus/api.py`）
    - 端点：POST `/api/v1/chat`、DELETE `/api/v1/sessions/{session_id}`、GET `/api/v1/health`
    - 集成 SessionManager 与 AgentEngine
    - 全局异常处理（返回 error_id，不暴露堆栈）
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [x] 5.3 编写 API 测试（核心必做）
    - Property 12、13、14、15、18、20
    - 覆盖会话复用、会话删除、TTL 过期、容量上限
    - _Requirements: 5.2, 5.3, 5.4, 5.6, 5.7, 5.8, 5.9, 5.10_

- [x] 6. CLI 交互模式
  - [x] 6.1 实现 CLI Interface（`excelmanus/cli.py`）
    - Rich 欢迎信息、加载动画、Markdown 输出
    - 命令：`/help`、`/history`、`/clear`、`exit/quit/Ctrl+C`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8_

  - [x] 6.2 编写 CLI 单元测试
    - 命令解析与退出流程
    - _Requirements: 4.5, 4.6, 4.7, 4.8_

- [x] 7. 集成与收尾
  - [x] 7.1 编写入口与部署文件
    - `excelmanus/__main__.py`、API 启动函数、MCP stdio 启动函数
    - Dockerfile（`python:3.10-slim`）
    - _Requirements: 7.2, 7.7, 3.5_

  - [x] 7.2 编写 README.md
    - 安装方式、CLI/API/MCP 三模式运行说明
    - 配置项与 Skill 扩展指南
    - 安全边界说明（`WORKSPACE_ROOT`）
    - _Requirements: 7.1, 8.1, 8.2_

  - [x] 7.3 全量验证与发布前检查
    - 执行全量测试并生成覆盖率报告
    - 验证日志脱敏、会话治理、安全边界
    - _Requirements: 1-8 全覆盖_

## 备注

- 核心路径（Config/Registry/Engine/MCP/API）的属性测试均为必做
- 如需加速 MVP，可将 CLI 视觉细节与部分高阶图表测试延后，但不得跳过核心测试
- 属性测试默认每项至少 100 次迭代
- 每个里程碑结束后执行一次回归测试，确保增量正确性
