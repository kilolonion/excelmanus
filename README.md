# ExcelManus

> ⚠️ **WIP (Work In Progress)** — 本项目仍在积极开发中，功能和 API 尚未稳定，可能随时发生破坏性变更。欢迎试用和反馈，但请勿用于生产环境。

基于大语言模型的 Excel 智能代理框架（`窗口感知层 + Tools + Skillpacks` 三层架构）。

- **窗口感知层（Window Perception）**：多文件虚拟桌面，自动生命周期管理与上下文注入
- **Tools**：基础能力执行层（工具函数 + schema + 安全边界 + 分层披露）
- **Skillpacks**：纯知识注入层（`SKILL.md` 元数据 + 路由 + 领域指引）

支持两种运行模式：

- **CLI 模式**：终端交互（Dashboard / Classic 双布局）
- **API 模式**：REST API（SSE 流式响应）

## 快速开始

**1. 安装**

```bash
pip install .          # 或 pip install -e ".[dev]" 用于开发
```

要求 Python >= 3.10。

**2. 配置**

在项目根目录创建 `.env`（仅需 3 项）：

```dotenv
EXCELMANUS_API_KEY=your-api-key
EXCELMANUS_BASE_URL=https://your-llm-endpoint/v1
EXCELMANUS_MODEL=your-model-id
```

**3. 运行**

```bash
# CLI 交互模式
excelmanus

# 或启动 API 服务
excelmanus-api
```

进入 CLI 后直接输入自然语言指令即可，例如：

```
> 读取 sales.xlsx 前10行
> 把 A 列金额求和写到 B1
> 生成一张柱状图保存为 chart.png
```

常用斜杠命令：`/help`（帮助）、`/skills`（技能列表）、`/model list`（切换模型）、`/clear`（清空对话）、`exit`（退出）。

更多配置和功能详见下方完整文档。

---

## 安装

```bash
pip install .
pip install -e ".[dev]"
```

要求 Python >= 3.10。

### Docker

```bash
docker build -t excelmanus .
docker run --env-file .env -v $(pwd)/workspace:/workspace excelmanus
```

## 配置

优先级：环境变量 > `.env` > 默认值。

最小可运行 `.env` 示例：

```dotenv
EXCELMANUS_API_KEY=your-api-key
EXCELMANUS_BASE_URL=https://your-llm-endpoint/v1
EXCELMANUS_MODEL=your-model-id
# 可选：EXCELMANUS_MODELS=[{"name":"default","model":"your-model-id","api_key":"...","base_url":"..."}]
```

### 基础配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_API_KEY` | LLM API Key（必填） | — |
| `EXCELMANUS_BASE_URL` | LLM API 地址（必填，可由 `EXCELMANUS_MODELS` 首项继承） | — |
| `EXCELMANUS_MODEL` | 模型名称（必填，可由 `EXCELMANUS_MODELS` 首项继承；Gemini 可从 BASE_URL 自动提取） | — |
| `EXCELMANUS_MAX_ITERATIONS` | Agent 最大迭代轮数 | `50` |
| `EXCELMANUS_MAX_CONSECUTIVE_FAILURES` | 连续失败熔断阈值 | `6` |
| `EXCELMANUS_SESSION_TTL_SECONDS` | API 会话空闲超时（秒） | `1800` |
| `EXCELMANUS_MAX_SESSIONS` | API 最大并发会话数 | `1000` |
| `EXCELMANUS_WORKSPACE_ROOT` | 文件访问白名单根目录 | `.` |
| `EXCELMANUS_LOG_LEVEL` | 日志级别 | `INFO` |
| `EXCELMANUS_EXTERNAL_SAFE_MODE` | 对外安全模式（隐藏思考/工具细节与路由元信息） | `true` |
| `EXCELMANUS_CORS_ALLOW_ORIGINS` | API CORS 允许来源（逗号分隔） | `http://localhost:5173` |
| `EXCELMANUS_MAX_CONTEXT_TOKENS` | 对话上下文 token 上限 | `128000` |
| `EXCELMANUS_PROMPT_CACHE_KEY_ENABLED` | 向 API 发送 prompt_cache_key 提升缓存命中率 | `true` |
| `EXCELMANUS_CLI_LAYOUT_MODE` | CLI 布局模式（`dashboard`/`classic`） | `dashboard` |

