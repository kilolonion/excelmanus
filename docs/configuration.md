# 配置参考

用户设置只存在**主数据库**（`~/.excelmanus/excelmanus.db` 的 `config_kv` 与 `model_profiles`）。设置页、导入配置、`/config` 都写这里。`load_config()` 只读这份设置源。

启动后打开 Web 设置页添加模型即可；无模型时服务以降级模式启动，保存档案后立即生效。

进程可以用少量**定位符**找到数据卷并监听，由启动脚本 / systemd 写入进程，**不是**设置覆盖层：

| 定位符 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_HOME` | 持久化根目录（主数据库、加密密钥） | `~/.excelmanus` |
| `EXCELMANUS_DB_PATH` | 主库路径 | `{EXCELMANUS_HOME}/excelmanus.db` |
| `EXCELMANUS_DATA_ROOT` | 集中数据目录（上传/输出；密钥不在此目录） | `{EXCELMANUS_HOME}/data` |
| `EXCELMANUS_DEPLOY_MODE` | `auto`/`standalone`/`server`；`auto` 与未知值均为 standalone；`server` 必须显式指定 | `auto` |
| `EXCELMANUS_API_HOST` / `EXCELMANUS_API_PORT` / `EXCELMANUS_BACKEND_PORT` / `EXCELMANUS_FRONTEND_PORT` | 监听地址与端口 | 见启动脚本 |
| `EXCELMANUS_WEB_WORKERS` | uvicorn worker 数；`>1` 时 API 启动打缓存失效 WARNING。单机保持 `1` | 由 `deploy/start.*` 设置，默认 `1` |
| `EXCELMANUS_MANAGE_TOKEN` | 绑定非 loopback 时必填（至少 16 字符）；配置后除健康检查外全部 `/api/v1` 需 `Authorization: Bearer` | 空（仅 loopback 可空） |
| `EXCELMANUS_SECRET_KEY` | Fernet 密钥种子（测试或自定义数据卷） | 空则生成 `{EXCELMANUS_HOME}/.secret_key` |

不要把模型密钥或运行时选项放进进程环境；残留的产品设置键会被忽略并打告警。

下面表格中的名称是主库 `config_kv` 的键，与设置页字段对应。

模型档案的 API Key 加密后存在主数据库，Fernet 密钥在 `$EXCELMANUS_HOME/.secret_key`（不跟随 DATA_ROOT，避免落入 Agent 工作区）。这两处必须在同一持久卷上，否则重启后 Key 无法解密。

## 基础配置

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_API_KEY` | 无激活档案时的 `config_kv` 回退；请在设置页添加档案 | — |
| `EXCELMANUS_BASE_URL` | 无激活档案时的 `config_kv` 回退 | — |
| `EXCELMANUS_MODEL` | 无激活档案时的 `config_kv` 回退；Gemini 可从 BASE_URL 自动提取 | — |
| `EXCELMANUS_PROTOCOL` | 模型协议类型（`auto`/`openai`/`openai_responses`/`anthropic`/`gemini`） | `auto` |
| `EXCELMANUS_MAX_ITERATIONS` | 本轮 LLM 回合与工具调用上限（并行工具各计 1 次） | `50` |
| `EXCELMANUS_MAX_CONSECUTIVE_FAILURES` | 连续失败熔断阈值 | `6` |
| `EXCELMANUS_SESSION_TTL_SECONDS` | API 会话空闲超时（秒） | `1800` |
| `EXCELMANUS_MAX_SESSIONS` | API 最大并发会话数 | `1000` |
| `EXCELMANUS_WORKSPACE_ROOT` | 文件访问白名单根目录 | `.` |
| `EXCELMANUS_LOG_LEVEL` | 日志级别 | `INFO` |
| `EXCELMANUS_CORS_ALLOW_ORIGINS` | API CORS 允许来源（逗号分隔）。启动时还会自动补上 `localhost` / `127.0.0.1` / `[::1]` 与前端端口 | `http://localhost:3000,http://127.0.0.1:3000` |
| `EXCELMANUS_MAX_CONTEXT_TOKENS` | 对话上下文 token 上限 | `128000` |
| `EXCELMANUS_PROMPT_CACHE_KEY_ENABLED` | 向 API 发送 prompt_cache_key 提升缓存命中率 | `true` |

