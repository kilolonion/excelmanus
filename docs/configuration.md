# 配置参考

优先级：非空进程环境变量 > 项目根 `.env` > `$EXCELMANUS_HOME/config.env` > 默认值。

空的 `KEY=`（包括从 `.env.example` 拷出来的空行）**不会**挡住正式仓里已经保存的 Key。

前端设置页 / 导入配置写入的环境项落在 **`$EXCELMANUS_HOME/config.env`**（默认 `~/.excelmanus/config.env`）。项目根 `.env` 只是开发便利文件；若该文件已存在，保存时会顺带同步一份。

模型档案的 API Key 加密后存在主数据库，Fernet 密钥在 `$EXCELMANUS_HOME/data/.secret_key`。这两处必须在同一持久卷上，否则重启后 Key 无法解密。

## 基础配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_API_KEY` | LLM API Key（必填） | — |
| `EXCELMANUS_BASE_URL` | LLM API 地址（必填） | — |
| `EXCELMANUS_MODEL` | 模型名称（必填；Gemini 可从 BASE_URL 自动提取） | — |
| `EXCELMANUS_PROTOCOL` | 模型协议类型（`auto`/`openai`/`openai_responses`/`anthropic`/`gemini`） | `auto` |
| `EXCELMANUS_MAX_ITERATIONS` | 本轮 LLM 回合与工具调用上限（并行工具各计 1 次） | `50` |
| `EXCELMANUS_MAX_CONSECUTIVE_FAILURES` | 连续失败熔断阈值 | `6` |
| `EXCELMANUS_SESSION_TTL_SECONDS` | API 会话空闲超时（秒） | `1800` |
| `EXCELMANUS_MAX_SESSIONS` | API 最大并发会话数 | `1000` |
| `EXCELMANUS_HOME` | 持久化根目录（`config.env`、默认数据库、加密密钥） | `~/.excelmanus` |
| `EXCELMANUS_WORKSPACE_ROOT` | 文件访问白名单根目录 | `.` |
| `EXCELMANUS_DATA_ROOT` | 集中数据目录（上传/密钥文件） | `{EXCELMANUS_HOME}/data` |
| `EXCELMANUS_DEPLOY_MODE` | 部署模式（`auto`/`standalone`/`server`），`auto` 与未知值均为 standalone；`server` 必须显式指定 | `auto` |
| `EXCELMANUS_LOG_LEVEL` | 日志级别 | `INFO` |
| `EXCELMANUS_CORS_ALLOW_ORIGINS` | API CORS 允许来源（逗号分隔） | `http://localhost:3000` |
| `EXCELMANUS_MAX_CONTEXT_TOKENS` | 对话上下文 token 上限 | `128000` |
| `EXCELMANUS_PROMPT_CACHE_KEY_ENABLED` | 向 API 发送 prompt_cache_key 提升缓存命中率 | `true` |
| `EXCELMANUS_CLI_LAYOUT_MODE` | CLI 布局模式（`dashboard`/`classic`） | `dashboard` |

## Skillpack 与路由配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_SKILLS_SYSTEM_DIR` | 内置 Skillpacks 目录 | `excelmanus/skillpacks/system` |
| `EXCELMANUS_SKILLS_USER_DIR` | 用户级 Skillpacks 目录 | `~/.excelmanus/skillpacks` |
| `EXCELMANUS_SKILLS_PROJECT_DIR` | 项目级 Skillpacks 目录 | `<workspace_root>/.excelmanus/skillpacks` |
| `EXCELMANUS_SKILLS_CONTEXT_CHAR_BUDGET` | 技能正文字符预算（0 表示不限制） | `12000` |
| `EXCELMANUS_SKILLS_DISCOVERY_ENABLED` | 是否启用通用目录发现 | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_SCAN_WORKSPACE_ANCESTORS` | 是否扫描 cwd→workspace 祖先链 `.agents/skills` | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_AGENTS` | 是否发现 `.agents/skills` | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_SCAN_EXTERNAL_TOOL_DIRS` | 是否发现外部工具目录 | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_EXTRA_DIRS` | 额外扫描目录（逗号分隔） | 空 |
| `EXCELMANUS_CLAWHUB_ENABLED` | 是否启用 ClawHub 技能市场 | `true` |
| `EXCELMANUS_CLAWHUB_REGISTRY_URL` | ClawHub 注册中心 URL | `https://clawhub.ai` |
| `EXCELMANUS_CLAWHUB_PREFER_CLI` | ClawHub 优先使用 CLI 安装 | `true` |
| `EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS` | 工具结果全局硬截断长度（0 表示不限制） | `12000` |