### Skillpack 与路由配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_SKILLS_SYSTEM_DIR` | 内置 Skillpacks 目录 | `excelmanus/skillpacks/system` |
| `EXCELMANUS_SKILLS_USER_DIR` | 用户级 Skillpacks 目录 | `~/.excelmanus/skillpacks` |
| `EXCELMANUS_SKILLS_PROJECT_DIR` | 项目级 Skillpacks 目录 | `<workspace_root>/.excelmanus/skillpacks` |
| `EXCELMANUS_SKILLS_CONTEXT_CHAR_BUDGET` | 技能正文字符预算（0 表示不限制） | `12000` |
| `EXCELMANUS_SKILLS_DISCOVERY_ENABLED` | 是否启用通用目录发现 | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_SCAN_WORKSPACE_ANCESTORS` | 是否扫描 cwd→workspace 祖先链 `.agents/skills` | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_AGENTS` | 是否发现 `.agents/skills` | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_SCAN_EXTERNAL_TOOL_DIRS` | 是否发现外部工具目录（`.claude/skills`/`~/.claude/skills`、`.openclaw/skills`/`~/.openclaw/skills`） | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_EXTRA_DIRS` | 额外扫描目录（逗号分隔） | 空 |
| `EXCELMANUS_MODELS` | 可切换模型档案（JSON 数组，供 `/model` 使用） | 空 |
| `EXCELMANUS_ROUTER_API_KEY` | 路由模型 API Key（未设置时回退主配置） | — |
| `EXCELMANUS_ROUTER_BASE_URL` | 路由模型 Base URL（未设置时回退主配置） | — |
| `EXCELMANUS_ROUTER_MODEL` | 路由模型名称（设置后与主模型解耦） | — |
| `EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS` | 工具结果全局硬截断长度（0 表示不限制） | `12000` |

### Subagent 配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_LARGE_EXCEL_THRESHOLD_BYTES` | 触发大文件 subagent 委派提示的阈值（字节） | `8388608` |
| `EXCELMANUS_SUBAGENT_ENABLED` | 是否启用 subagent 执行 | `true` |
| `EXCELMANUS_AUX_MODEL` | 辅助模型（统一用于 subagent 默认模型与窗口顾问模型；为空时回退主模型） | — |
| `EXCELMANUS_SUBAGENT_MAX_ITERATIONS` | subagent 最大迭代轮数 | `120` |
| `EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES` | subagent 连续失败熔断阈值 | `6` |
| `EXCELMANUS_SUBAGENT_USER_DIR` | 用户级 subagent 目录 | `~/.excelmanus/agents` |
| `EXCELMANUS_SUBAGENT_PROJECT_DIR` | 项目级 subagent 目录 | `<workspace_root>/.excelmanus/agents` |

### 上下文自动压缩（Compaction）

对话超阈值时用辅助模型压缩早期对话，后台静默执行，不阻塞主链路。需配置 `EXCELMANUS_AUX_MODEL`。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_COMPACTION_ENABLED` | 是否启用自动压缩 | `true` |
| `EXCELMANUS_COMPACTION_THRESHOLD_RATIO` | 触发压缩的上下文占比阈值 | `0.85` |
| `EXCELMANUS_COMPACTION_KEEP_RECENT_TURNS` | 压缩时保留的最近轮数 | `5` |
| `EXCELMANUS_COMPACTION_MAX_SUMMARY_TOKENS` | 压缩摘要最大 token 数 | `1500` |
| `EXCELMANUS_SUMMARIZATION_ENABLED` | 是否启用对话历史摘要 | `true` |
| `EXCELMANUS_SUMMARIZATION_THRESHOLD_RATIO` | 摘要触发阈值 | `0.8` |
| `EXCELMANUS_SUMMARIZATION_KEEP_RECENT_TURNS` | 摘要保留最近轮数 | `3` |

### Hook 配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_HOOKS_COMMAND_ENABLED` | 是否允许 `command` hook 执行 | `false` |
| `EXCELMANUS_HOOKS_COMMAND_ALLOWLIST` | `command` hook 白名单前缀（逗号分隔） | 空 |
| `EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS` | `command` hook 超时（秒） | `10` |
| `EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS` | hook 输出截断长度 | `32000` |

### 路由行为

- 所有工具始终可见（core 工具完整 schema，extended 工具摘要 schema）。
- LLM 通过 `expand_tools` 展开指定类别获取完整参数后即可调用。
- `activate_skill` 用于注入领域知识指引（纯知识注入，不控制工具可见性）。