## Skillpack 与路由配置

| 配置键 | 说明 | 默认值 |
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
| `EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS` | 工具结果全局硬截断长度（0 表示不限制） | `12000` |

## Subagent 配置

| 配置键 | 说明 | 默认值 |
|---|---|---|
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

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_COMPACTION_ENABLED` | 是否启用自动压缩 | `true` |
| `EXCELMANUS_COMPACTION_THRESHOLD_RATIO` | 触发压缩的上下文占比阈值 | `0.85` |
| `EXCELMANUS_COMPACTION_KEEP_RECENT_TURNS` | 压缩时保留的最近轮数 | `5` |
| `EXCELMANUS_COMPACTION_MAX_SUMMARY_TOKENS` | 压缩摘要最大 token 数 | `1500` |

## Hook 配置

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_HOOKS_COMMAND_ENABLED` | 是否允许 `command` hook 执行 | `false` |
| `EXCELMANUS_HOOKS_COMMAND_ALLOWLIST` | `command` hook 白名单前缀（逗号分隔） | 空 |
| `EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS` | `command` hook 超时（秒） | `10` |
| `EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS` | hook 输出截断长度 | `32000` |

## 路由行为

- 每轮请求前由 `EffectiveToolCatalog` 推导可见工具集（`names()` / `tool_schemas()` / `tool_index_text()` / `introspection_source()` / `digest()` 同源）。目录可见性用 `policy.is_catalog_visible(tool_name, declared)`：无写效应，或该工具含只读 action（`policy.has_readonly_action`）。`READ_ONLY_SAFE_TOOLS` 白名单不再驱动目录可见集（仍用于审批、并行等其它判定）。
- `read` / `plan` 可见集不含纯写工具。`manage_spreadsheet_versions` 因含只读 `list` action 在 read/plan 仍可见；`checkpoint` / `restore` 执行期由 `write_effect_for_call` 判为 `workspace_write` 拦截。`write` 看见完整目录；`code` 目录仅 `run_code`。看见工具不等于可以改表。
- 技能靠 user-role 目录快照。模型调用 `skill` 加载正文；用户 `/name` 手势也会注入 `<skill-invocation>`。二者都不改工具可见性。

## 多模型

> **注意**：`EXCELMANUS_MODELS` 已废弃。模型档案只在主数据库，通过 Web 设置页管理。

- 只保留一个激活模型。`/model <name>` 切换当前激活档案。
- 对话、子代理、上下文压缩、记忆提取都使用该激活模型。

## 视觉配置