## Subagent 配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_LARGE_EXCEL_THRESHOLD_BYTES` | 触发大文件 subagent 委派提示的阈值（字节） | `8388608` |
| `EXCELMANUS_SUBAGENT_ENABLED` | 是否启用 subagent 执行 | `true` |
| `EXCELMANUS_SUBAGENT_MAX_ITERATIONS` | 子代理循环的 LLM 回合与工具调用上限 | `120` |
| `EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES` | subagent 连续失败熔断阈值 | `6` |
| `EXCELMANUS_SUBAGENT_TIMEOUT_SECONDS` | 单个子代理执行超时（秒） | `600` |
| `EXCELMANUS_PARALLEL_SUBAGENT_MAX` | 并行子代理最大并发数 | `3` |
| `EXCELMANUS_PARALLEL_READONLY_TOOLS` | 同一轮次相邻只读工具并发执行 | `true` |
| `EXCELMANUS_SUBAGENT_USER_DIR` | 用户级 subagent 目录 | `~/.excelmanus/agents` |
| `EXCELMANUS_SUBAGENT_PROJECT_DIR` | 项目级 subagent 目录 | `<workspace_root>/.excelmanus/agents` |

## 上下文自动压缩（Compaction）

对话超阈值时用当前激活模型压缩早期对话，后台静默执行，不阻塞主链路。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_COMPACTION_ENABLED` | 是否启用自动压缩 | `true` |
| `EXCELMANUS_COMPACTION_THRESHOLD_RATIO` | 触发压缩的上下文占比阈值 | `0.85` |
| `EXCELMANUS_COMPACTION_KEEP_RECENT_TURNS` | 压缩时保留的最近轮数 | `5` |
| `EXCELMANUS_COMPACTION_MAX_SUMMARY_TOKENS` | 压缩摘要最大 token 数 | `1500` |
| `EXCELMANUS_SUMMARIZATION_ENABLED` | 旧式对话历史摘要（compaction 之外的第二层，默认关） | `false` |
| `EXCELMANUS_SUMMARIZATION_THRESHOLD_RATIO` | 摘要触发阈值 | `0.8` |
| `EXCELMANUS_SUMMARIZATION_KEEP_RECENT_TURNS` | 摘要保留最近轮数 | `3` |

## Hook 配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_HOOKS_COMMAND_ENABLED` | 是否允许 `command` hook 执行 | `false` |
| `EXCELMANUS_HOOKS_COMMAND_ALLOWLIST` | `command` hook 白名单前缀（逗号分隔） | 空 |
| `EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS` | `command` hook 超时（秒） | `10` |
| `EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS` | hook 输出截断长度 | `32000` |

## 路由行为

- 工具 schema 在每轮请求前构建（元工具 + domain 工具）。`plan` 与 `write` 看到同一套工具；看见写入工具不等于可以改表。
- 技能靠 user-role 目录快照。模型调用 `skill` 加载正文；用户 `/name` 手势也会注入 `<skill-invocation>`。二者都不改工具可见性。

## System Message 模式

`EXCELMANUS_SYSTEM_MESSAGE_MODE`（默认 `auto`）：

- `replace`：多条 system 分段注入。
- `merge`：合并为单条 system。
- `auto`：默认先走 `replace`，遇到 provider 的多 system 兼容错误时自动回退到 `merge`。

## 多模型