### System Message 模式

`EXCELMANUS_SYSTEM_MESSAGE_MODE`（默认 `auto`）：

- `replace`：多条 system 分段注入。
- `merge`：合并为单条 system。
- `auto`：默认先走 `replace`，遇到 provider 的多 system 兼容错误时自动回退到 `merge`。

### 多模型与路由模型

- `/model <name>` 切换主对话模型（`EXCELMANUS_MODELS` 中的 profile）。
- 未设置 `EXCELMANUS_ROUTER_MODEL` 时，路由模型跟随 `/model` 切换。
- 设置了 `EXCELMANUS_ROUTER_MODEL` 时，路由模型保持独立，不受 `/model` 影响。

### 窗口感知层配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_WINDOW_PERCEPTION_ENABLED` | 是否启用窗口感知层 | `true` |
| `EXCELMANUS_WINDOW_PERCEPTION_SYSTEM_BUDGET_TOKENS` | 系统注入窗口预算 | `3000` |
| `EXCELMANUS_WINDOW_PERCEPTION_TOOL_APPEND_TOKENS` | 工具返回附加预算 | `500` |
| `EXCELMANUS_WINDOW_PERCEPTION_MAX_WINDOWS` | 最大窗口数 | `6` |
| `EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_ROWS` | 默认视口行数 | `25` |
| `EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_COLS` | 默认视口列数 | `10` |
| `EXCELMANUS_WINDOW_PERCEPTION_MINIMIZED_TOKENS` | 最小化窗口预算 | `80` |
| `EXCELMANUS_WINDOW_PERCEPTION_BACKGROUND_AFTER_IDLE` | 进入后台阈值（idle turn） | `1` |
| `EXCELMANUS_WINDOW_PERCEPTION_SUSPEND_AFTER_IDLE` | 进入挂起阈值（idle turn） | `3` |
| `EXCELMANUS_WINDOW_PERCEPTION_TERMINATE_AFTER_IDLE` | 进入关闭阈值（idle turn） | `5` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_MODE` | 生命周期顾问模式（`rules`/`hybrid`） | `rules` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TIMEOUT_MS` | 小模型顾问超时（毫秒） | `800` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_WINDOW_COUNT` | 触发小模型的窗口数阈值 | `3` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_TURN` | 触发小模型的对话轮次阈值 | `4` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_PLAN_TTL_TURNS` | 小模型计划有效轮数（TTL） | `2` |

顾问模式：
- `rules`：仅使用确定性规则（无小模型调用）。
- `hybrid`：规则兜底 + 异步小模型缓存，失败或超时自动回退规则，不阻塞主链路。

窗口感知高级配置：

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_WINDOW_RETURN_MODE` | 工具返回模式（`unified`/`anchored`/`enriched`/`adaptive`） | `adaptive` |
| `EXCELMANUS_ADAPTIVE_MODEL_MODE_OVERRIDES` | adaptive 模式下按模型覆盖返回模式（JSON object） | 空 |
| `EXCELMANUS_WINDOW_FULL_MAX_ROWS` | 全量窗口最大行数 | `25` |
| `EXCELMANUS_WINDOW_FULL_TOTAL_BUDGET_TOKENS` | 全量窗口 token 预算 | `500` |
| `EXCELMANUS_WINDOW_DATA_BUFFER_MAX_ROWS` | 数据缓冲最大行数 | `200` |
| `EXCELMANUS_WINDOW_INTENT_ENABLED` | 是否启用意图识别 | `true` |
| `EXCELMANUS_WINDOW_INTENT_STICKY_TURNS` | 意图粘滞轮数 | `3` |
| `EXCELMANUS_WINDOW_INTENT_REPEAT_WARN_THRESHOLD` | 重复意图警告阈值 | `2` |
| `EXCELMANUS_WINDOW_INTENT_REPEAT_TRIP_THRESHOLD` | 重复意图熔断阈值 | `3` |
| `EXCELMANUS_WINDOW_RULE_ENGINE_VERSION` | 窗口规则引擎版本（`v1`/`v2`） | `v1` |
| `EXCELMANUS_WINDOW_ADVISOR_API_KEY` | 顾问独立 API Key（可选） | — |
| `EXCELMANUS_WINDOW_ADVISOR_BASE_URL` | 顾问独立 Base URL（可选） | — |