图片只交给当前激活模型阅读。无视觉时拒绝附件；有视觉时用 `read_image` 或工作台附件注入，再由模型产出 `WorkbookSpec` 并调用 `edit_spreadsheet(workbook_spec=)` 建表。没有独立视觉流水线，也没有附属 VLM 描述。

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_MAIN_MODEL_VISION` | 激活模型视觉能力（`auto`/`true`/`false`） | `auto` |
| `EXCELMANUS_IMAGE_PIXEL_BUDGET` | 请求版总像素上限（正整数或 `low`） | `640000` |
| `EXCELMANUS_IMAGE_MAX_BYTES` | 单张请求版编码字节上限 | `1048576` |
| `EXCELMANUS_IMAGE_FILES_API` | Files 传输：`auto` / `true` / `false` | `auto` |
| `EXCELMANUS_FRIENDLY_ERROR_MESSAGES` | 将 HTTP 内部错误映射为可读提示 | `true` |
| `EXCELMANUS_LLM_RETRY_MAX_ATTEMPTS` | 模型调用失败最大尝试次数（含首次） | `3` |
| `EXCELMANUS_LLM_RETRY_BASE_DELAY_SECONDS` | 重试指数退避起始延迟（秒） | `2.0` |
| `EXCELMANUS_LLM_RETRY_MAX_DELAY_SECONDS` | 单次重试等待上限（秒） | `30.0` |

## 备份沙盒

备份 overlay 已移除。写入落在用户路径；历史在 `.excelmanus/revisions/`。

升级后首次打开工作区会自动把旧 `outputs/backups` 一次性导入 RevisionStore；marker 在 `.excelmanus/migrations/overlay-backups.json`。需要重跑时执行：

```bash
python -m excelmanus.workspace.migrate <workspace> --force
```

## 代码策略引擎配置

对 `run_code` 执行的代码进行静态分析，按安全级别自动分流审批。

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_CODE_POLICY_ENABLED` | 是否启用代码策略引擎 | `true` |
| `EXCELMANUS_CODE_POLICY_GREEN_AUTO` | Green 级（安全）代码自动批准 | `true` |
| `EXCELMANUS_CODE_POLICY_YELLOW_AUTO` | Yellow 级代码自动批准（默认关；打开后仍不会自动批准文件系统写入） | `false` |
| `EXCELMANUS_CODE_POLICY_EXTRA_SAFE` | 额外安全模块白名单（逗号分隔） | 空 |
| `EXCELMANUS_CODE_POLICY_EXTRA_BLOCKED` | 额外阻断模块黑名单（逗号分隔） | 空 |

## 持久记忆

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_MEMORY_ENABLED` | 全局记忆开关 | `true` |
| `EXCELMANUS_MEMORY_DIR` | 记忆目录 | `~/.excelmanus/memory` |
| `EXCELMANUS_MEMORY_AUTO_LOAD_LINES` | `load_core` 单次上限；当前不会在会话开始自动注入 | `200` |
| `EXCELMANUS_MEMORY_EXPIRE_DAYS` | host 会话启动时清理过期记忆；`0` 不过期 | `90` |
| `EXCELMANUS_MEMORY_MAINTENANCE_ENABLED` | 提取新记忆后按条件做 LLM 维护 | `false` |
| `EXCELMANUS_MEMORY_MAINTENANCE_MIN_ENTRIES` | 少于此条数不维护 | `10` |
| `EXCELMANUS_MEMORY_MAINTENANCE_NEW_THRESHOLD` | 新增达到此数才可能维护 | `5` |
| `EXCELMANUS_MEMORY_MAINTENANCE_INTERVAL_HOURS` | 两次维护最小间隔（小时） | `4.0` |
| `EXCELMANUS_MEMORY_MAINTENANCE_MODEL` | 维护模型 ID；空则用激活模型 | 空 |

主题文件：`file_patterns.md`、`user_prefs.md`、`error_solutions.md`、`general.md`。
记忆通过工具按主题读取，不会在会话开始时自动注入 prompt。条目去重走 `UNIQUE(category, content_hash)`。

## 内置搜索引擎

内置三个搜索引擎 MCP Server（Exa / Tavily / Brave），无需手动配置 `mcp.json`。

**启用策略**（"默认+可选"模式）：
1. `EXCELMANUS_EXA_SEARCH=false` → 禁用全部内置搜索引擎
2. `EXCELMANUS_SEARCH_DEFAULT` 指定的默认引擎始终启用
3. 非默认引擎仅在配置了对应 API 密钥后额外启用
4. 默认引擎为 tavily/brave 但缺少 API 密钥或 Node.js → 自动降级回 exa

Tavily 和 Brave 通过 `npx` 启动（stdio 传输），需要系统安装 Node.js。Exa 通过 HTTP 连接，无需 Node.js。

| 配置键 | 说明 | 默认值 |
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

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_MCP_SHARED_MANAGER` | API 会话是否复用共享 MCP 管理器 | `false` |
| `EXCELMANUS_MCP_ENABLE_STREAMABLE_HTTP` | 是否启用 streamable_http transport | `false` |
| `EXCELMANUS_MCP_UNDEFINED_ENV` | 未定义环境变量策略（`keep`/`empty`/`error`） | `keep` |
| `EXCELMANUS_MCP_STRICT_SECRETS` | 明文敏感字段是否阻断加载 | `false` |