> **注意**：`EXCELMANUS_MODELS` 环境变量已废弃。模型档案已迁移至数据库管理，通过 Web 设置页面或 `/model` 命令操作。首次启动时若存在此环境变量会自动迁移到数据库。

- 只保留一个激活模型。`/model <name>` 切换当前激活档案。
- 对话、子代理、上下文压缩、记忆提取都使用该激活模型。

## 视觉配置

图片只交给当前激活模型阅读。无视觉时拒绝附件；有视觉时用 `read_image` 或工作台附件注入，再由模型产出 `WorkbookSpec` 并调用 `edit_spreadsheet(workbook_spec=)` 建表。没有独立视觉流水线，也没有附属 VLM 描述。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_MAIN_MODEL_VISION` | 激活模型视觉能力（`auto`/`true`/`false`） | `auto` |
| `EXCELMANUS_IMAGE_KEEP_ROUNDS` | 图片在上下文中保持完整 base64 的最小轮次 | `3` |

## 备份沙盒

备份 overlay 已移除。写入落在用户路径；历史在 `.excelmanus/revisions/`。

## 代码策略引擎配置

对 `run_code` 执行的代码进行静态分析，按安全级别自动分流审批。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_CODE_POLICY_ENABLED` | 是否启用代码策略引擎 | `true` |
| `EXCELMANUS_CODE_POLICY_GREEN_AUTO` | Green 级（安全）代码自动批准 | `true` |
| `EXCELMANUS_CODE_POLICY_YELLOW_AUTO` | Yellow 级代码自动批准（默认关；打开后仍不会自动批准文件系统写入） | `false` |
| `EXCELMANUS_CODE_POLICY_EXTRA_SAFE` | 额外安全模块白名单（逗号分隔） | 空 |
| `EXCELMANUS_CODE_POLICY_EXTRA_BLOCKED` | 额外阻断模块黑名单（逗号分隔） | 空 |

## Embedding 语义检索配置

为持久记忆、文件清单和错误解决方案提供语义检索。需独立配置 embedding API，并显式设置 `EXCELMANUS_EMBEDDING_ENABLED=true` 才会构造客户端。

启用后激活：
- **SemanticRegistry** — 语义相关文件摘要注入
- **ErrorSolutionStore** — 错误→解决方案向量索引，持久化到 `.excelmanus/error_solutions/`
- **记忆语义去重** — 新提取记忆与已有条目 cosine 比对，过滤重复
- **智能上下文压缩** — 压缩时按语义相关性评分差异化截断（高相关消息保留更多）

所有集成点通过共享 `_embedding_client` 统一初始化。`embedding_enabled=false` 时全部降级为无操作。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_EMBEDDING_ENABLED` | 是否启用语义检索（须显式打开） | `false` |
| `EXCELMANUS_EMBEDDING_API_KEY` | Embedding API Key | — |
| `EXCELMANUS_EMBEDDING_BASE_URL` | Embedding API Base URL | — |
| `EXCELMANUS_EMBEDDING_MODEL` | Embedding 模型名称 | `text-embedding-3-small` |
| `EXCELMANUS_EMBEDDING_DIMENSIONS` | 向量维度 | `1536` |
| `EXCELMANUS_EMBEDDING_TIMEOUT_SECONDS` | 请求超时（秒） | `30.0` |
| `EXCELMANUS_MEMORY_SEMANTIC_TOP_K` | 记忆语义检索 Top-K | `10` |
| `EXCELMANUS_MEMORY_SEMANTIC_THRESHOLD` | 记忆语义检索阈值 | `0.3` |
| `EXCELMANUS_MEMORY_SEMANTIC_FALLBACK_RECENT` | 语义检索失败时回退最近条数 | `5` |
| `EXCELMANUS_REGISTRY_SEMANTIC_TOP_K` | 文件注册表语义检索 Top-K | `5` |
| `EXCELMANUS_REGISTRY_SEMANTIC_THRESHOLD` | 文件注册表语义检索阈值 | `0.25` |

## 持久记忆

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_MEMORY_ENABLED` | 全局记忆开关 | `true` |
| `EXCELMANUS_MEMORY_DIR` | 记忆目录 | `~/.excelmanus/memory` |
| `EXCELMANUS_MEMORY_AUTO_LOAD_LINES` | 自动加载行数 | `200` |
| `EXCELMANUS_MEMORY_AUTO_EXTRACT_INTERVAL` | 每 N 轮后台静默提取记忆（0 = 禁用） | `0` |