### VLM（视觉语言模型）配置

支持图片识别与视觉增强描述。可独立配置 VLM 模型，未配置时回退主模型。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_VLM_API_KEY` | VLM API Key（可选） | — |
| `EXCELMANUS_VLM_BASE_URL` | VLM Base URL（可选） | — |
| `EXCELMANUS_VLM_MODEL` | VLM 模型名称（可选） | — |
| `EXCELMANUS_VLM_TIMEOUT_SECONDS` | VLM 请求超时（秒） | `300` |
| `EXCELMANUS_VLM_MAX_RETRIES` | VLM 最大重试次数 | `1` |
| `EXCELMANUS_VLM_RETRY_BASE_DELAY_SECONDS` | VLM 重试基础延迟（秒） | `5.0` |
| `EXCELMANUS_VLM_IMAGE_MAX_LONG_EDGE` | 图片长边上限（px） | `2048` |
| `EXCELMANUS_VLM_IMAGE_JPEG_QUALITY` | JPEG 压缩质量 | `92` |
| `EXCELMANUS_VLM_ENHANCE` | VLM 增强描述总开关 | `true` |
| `EXCELMANUS_MAIN_MODEL_VISION` | 主模型视觉能力（`auto`/`true`/`false`） | `auto` |

CLI 中使用 `@img <path>` 语法附加图片：

```
读取这张截图中的表格 @img screenshot.png
```

### 备份沙盒配置

默认开启，所有文件写操作自动在 `outputs/backups/` 保留副本，支持回滚。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_BACKUP_ENABLED` | 是否启用备份沙盒 | `true` |

### 代码策略引擎配置

对 `run_code` 执行的代码进行静态分析，按安全级别自动分流审批。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_CODE_POLICY_ENABLED` | 是否启用代码策略引擎 | `true` |
| `EXCELMANUS_CODE_POLICY_GREEN_AUTO` | Green 级（安全）代码自动批准 | `true` |
| `EXCELMANUS_CODE_POLICY_YELLOW_AUTO` | Yellow 级（需审计）代码自动批准 | `true` |
| `EXCELMANUS_CODE_POLICY_EXTRA_SAFE` | 额外安全模块白名单（逗号分隔） | 空 |
| `EXCELMANUS_CODE_POLICY_EXTRA_BLOCKED` | 额外阻断模块黑名单（逗号分隔） | 空 |

### Embedding 语义检索配置

为持久记忆和文件清单提供语义检索能力。需独立配置 embedding API，配置后自动启用。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_EMBEDDING_ENABLED` | 是否启用语义检索（配置 API 后自动启用） | `false` |
| `EXCELMANUS_EMBEDDING_API_KEY` | Embedding API Key | — |
| `EXCELMANUS_EMBEDDING_BASE_URL` | Embedding API Base URL | — |
| `EXCELMANUS_EMBEDDING_MODEL` | Embedding 模型名称 | `text-embedding-v3` |
| `EXCELMANUS_EMBEDDING_DIMENSIONS` | 向量维度 | `1536` |
| `EXCELMANUS_EMBEDDING_TIMEOUT_SECONDS` | 请求超时（秒） | `30.0` |
| `EXCELMANUS_MEMORY_SEMANTIC_TOP_K` | 记忆语义检索 Top-K | `10` |
| `EXCELMANUS_MEMORY_SEMANTIC_THRESHOLD` | 记忆语义检索阈值 | `0.3` |
| `EXCELMANUS_MEMORY_SEMANTIC_FALLBACK_RECENT` | 语义检索失败时回退最近条数 | `5` |
| `EXCELMANUS_MANIFEST_SEMANTIC_TOP_K` | 文件清单语义检索 Top-K | `5` |
| `EXCELMANUS_MANIFEST_SEMANTIC_THRESHOLD` | 文件清单语义检索阈值 | `0.25` |

### MCP 配置

项目根目录 `mcp.json` 使用 `scripts/mcp/*.sh` 启动器：