`mcp.json` 能力：
- `transport` 支持 `stdio`、`sse`、`streamable_http`。
- 支持在 `args/env/url/headers` 中使用 `$VAR` / `${VAR}`。这是 MCP 子进程自己的密钥展开，读的是启动该子进程时的进程环境，不是产品设置仓。
- MCP 仅负责注册 `mcp_*` 工具；Skillpack 负责策略与授权。若 Skillpack 需要 MCP，在 `SKILL.md` 中声明 `required-mcp-servers` / `required-mcp-tools`。

MCP 安全扫描：
- 本地：`scripts/security/scan_secrets.sh`
- pre-commit：`.pre-commit-config.yaml` 内置钩子
- CI：`.github/workflows/security-secrets.yml`

## 统一数据库

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_DB_PATH` | SQLite 数据库路径（聊天记录、记忆、审批、模型档案、用户设置均存于此） | `~/.excelmanus/excelmanus.db` |

## 聊天记录持久化

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_CHAT_HISTORY_ENABLED` | 是否启用聊天记录持久化 | `true` |

## 会话摘要

默认关。开启后只在会话结束时把摘要落入主库，不检索、不注入新会话。

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_SESSION_SUMMARY_ENABLED` | 会话结束是否生成摘要并落库 | `false` |
| `EXCELMANUS_SESSION_SUMMARY_MIN_TURNS` | 少于此轮数不写摘要 | `3` |

## API 号池

默认关。开启后可在设置页管理 API 号池与订阅轮换。

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_POOL_ENABLED` | 是否启用号池 | `false` |
| `EXCELMANUS_POOL_AUTO_ENABLED` | 是否自动轮换 | `false` |
| `EXCELMANUS_POOL_AUTO_INTERVAL` | 自动轮换检查间隔（秒） | `60` |
| `EXCELMANUS_POOL_AUTO_COOLDOWN` | 轮换冷却（秒） | `300` |
| `EXCELMANUS_POOL_AUTO_HYSTERESIS_DELTA` | 自动轮换滞回阈值 | `0.12` |
| `EXCELMANUS_POOL_AUTO_MIN_DWELL` | 同一账号最短停留（秒） | `180` |
| `EXCELMANUS_POOL_AUTO_BREAKER_OPEN` | 熔断打开时长（秒） | `120` |
| `EXCELMANUS_POOL_AUTO_BREAKER_THRESHOLD` | 熔断失败次数阈值 | `5` |

## 工具参数 Schema 校验

对 LLM 返回的工具调用参数进行 JSON Schema 级校验，分三级模式。默认 **shadow**：不阻断调用，把违规写入工具结果供模型自纠。

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_TOOL_SCHEMA_VALIDATION_MODE` | `off`：不校验。`shadow`：记录不阻断；违规以 `schema_validation` / `schema_violations` / `remediation` 出现在工具结果里。`enforce`：阻断并返回错误（`error_code=TOOL_ARGUMENT_VALIDATION_ERROR`，含 `failure_class` / `remediation`） | `shadow` |
| `EXCELMANUS_TOOL_SCHEMA_VALIDATION_CANARY_PERCENT` | `enforce` 模式灰度比例（0~100），100 = 全量 | `100` |
| `EXCELMANUS_TOOL_SCHEMA_STRICT_PATH` | 严格路径策略：路径参数必须为相对路径且禁止 `..` | `false` |

## 会话快照

每轮结束后保存 SessionState / 任务列表到 `session_state_snapshots` 表，用于会话恢复。这不是文件检查点；文件历史在 `.excelmanus/revisions/`。

## 代码沙盒

`run_code` 只走本机子进程围栏：禁网络、禁起进程、禁出工作区。产品安装也不再提供 Compose / 镜像轨。

## Thinking（推理深度）

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_THINKING_EFFORT` | 推理深度级别（`none`/`minimal`/`low`/`medium`/`high`/`xhigh`/`max`） | `medium` |
| `EXCELMANUS_THINKING_BUDGET` | 精确 token 预算（> 0 时覆盖 effort 换算值） | `0` |