主题文件：`file_patterns.md`、`user_prefs.md`、`error_solutions.md`、`general.md`。
核心文件 `MEMORY.md` 保存时会与主题文件同步写入，用于会话启动自动加载。

启用 Embedding 后，记忆检索自动切换为语义匹配模式，新提取的记忆会与已有条目做 cosine 语义去重（阈值 0.88），自动过滤冗余。

## Playbook（战术手册）

可选 SQLite 手册，默认关闭。开启后用 `/playbook` 查阅条目；默认路径不自动归纳、也不按轮注入。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_PLAYBOOK_ENABLED` | 启用 Playbook 存储 | `false` |
| `EXCELMANUS_PLAYBOOK_DB_PATH` | Playbook 数据库路径（空则使用默认路径） | 空 |
| `EXCELMANUS_PLAYBOOK_MAX_BULLETS` | Playbook 最大条目数 | `500` |

## 内置搜索引擎

内置三个搜索引擎 MCP Server（Exa / Tavily / Brave），无需手动配置 `mcp.json`。

**启用策略**（"默认+可选"模式）：
1. `EXCELMANUS_EXA_SEARCH=false` → 禁用全部内置搜索引擎
2. `EXCELMANUS_SEARCH_DEFAULT` 指定的默认引擎始终启用
3. 非默认引擎仅在配置了对应 API 密钥后额外启用
4. 默认引擎为 tavily/brave 但缺少 API 密钥或 Node.js → 自动降级回 exa

Tavily 和 Brave 通过 `npx` 启动（stdio 传输），需要系统安装 Node.js。Exa 通过 HTTP 连接，无需 Node.js。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_EXA_SEARCH` | 内置搜索引擎总开关 | `true` |
| `EXCELMANUS_SEARCH_DEFAULT` | 默认搜索引擎（`exa` / `tavily` / `brave`） | `exa` |
| `EXCELMANUS_EXA_API_KEY` | Exa API 密钥（可选，提升搜索质量和速率限制） | — |
| `EXCELMANUS_TAVILY_API_KEY` | Tavily API 密钥（配置后额外启用 Tavily 搜索） | — |
| `EXCELMANUS_BRAVE_API_KEY` | Brave API 密钥（配置后额外启用 Brave 搜索） | — |

用户可在 `mcp.json` 中配置同名 Server（如 `"exa"`）覆盖内置配置。

## MCP 配置

自定义 MCP Server 写在工作区或 `~/.excelmanus/mcp.json`（也可用 `EXCELMANUS_MCP_CONFIG` 指定路径）：

- `stdio` 条目按 `command`/`args` 启动；Tavily / Brave 走 `npx`，需要本机 Node.js。
- Exa 走 HTTP（`streamable_http` 或 SSE），不依赖 `npx`。
- 进程状态目录默认 `<workspace>/.excelmanus/mcp`，可用 `EXCELMANUS_MCP_STATE_DIR` 覆盖。

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
- CI：`.github/workflows/security-secrets.yml`

## 统一数据库

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_DB_PATH` | SQLite 数据库路径（聊天记录、记忆、向量、审批均存于此） | `~/.excelmanus/excelmanus.db` |

## 聊天记录持久化

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_CHAT_HISTORY_ENABLED` | 是否启用聊天记录持久化 | `true` |

## 工具参数 Schema 校验