- 首次启动时按固定版本自动安装到 `./.excelmanus/mcp/`
- 后续启动直接复用本地缓存
- 如需强制重装，删除 `./.excelmanus/mcp/` 后重启即可
- 可通过 `EXCELMANUS_MCP_STATE_DIR` 自定义缓存目录

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_MCP_SHARED_MANAGER` | API 会话是否复用共享 MCP 管理器 | `false` |
| `EXCELMANUS_MCP_ENABLE_STREAMABLE_HTTP` | 是否启用 streamable_http transport | `false` |
| `EXCELMANUS_MCP_UNDEFINED_ENV` | 未定义环境变量策略（`keep`/`empty`/`error`） | `keep` |
| `EXCELMANUS_MCP_STRICT_SECRETS` | 明文敏感字段是否阻断加载 | `false` |

`mcp.json` 能力：
- `transport` 支持 `stdio`、`sse`、`streamable_http`。
- 支持在 `args/env/url/headers` 中使用 `$VAR` / `${VAR}` 环境变量引用。
- MCP 仅负责注册 `mcp_*` 工具；Skillpack 负责策略与授权。若 Skillpack 需要 MCP，在 `SKILL.md` 中声明 `required-mcp-servers` / `required-mcp-tools`。

MCP 安全扫描：
- 本地：`scripts/security/scan_secrets.sh`
- pre-commit：`.pre-commit-config.yaml` 内置钩子
- CI：`.github/workflows/secret-scan.yml`

## 使用方式

### CLI

```bash
excelmanus
# 或
python -m excelmanus
```

可用命令：

| 命令 | 说明 |
|---|---|
| `/help` | 帮助 |
| `/history` | 对话历史 |
| `/clear` | 清空对话 |
| `/save` | 保存对话 |
| `/skills [list\|get\|create\|patch\|delete]` | Skillpack 管理 |
| `/subagent [on\|off\|status\|list\|run]` | Subagent 控制 |
| `/fullAccess [on\|off\|status]` | 会话级代码权限 |
| `/accept <id>` / `/reject <id>` / `/undo <id>` | 审批门禁 |
| `/plan [on\|off\|status\|approve\|reject]` | Plan 模式 |
| `/model [list\|<name>]` | 模型切换 |
| `/config [list\|set\|get\|delete]` | MCP 环境变量管理 |
| `/backup [status\|on\|off\|apply\|list]` | 备份沙盒控制 |
| `/mcp` | MCP Server 状态 |
| `/<skill_name> [args...]` | 斜杠直接调用技能 |
| `exit` | 退出 |

输入斜杠命令时支持灰色内联补全与相似度纠错建议。Plan 草案保存于 `.excelmanus/plans/`。


Subagent `memory_scope` 支持 `user` / `project` 两种作用域：
- `user`：`~/.excelmanus/agent-memory/{agent_name}/`
- `project`：`{workspace_root}/.excelmanus/agent-memory/{agent_name}/`

### 持久记忆

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_MEMORY_ENABLED` | 全局记忆开关 | `true` |
| `EXCELMANUS_MEMORY_DIR` | 记忆目录 | `~/.excelmanus/memory` |
| `EXCELMANUS_MEMORY_AUTO_LOAD_LINES` | 自动加载行数 | `200` |

主题文件：`file_patterns.md`、`user_prefs.md`、`error_solutions.md`、`general.md`。
核心文件 `MEMORY.md` 保存时会与主题文件同步写入，用于会话启动自动加载。

启用 Embedding 后，记忆检索自动切换为语义匹配模式（见 [Embedding 语义检索配置](#embedding-语义检索配置)）。

### Accept 门禁与审计

- Tier A（破坏性写入）需 `/accept` 确认，Tier B（可审计写入）默认直行并强制审计。
- 非 `fullAccess` 状态下，Tier A 工具先进入待确认队列。
- MCP 工具仅在白名单内自动批准，非白名单进入待确认队列。
- 已执行的高风险操作在 `outputs/approvals/<id>/` 下保存审计产物：
  - `manifest.json`：`version/approval/execution/artifacts/changes` 分层结构
  - `changes.patch`：文本文件 unified diff
  - `snapshots/`：回滚快照
- 支持回滚的记录可执行 `/undo <id>`，支持进程重启后回滚。

### API

```bash
excelmanus-api
```

| 接口 | 说明 |
|---|---|
| `POST /api/v1/chat` | 发送消息（`message`、`session_id?`） |
| `GET /api/v1/skills` | Skillpack 摘要列表 |
| `GET /api/v1/skills/{name}` | Skillpack 详情（safe mode 下返回摘要） |
| `POST /api/v1/skills` | 创建 Skillpack |
| `PATCH /api/v1/skills/{name}` | 更新 Skillpack |
| `DELETE /api/v1/skills/{name}` | 删除 Skillpack（软删除归档） |
| `DELETE /api/v1/sessions/{session_id}` | 删除会话 |
| `GET /api/v1/health` | 健康检查 |

说明：
- `EXCELMANUS_EXTERNAL_SAFE_MODE=true` 时，Skills 写操作返回 `403`，`tool_calls.arguments` 做脱敏处理。
- Skills 写操作同时接受 `snake_case` 与 `kebab-case` 字段；响应统一使用 `kebab-case`。
- Skillpack 写操作仅允许 project 层。
- SSE `task_update` 事件统一映射 `TASK_LIST_CREATED` 与 `TASK_ITEM_UPDATED`。

示例：

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "读取 sales.xlsx 前10行"}'
```

## Bench 测试框架

内置的自动化测试运行器，用于批量评估 Agent 表现。

```bash
# 运行所有用例
python -m excelmanus.bench --all