## OpenAI Responses API

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_USE_RESPONSES_API` | 设为 `1` 启用 Responses API（`/responses` 端点），仅对非 Gemini/Claude 的 OpenAI 兼容 URL 生效 | `0` |

## System One / Jev

Jev 不是聊天模型，不进 `model_profiles`。TypeSafe、Vercel 与自定义决策提供商在设置页「模型 → 供应商」；Jev 系列选型与总闸/子闸在「模型 → 模型配置」。都写主库 `config_kv`。

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_JEV_ENABLED` | 总闸：`off` / `shadow` / `enforce` | `off` |
| `EXCELMANUS_JEV_EXPOSURE` | 暴露面子闸 | `off` |
| `EXCELMANUS_JEV_OBSERVATION` | 观察面子闸 | `off` |
| `EXCELMANUS_JEV_MODE_HINT` | 模式建议卡 | `false` |
| `EXCELMANUS_JEV_PRESENT_AS_AUTO` | 瞬态 present_as | `false` |
| `EXCELMANUS_JEV_UI_HINT` | 回合末 UI 面建议 | `false` |
| `EXCELMANUS_JEV_MODEL` | 当前决策模型 | `jev-1.13.0` |
| `EXCELMANUS_JEV_ACTIVE_PROVIDER` | 当前决策提供商 id（`typesafe` / `vercel` / `custom-*`） | — |
| `EXCELMANUS_JEV_PROVIDERS` | 决策提供商列表（含密钥，Fernet 加密） | `[]` |
| `EXCELMANUS_JEV_TIMEOUT_SECONDS` | 单次评估超时 | `1.5` |
| `EXCELMANUS_JEV_CALIBRATED` | 中文对照已签字后才允许 enforce 副作用 | `false` |
| `EXCELMANUS_TYPESAFE_API_KEY` | TypeSafe 直连密钥（与提供商列表同步） | — |
| `EXCELMANUS_AI_GATEWAY_API_KEY` | Vercel Gateway 密钥（与提供商列表同步） | — |

未签字时即使总闸为 `enforce` 也不会 `applied`。标定器 `bench/jev_live_calibrate.py` 从同一份主库读密钥。

## 加密配置

敏感字段（模型 API Key、OAuth Access Token 等）的加密存储。需安装 `cryptography` 依赖。

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_SECRET_KEY` | Fernet 加密密钥种子（定位符，见上文） | 自动生成 |

密钥派生优先级：
1. 进程启动时若已设置 `EXCELMANUS_SECRET_KEY`（SHA-256 派生；用于测试或自定义数据卷）
2. `{EXCELMANUS_HOME}/.secret_key` 自动生成（首次启动时创建，文件权限 600）
3. 历史 `DATA_ROOT/.secret_key` / `~/.excelmanus/data/.secret_key`（读取后迁移到正式路径）
4. 均不可用时，加密组件不启用（仅限开发环境）

`FileAccessGuard` 会拒绝读写 `.secret_key`、`excelmanus.db`、`installations.json` 以及磁盘上残留的 `.env` / `config.env`，即使这些文件位于已登记的工作区内。

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

## 变更记录

- 2026-09-19：Jev 设置从「运行时」改到「模型 → 供应商 / 模型配置」。TypeSafe 与 Vercel 拆成两个预填卡，并支持自定义决策提供商。
- 2026-09-18：产品设置只走主库 `config_kv` / `model_profiles` 与进程覆盖层。Jev 总闸与密钥也走这条链（Web 设置页运行时项），不再从进程环境或仓库根 `.env` 继承。定位符（`HOME` / `DB_PATH` / `DATA_ROOT` / `DEPLOY_MODE` / 端口 / `MANAGE_TOKEN`）仍由启动脚本写入进程。`deploy/.env.deploy` 与 `web/.env.local` 是运维机清单和 Next.js 运行时 origin。MCP `mcp.json` 的 `$VAR` 只展开给 MCP 子进程。
