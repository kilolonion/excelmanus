# 配置参考

优先级：环境变量 > `.env` > 默认值。

## 基础配置

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
| `EXCELMANUS_CORS_ALLOW_ORIGINS` | API CORS 允许来源（逗号分隔） | `http://localhost:3000, http://localhost:5173` |
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
| `EXCELMANUS_MODELS` | 可切换模型档案（JSON 数组，供 `/model` 使用） | 空 |
| `EXCELMANUS_AUX_API_KEY` | AUX API Key（路由 + 子代理默认模型 + 窗口顾问） | — |
| `EXCELMANUS_AUX_BASE_URL` | AUX Base URL（未设置时回退主配置） | — |
| `EXCELMANUS_AUX_MODEL` | AUX 模型名称（未设置时回退主模型） | — |
| `EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS` | 工具结果全局硬截断长度（0 表示不限制） | `12000` |

## Subagent 配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_LARGE_EXCEL_THRESHOLD_BYTES` | 触发大文件 subagent 委派提示的阈值（字节） | `8388608` |
| `EXCELMANUS_SUBAGENT_ENABLED` | 是否启用 subagent 执行 | `true` |
| `EXCELMANUS_AUX_MODEL` | 辅助模型（路由 + subagent 默认模型 + 窗口顾问模型） | — |
| `EXCELMANUS_SUBAGENT_MAX_ITERATIONS` | subagent 最大迭代轮数 | `120` |
| `EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES` | subagent 连续失败熔断阈值 | `6` |
| `EXCELMANUS_SUBAGENT_USER_DIR` | 用户级 subagent 目录 | `~/.excelmanus/agents` |
| `EXCELMANUS_SUBAGENT_PROJECT_DIR` | 项目级 subagent 目录 | `<workspace_root>/.excelmanus/agents` |

## 上下文自动压缩（Compaction）

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

## Hook 配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_HOOKS_COMMAND_ENABLED` | 是否允许 `command` hook 执行 | `false` |
| `EXCELMANUS_HOOKS_COMMAND_ALLOWLIST` | `command` hook 白名单前缀（逗号分隔） | 空 |
| `EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS` | `command` hook 超时（秒） | `10` |
| `EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS` | hook 输出截断长度 | `32000` |

## 路由行为

- 工具 schema 在每轮请求前按 `write_hint` 动态构建（默认注入元工具 + domain 工具）。
- 当 `write_hint=read_only` 时，仅暴露只读工具子集（并保留 `run_code` 与常驻元工具）以降低 schema token 开销。
- `activate_skill` 仅注入领域知识指引（纯知识注入，不控制工具可见性）。

## System Message 模式

`EXCELMANUS_SYSTEM_MESSAGE_MODE`（默认 `auto`）：

- `replace`：多条 system 分段注入。
- `merge`：合并为单条 system。
- `auto`：默认先走 `replace`，遇到 provider 的多 system 兼容错误时自动回退到 `merge`。

## 多模型与 AUX 模型

- `/model <name>` 切换主对话模型（`EXCELMANUS_MODELS` 中的 profile）。
- 未设置 `EXCELMANUS_AUX_MODEL` 时，路由与窗口顾问模型跟随 `/model` 切换。
- 设置了 `EXCELMANUS_AUX_MODEL` 时，路由 + 子代理默认模型 + 窗口顾问模型统一使用 AUX，不受 `/model` 影响。

## 窗口感知层配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_WINDOW_PERCEPTION_ENABLED` | 是否启用窗口感知层 | `true` |
| `EXCELMANUS_WINDOW_PERCEPTION_SYSTEM_BUDGET_TOKENS` | 系统注入窗口预算 | `3000` |
| `EXCELMANUS_WINDOW_PERCEPTION_TOOL_APPEND_TOKENS` | 工具返回附加预算 | `500` |
| `EXCELMANUS_WINDOW_PERCEPTION_MAX_WINDOWS` | 最大窗口数 | `6` |
| `EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_ROWS` | 默认视口行数 | `25` |
| `EXCELMANUS_WINDOW_PERCEPTION_DEFAULT_COLS` | 默认视口列数 | `10` |
| `EXCELMANUS_WINDOW_PERCEPTION_MINIMIZED_TOKENS` | 最小化窗口预算 | `80` |
| `EXCELMANUS_WINDOW_PERCEPTION_BACKGROUND_AFTER_IDLE` | 进入后台阈值（idle turn） | `2` |
| `EXCELMANUS_WINDOW_PERCEPTION_SUSPEND_AFTER_IDLE` | 进入挂起阈值（idle turn） | `5` |
| `EXCELMANUS_WINDOW_PERCEPTION_TERMINATE_AFTER_IDLE` | 进入关闭阈值（idle turn） | `8` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_MODE` | 生命周期顾问模式（`rules`/`hybrid`） | `rules` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TIMEOUT_MS` | 小模型顾问超时（毫秒） | `800` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_WINDOW_COUNT` | 触发小模型的窗口数阈值 | `3` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_TURN` | 触发小模型的对话轮次阈值 | `4` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_PLAN_TTL_TURNS` | 小模型计划有效轮数（TTL） | `2` |

顾问模式：
- `rules`：仅使用确定性规则（无小模型调用）。
- `hybrid`：规则兜底 + 异步小模型缓存，失败或超时自动回退规则，不阻塞主链路。

### 窗口感知高级配置

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

## VLM（视觉语言模型）配置

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

## 备份沙盒配置

默认开启，所有文件写操作自动在 `outputs/backups/` 保留副本，支持回滚。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_BACKUP_ENABLED` | 是否启用备份沙盒 | `true` |

## 代码策略引擎配置

对 `run_code` 执行的代码进行静态分析，按安全级别自动分流审批。

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_CODE_POLICY_ENABLED` | 是否启用代码策略引擎 | `true` |
| `EXCELMANUS_CODE_POLICY_GREEN_AUTO` | Green 级（安全）代码自动批准 | `true` |
| `EXCELMANUS_CODE_POLICY_YELLOW_AUTO` | Yellow 级（需审计）代码自动批准 | `true` |
| `EXCELMANUS_CODE_POLICY_EXTRA_SAFE` | 额外安全模块白名单（逗号分隔） | 空 |
| `EXCELMANUS_CODE_POLICY_EXTRA_BLOCKED` | 额外阻断模块黑名单（逗号分隔） | 空 |

## Embedding 语义检索配置

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
| `EXCELMANUS_REGISTRY_SEMANTIC_TOP_K` | 文件注册表语义检索 Top-K | `5` |
| `EXCELMANUS_REGISTRY_SEMANTIC_THRESHOLD` | 文件注册表语义检索阈值 | `0.25` |

## 持久记忆

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_MEMORY_ENABLED` | 全局记忆开关 | `true` |
| `EXCELMANUS_MEMORY_DIR` | 记忆目录 | `~/.excelmanus/memory` |
| `EXCELMANUS_MEMORY_AUTO_LOAD_LINES` | 自动加载行数 | `200` |

主题文件：`file_patterns.md`、`user_prefs.md`、`error_solutions.md`、`general.md`。
核心文件 `MEMORY.md` 保存时会与主题文件同步写入，用于会话启动自动加载。

启用 Embedding 后，记忆检索自动切换为语义匹配模式。

## MCP 配置

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
- CI：`.github/workflows/security-secrets.yml`