# 运行单个 suite
python -m excelmanus.bench --suite bench/cases/suite_01_基础读取类.json

# 单条消息测试
python -m excelmanus.bench --message "读取销售明细前10行"
```

支持特性：
- 单轮 / 多轮对话用例
- 自动断言校验（`bench_validator`）
- 结构化 JSON 日志输出（schema v3）
- `--trace` 模式记录引擎内部交互轨迹（系统提示注入、窗口感知增强等）
- LLM 调用拦截与完整请求/响应记录
- Suite 级并发执行与实时进度面板
- 用例文件位于 `bench/cases/`

## Skillpack 扩展

> 协议 SSOT：以 `docs/skillpack_protocol.md` 为准。

目录结构：`<dir>/<namespace_or_name>/SKILL.md`

`SKILL.md` frontmatter 必填字段：`name`、`description`

可选字段（标准 `kebab-case` 键）：
- `file-patterns`、`resources`、`version`
- `disable-model-invocation`、`user-invocable`、`argument-hint`
- `hooks`、`model`、`metadata`
- `command-dispatch`（`none`/`tool`）、`command-tool`
- `required-mcp-servers`、`required-mcp-tools`

Hook 要点：
- 事件键支持 `PreToolUse` / `preToolUse` / `pre_tool_use` 三种写法。
- 决策优先级：`deny > ask > allow > continue`。
- `command` hook 需 `EXCELMANUS_HOOKS_COMMAND_ENABLED=true`，仅允许单段命令。
- `agent` hook 支持：`agent_name`、`task`、`on_failure`、`inject_summary_as_context`。

命名规范：支持命名空间（如 `team/data-cleaner`），分段正则 `[a-z0-9][a-z0-9._-]{0,63}`，全名最大 255。

加载优先级（高→低）：

1. workspace 目录：`.excelmanus/skillpacks`、`.agents/skills`、`.claude/skills`、`.openclaw/skills`
2. 祖先链 `.agents/skills`（越近越高）
3. 用户目录：`~/.excelmanus/skillpacks`、`~/.claude/skills`、`~/.openclaw/skills`
4. 系统目录：`excelmanus/skillpacks/system`

同名技能按优先级覆盖，最终只保留一个生效版本。frontmatter 兼容 `snake_case` 输入，推荐统一使用 `kebab-case`。

内置 Skillpacks：
- `data_basic`：读取/分析/筛选/转换
- `chart_basic`：图表生成
- `format_basic`：样式调整
- `file_ops`：文件操作
- `sheet_ops`：工作表管理与跨表操作
- `excel_code_runner`：写脚本并运行 Python 处理大体量 Excel

## 安全边界

- 所有文件读写受 `WORKSPACE_ROOT` 限制，路径穿越与符号链接越界会被拒绝
- 代码执行默认受限（`excel_code_runner`），需通过 `/fullAccess` 临时解锁
- `run_code` 使用软沙盒执行（最小环境变量白名单、`-I` 隔离、进程隔离、Unix 资源限制）
- 安全边界由 ToolPolicy 层（Tier A/B 分层审批）统一管控
- 代码策略引擎对 `run_code` 执行静态分析，按 Green/Yellow/Red 三级自动分流
- 备份沙盒默认开启，所有文件写操作在 `outputs/backups/` 保留副本

## 开发

```bash
pip install -e ".[dev]"
pytest
pytest tests/test_skillpack_docs_contract.py -q
```

## 许可证

MIT