对 LLM 返回的工具调用参数进行 JSON Schema 级校验，分三级模式。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_TOOL_SCHEMA_VALIDATION_MODE` | `off`（关闭）/ `shadow`（仅日志不阻断）/ `enforce`（阻断并返回错误） | `off` |
| `EXCELMANUS_TOOL_SCHEMA_VALIDATION_CANARY_PERCENT` | `enforce` 模式灰度比例（0~100），100 = 全量 | `100` |
| `EXCELMANUS_TOOL_SCHEMA_STRICT_PATH` | 严格路径策略：路径参数必须为相对路径且禁止 `..` | `false` |

## 会话快照

每轮结束后保存 SessionState / 任务列表到 `session_checkpoints` 表，用于会话恢复。这不是文件检查点；文件历史在 `.excelmanus/revisions/`。

## 代码沙盒

`run_code` 只走本机子进程围栏：禁网络、禁起进程、禁出工作区。产品安装也不再提供 Compose / 镜像轨。

## Thinking（推理深度）

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_THINKING_EFFORT` | 推理深度级别（`none`/`minimal`/`low`/`medium`/`high`/`xhigh`/`max`） | `medium` |
| `EXCELMANUS_THINKING_BUDGET` | 精确 token 预算（> 0 时覆盖 effort 换算值） | `0` |

## OpenAI Responses API

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_USE_RESPONSES_API` | 设为 `1` 启用 Responses API（`/responses` 端点），仅对非 Gemini/Claude 的 OpenAI 兼容 URL 生效 | `0` |

## 加密配置

敏感字段（模型 API Key、OAuth Access Token 等）的加密存储。需安装 `cryptography` 依赖。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_SECRET_KEY` | Fernet 加密密钥种子（留空则自动在 `{EXCELMANUS_HOME}/data/.secret_key` 生成） | 自动生成 |

密钥派生优先级：
1. `EXCELMANUS_SECRET_KEY` 环境变量（SHA-256 派生）
2. `{EXCELMANUS_HOME}/data/.secret_key` 自动生成（首次启动时创建，文件权限 600）
3. 旧路径 `~/.excelmanus/data/.secret_key`（若存在则复制到正式路径）
4. 均不可用时，加密组件不启用（仅限开发环境）

## 单用户工作区

一份进程只有一份 data home（SQLite 聊天库、记忆、MCP 配置、模型凭证），**不是**多租户。用户可以把多个本机文件夹登记为工作区：每个对话绑定其中一个文件夹，同一文件夹下的多条对话共享该目录里的文件。Agent 的 cwd、文件守卫、版本与 registry 扫描根跟随当前会话的文件夹；记忆和 MCP 仍是进程级共享，不会按文件夹隔离。

默认工作区是 `EXCELMANUS_DATA_ROOT`（若设置）或 `EXCELMANUS_WORKSPACE_ROOT`。对话 tab 可以收编已经存在的本机目录，不会在目标路径上 mkdir，也不会把聊天记录写到 xlsx 旁边。

`EXCELMANUS_AUTH_ENABLED` / `NEXT_PUBLIC_AUTH_ENABLED` / `EXCELMANUS_SESSION_ISOLATION` 已移除。Codex 订阅 OAuth 仍可用（进程级，不绑定登录用户）。

后端默认监听 `127.0.0.1`。若绑定非 loopback 地址（LAN 或公网），必须设置 `EXCELMANUS_MANAGE_TOKEN`（至少 16 字符）；令牌一旦配置，除健康检查外全部 `/api/v1` 要求 `Authorization: Bearer`。服务器模式请让 Nginx 反代到 `127.0.0.1:8000`，不要把应用端口直接暴露到 `0.0.0.0`。

### 旧版 `users/` 手动搬迁

不要自动合并多个 `users/{id}`。若本地还留着旧隔离目录：

1. 选出**唯一**要继续用的 `users/{id}/`。
2. 把其中的工作区文件拷到当前 `data_root` / `workspace_root`。
3. 各用户目录下的 `data.db` **不会**自动导入主库；聊天记录与记忆需自行决定是否手工迁移。
4. FileRegistry 扫描会跳过名为 `users` 的目录，避免把归档残骸扫进工作区。
