# 配置参考

适用版本：1.8.0 源码 · 更新日期：2026-09-21

[文档导航](README.md) · [English](configuration_en.md) · [运维手册](ops-manual.md)

**持久化设置以主数据库为准**：模型档案存放在 `model_profiles`，其他设置存放在 `config_kv`。默认数据库为 `~/.excelmanus/excelmanus.db`。设置页、配置导入和 `/config` 共用这份数据；运行期间可存在内存覆盖值，`load_config()` 不直接读取产品环境变量。

启动后打开 Web 设置页添加模型即可；未配置模型时仍可进入设置页；保存并激活有效档案后即可开始对话。

以下**启动参数（定位符）**由启动脚本或服务管理器提供，用于确定数据目录、部署模式和监听地址：

| 定位符 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_HOME` | 持久化根目录（主数据库、加密密钥） | `~/.excelmanus` |
| `EXCELMANUS_DB_PATH` | 主库路径 | `{EXCELMANUS_HOME}/excelmanus.db` |
| `EXCELMANUS_DATA_ROOT` | 集中数据目录（上传/输出；密钥不在此目录） | `{EXCELMANUS_HOME}/data` |
| `EXCELMANUS_DEPLOY_MODE` | `auto`/`standalone`/`server`；`auto` 与未知值均为 standalone；`server` 必须显式指定 | `auto` |
| `EXCELMANUS_API_HOST` / `EXCELMANUS_API_PORT` / `EXCELMANUS_BACKEND_PORT` / `EXCELMANUS_FRONTEND_PORT` | 监听地址与端口 | 见启动脚本 |
| `EXCELMANUS_WEB_WORKERS` | uvicorn worker 数；`>1` 时 API 会提示会话与缓存可能分散到多个进程。单机保持 `1` | 由 `deploy/start.*` 设置，默认 `1` |
| `EXCELMANUS_MANAGE_TOKEN` | 可选的自动化/桌面访问令牌，至少 16 字符；兼容 `Authorization: Bearer` 或 `X-ExcelManus-Token`，不支持 URL 查询参数 | 空 |
| `EXCELMANUS_LOGIN_USERNAME` | 首次部署的单管理员账号；设置 → 安全保存的账号优先 | `admin` |
| `EXCELMANUS_LOGIN_PASSWORD` | 首次部署的管理员密码，至少 12 字符；设置 → 安全保存的密码优先 | 空（本机默认不启用） |
| `EXCELMANUS_LOGIN_SESSION_HOURS` | 浏览器登录有效期，整数 1–168 小时 | `12` |
| `EXCELMANUS_LOGIN_COOKIE_SECURE` | `auto` 根据请求 HTTPS 状态设置 Secure Cookie；HTTPS 反代部署可显式设 `true` | `auto` |
| `EXCELMANUS_SECRET_KEY` | Fernet 密钥种子（测试或自定义数据卷） | 空则生成 `{EXCELMANUS_HOME}/.secret_key` |
| `EXCELMANUS_DESKTOP` | 桌面运行标记，由桌面启动器设置 | 源码启动不设置 |
| `EXCELMANUS_RUN_PYTHON` | `run_code` 使用的 Python 路径；桌面版自动指定随包运行时 | 随运行环境确定 |
| `EXCELMANUS_WEB_UPGRADE_ENABLED` | `server` 或非本机访问时允许从网页执行一键更新；需同时启用登录保护并经管理员认证 | 空（服务器网页更新关闭） |

模型密钥和运行时选项应在设置页保存；进程环境中残留的产品设置键会被忽略，并记录警告。

项目 `.env` 和用户目录 `config.env` 均不再作为产品配置源，也不会自动导入。
评测工具仍保留显式的 `python -m excelmanus.bench --import-env PATH` 一次性导入命令；
它只把指定文件写成数据库中的模型档案，不参与启动配置加载，也不生成文件来源描述。

除标为“定位符”的项目外，下面的名称都是主库设置键。优先使用设置页；部分高级键不一定有独立的界面字段。以 `EXCELMANUS_` 开头不代表可以通过环境变量覆盖。配置保存后按界面提示应用或重启；监听端口、数据目录等启动参数必须由启动进程设置。

模型档案的 API Key 加密后存在主数据库，Fernet 密钥在 `$EXCELMANUS_HOME/.secret_key`（不跟随 DATA_ROOT，避免落入 Agent 工作区）。迁移或恢复时必须保留与数据库配套的密钥；仅复制数据库可能导致凭证无法解密。

## 基础配置

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_API_KEY` | 无激活档案时的 `config_kv` 回退；请在设置页添加档案 | — |
| `EXCELMANUS_BASE_URL` | 无激活档案时的 `config_kv` 回退 | — |
| `EXCELMANUS_MODEL` | 无激活档案时的 `config_kv` 回退；Gemini 可从 BASE_URL 自动提取 | — |
| `EXCELMANUS_PROTOCOL` | 模型协议类型（`auto`/`openai`/`openai_responses`/`anthropic`/`gemini`） | `auto` |
| `EXCELMANUS_MAX_ITERATIONS` | 本轮 LLM 回合与工具调用上限（并行工具各计 1 次） | `120` |
| `EXCELMANUS_TURN_TIMEOUT_SECONDS` | 单个 turn 的 wall-clock 上限（`0` 表示不限制） | `0` |
| `EXCELMANUS_RESPONSES_CONTINUATION_ENABLED` | 启用 Responses API 的 `previous_response_id` 原生续接 | `false` |
| `EXCELMANUS_RESPONSES_BACKGROUND_ENABLED` | 使用 Responses API 后台响应并轮询到终态 | `false` |
| `EXCELMANUS_TURN_TOKEN_BUDGET` | 单个 turn 输入与输出 token 总上限（`0` 表示不限制） | `0` |
| `EXCELMANUS_TURN_COST_BUDGET_USD` | 单个 turn 成本上限（美元，`0` 表示不限制） | `0` |
| `EXCELMANUS_INPUT_COST_PER_1K_USD` | provider 未返回成本时的输入 token 估算单价 | `0` |
| `EXCELMANUS_OUTPUT_COST_PER_1K_USD` | provider 未返回成本时的输出 token 估算单价 | `0` |
| `EXCELMANUS_MAX_CONSECUTIVE_FAILURES` | 连续失败熔断阈值 | `6` |
| `EXCELMANUS_SESSION_TTL_SECONDS` | API 会话空闲超时（秒） | `1800` |
| `EXCELMANUS_MAX_SESSIONS` | API 最大并发会话数 | `1000` |
| `EXCELMANUS_WORKSPACE_ROOT` | 文件访问白名单根目录 | `~/.excelmanus/data` |
| `EXCELMANUS_LOG_LEVEL` | 日志级别 | `INFO` |
| `EXCELMANUS_CORS_ALLOW_ORIGINS` | API CORS 允许来源（逗号分隔）。启动时还会自动补上 `localhost` / `127.0.0.1` / `[::1]` 与前端端口 | `http://localhost:3000,http://127.0.0.1:3000` |
| `EXCELMANUS_MAX_CONTEXT_TOKENS` | 显式设置时覆盖模型推断的上下文上限 | 按模型推断；未知模型回退 `256000` |
| `EXCELMANUS_PROMPT_CACHE_KEY_ENABLED` | 向 API 发送 prompt_cache_key 提升缓存命中率 | `true` |
| `EXCELMANUS_PROMPT_CACHE_RETENTION` | 提示词缓存保留策略（`default`/`extended`）。`extended` 仅对一方端点生效：Anthropic（`api.anthropic.com`）为各 `cache_control` 断点加 `ttl=1h` 并发送 `anthropic-beta: extended-cache-ttl-2025-04-11`；OpenAI（`api.openai.com`，Chat 与 Responses）发送顶层 `prompt_cache_retention=24h`。兼容网关与自部署端点不发送这些字段 | `default` |

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
| `EXCELMANUS_PARALLEL_SUBAGENT_MAX` | 同步并行批上限与单会话后台子代理并发数；后台超出并发数时排队 | `3` |
| `EXCELMANUS_PARALLEL_READONLY_TOOLS` | 同批无依赖只读工具并发执行；关闭后仍检查依赖 | `true` |
| `EXCELMANUS_PARALLEL_TOOL_MAX` | 单批只读工具最大执行并发数，1–32；新会话生效 | `4` |
| `EXCELMANUS_SUBAGENT_USER_DIR` | 用户级 subagent 目录 | `~/.excelmanus/agents` |
| `EXCELMANUS_SUBAGENT_PROJECT_DIR` | 项目级 subagent 目录 | `<workspace_root>/.excelmanus/agents` |

`delegate` 默认仍等待结果。单任务传 `background=true` 会立即返回 `run.run_id`；
使用 `action=status/list/wait` 查询，`send` 追加指令或回答子代理的问题，
`pause/cancel` 停止当前执行，`resume` 带上已保存的对话新建执行并返回新 ID。
`wait_seconds` 为 0–60，等待超时只返回状态，不取消任务。

任务和结果复用 session snapshot；重启时未完成任务显示 `interrupted`，显式 `resume`
才继续。恢复从保存的对话继续，不会复活旧进程、线程或外部请求。
暂停/取消会等当前本地同步调用完成，已经提交的文件改动保留。
后台任务超时从实际开始执行计时；主聊天结束不终止它，删除会话或关闭服务会收尾。

会话 API 提供 `GET /api/v1/sessions/{session_id}/subagents`，以及
`POST /api/v1/sessions/{session_id}/subagents/{run_id}`（body 包含 `action`、可选 `message` / `wait_seconds`）。
`GET /api/v1/sessions/{session_id}/task-list` 返回会话当前任务清单快照（无清单时为 `null`）。
网页会话顶栏的「任务」入口可查看助手的任务清单进度，以及后台任务、执行结果和已修改文件，
支持补充指令、回答问题、暂停、取消与继续。主对话结束或停止后，当前会话的活动后台任务仍每两秒
更新状态；切换回来或刷新页面会重新查询。继续会创建新执行记录，旧记录保留。
后台任务结束时会刷新其修改过的文件视图。面板仅展示当前会话，任务仍由对话中的
`delegate` 启动，不在页面加载时自动恢复执行。

## Agent 自我管理

默认关闭。在「设置 → 系统 → 能力」开启后，agent 可加载 `agent_self_management` 技能，使用 `inspect_agent` 查询能力与配置、`configure_agent` 调整当前会话的推理、上下文和工具开关。保存开关后立即同步已有会话；修改仅作用于当前内存会话，不能更改密钥、审批权限或全局默认。

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_AGENT_SELF_MANAGEMENT_ENABLED` | 启用自我管理技能及 `inspect_agent` / `configure_agent` 工具 | `false` |

## 上下文自动压缩（Compaction）

上下文接近阈值时，使用当前激活模型概括较早的对话，并保留近期内容。压缩会产生额外模型调用；遇到上下文溢出等情况，当前请求可能需要等待压缩或按恢复流程重试。

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_COMPACTION_ENABLED` | 是否启用自动压缩 | `true` |
| `EXCELMANUS_COMPACTION_THRESHOLD_RATIO` | 触发压缩的上下文占比阈值 | `0.85` |
| `EXCELMANUS_COMPACTION_KEEP_RECENT_TURNS` | 压缩时保留的最近轮数 | `5` |
| `EXCELMANUS_COMPACTION_MAX_SUMMARY_TOKENS` | 压缩摘要生成预算 | `4096` |

## Hook 配置

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_HOOKS_COMMAND_ENABLED` | 是否允许 `command` hook 执行 | `false` |
| `EXCELMANUS_HOOKS_COMMAND_ALLOWLIST` | `command` hook 白名单前缀（逗号分隔） | 空 |
| `EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS` | `command` hook 超时（秒） | `10` |
| `EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS` | hook 输出截断长度 | `32000` |

## 工具与权限

- `write` 模式允许当前工作区权限内的写入；直接工具与 `run_code` 可以交替使用。
- `read` / `plan` 不向模型提供纯写工具。含只读 action 的工具仍可被发现，但写入 action 在执行时会被拦截。例如版本工具可以列出历史，创建检查点、恢复和删除操作仍受写入权限限制。
- 常用表格工具与必要控制入口直接提供；对象、版本、公式追踪、Word、文件操作、委派和 MCP 等能力通过 `introspect_capability` 按需加载。`em.*` SDK 绑定完整的授权执行目录。
- 技能通过 `skill`、`/<技能名>` 或 `@` 按需加载，不改变会话的工具权限。默认审批与只读权限是两套不同的约束：普通写入不一定弹窗，只读限制仍会生效。
- 选择「跳过」审批后，宿主会自动批准模型的工具与 Shell 请求；`run_code` 和 Shell 可访问网络、启动子进程并执行非白名单命令。该模式具有本机命令执行风险，仅在信任当前任务时开启；「询问」模式仍保持原有白名单、网络和审批限制。

实现与维护说明见 [提示词分层](prompt-layering.md) 和 [Skillpack 协议](skillpack_protocol.md)。

## 多模型

> **注意**：`EXCELMANUS_MODELS` 已废弃。模型档案只在主数据库，通过 Web 设置页管理。

- 只保留一个激活模型。`/model <name>` 切换当前激活档案。
- 对话、子代理、上下文压缩、记忆提取都使用该激活模型。
- Web 设置页的提供商预设、默认模型、协议、思考模式、模型族和 Logo 统一维护在
  `web/src/components/settings/model/constants.tsx`；引导页只维护说明文字并从该预设派生，避免模型 ID 与 Logo 漂移。
- 提供商预设用于填写连接参数，不代表账号一定有权调用其中的模型。请以服务商返回的模型列表、连接测试及实际能力探测结果为准。
- Responses 接口优先通过模型档案的 `openai_responses` 协议选择。视觉、工具调用与推理深度仍取决于具体模型和网关。

### Codex 订阅连接

在「设置 → 模型 → 订阅账号」中连接。当前集成的浏览器回调固定为 `http://localhost:1455/auth/callback`；远程部署使用设备码，或按页面提示粘贴完整回调地址。不要把回调替换成站点域名。OAuth 凭证加密保存到主数据库，属于进程级配置，不构成 ExcelManus 用户账号。

## 模型能力探测

可在模型设置中发起连接或能力探测。探测使用配置的模型接口，可能产生请求费用；结果受服务商、网关和当时可用性影响。以下高级项也是主库设置键，不是环境变量：

| 配置键 | 说明 | 默认值 |
| --- | --- | --- |
| `CAP_PROBE_JOB_CONCURRENCY` | 全局并发探测数 | `2` |
| `CAP_PROBE_PROVIDER_CONCURRENCY` | 同一提供商的并发探测数 | `1` |
| `CAP_PROBE_HEALTH_TIMEOUT` | 连接探测超时（秒） | `8` |
| `CAP_PROBE_TOOL_TIMEOUT` | 工具调用探测超时（秒） | `20` |
| `CAP_PROBE_VISION_TIMEOUT` | 视觉探测超时（秒） | `20` |
| `CAP_PROBE_THINKING_TOTAL_TIMEOUT` | 推理探测总预算（秒） | `30` |
| `CAP_PROBE_THINKING_STRATEGY_TIMEOUT` | 单项推理策略探测超时（秒） | `8` |

健康检查遇到瞬时错误（超时、限流、网络抖动）会自动重试一次再判定，避免模型冷启动或短暂抖动跳过整轮探测；永久性错误（认证失败、模型不存在、额度不足）立即判定不重试。

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

## 文件修订与旧备份迁移

旧版备份覆盖层已移除。修改直接保存到用户文件，历史修订存放在工作区的 `.excelmanus/revisions/`。

升级后首次打开工作区会自动把旧 `outputs/backups` 一次性导入 RevisionStore；marker 在 `.excelmanus/migrations/overlay-backups.json`。需要重跑时执行：

```bash
uv run python -m excelmanus.workspace.migrate /path/to/workspace --force
```

## 代码策略引擎配置

对 `run_code` 执行的代码进行静态分析，按安全级别自动分流审批。

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_CODE_POLICY_ENABLED` | 是否启用代码策略引擎 | `true` |
| `EXCELMANUS_CODE_POLICY_GREEN_AUTO` | Green 级（安全）代码自动批准 | `true` |
| `EXCELMANUS_CODE_POLICY_YELLOW_AUTO` | 含 NETWORK 能力的 Yellow 级代码是否自动批准；普通文件写入仍按工作区与版本规则处理，不由此开关逐次确认 | `false` |
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
| `EXCELMANUS_MCP_CONFIG` | 自定义 MCP 配置文件路径，优先于默认搜索位置 | 空 |
| `EXCELMANUS_MCP_STATE_DIR` | MCP 进程状态目录 | `<workspace>/.excelmanus/mcp` |
| `EXCELMANUS_MCP_EXPAND_ENV_REFS` | 展开 MCP 配置中的 `$VAR` / `${VAR}` | `true` |
| `EXCELMANUS_MCP_SHARED_MANAGER` | API 会话是否复用共享 MCP 管理器 | `false` |
| `EXCELMANUS_MCP_ENABLE_STREAMABLE_HTTP` | 是否启用 streamable_http transport | `true` |
| `EXCELMANUS_MCP_UNDEFINED_ENV` | 未定义环境变量策略（`keep`/`empty`/`error`） | `keep` |
| `EXCELMANUS_MCP_STRICT_SECRETS` | 明文敏感字段是否阻断加载 | `false` |

`mcp.json` 能力：
- `transport` 支持 `stdio`、`sse`、`streamable_http`。
- 支持在 `args/env/url/headers` 中使用 `$VAR` / `${VAR}`。这是 MCP 子进程自己的密钥展开，读的是启动该子进程时的进程环境，不是产品设置仓。
- MCP 注册 `mcp_*` 工具；可见性和执行权限由运行时策略控制。Skillpack 可以提供使用方法并声明 `required-mcp-servers` / `required-mcp-tools`，但不会单独授予权限。

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

默认关闭。开启后在会话结束时将摘要保存到主数据库，不自动检索或注入新会话。

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

任务认领、模型步骤边界和回合结束时，保存 SessionState / 任务列表到
`session_state_snapshots`。快照包括当前主任务、输入参数、步骤位置、未消费的插话与排队消息。
SessionManager 先同步已有会话消息和工具结果，再保存引用它们的执行状态。
这不是文件检查点；文件历史在 `.excelmanus/revisions/`。

服务重启后，执行中的主任务显示为 `interrupted`，读取状态不会自动开始执行。
在聊天中输入 `/resume`，或 `/resume 先补齐区域统计`，可从保存的对话和工具结果发起
后续回合。继续会保留原来的聊天模式，并记录 `resumed_from`；已完成的工具不会由程序自动重放。
不完整的工具结果会标记为「结果未完整记录」，交给后续步骤核对实际结果。
如果只有尚未认领的消息，则 `/resume` 唤醒已有队列。

`GET /api/v1/sessions/{session_id}/turn` 返回最近主回合的状态、任务、步骤、排队数和
`can_resume`。执行中的任务不允许用 `/resume` 再启动一份。当前只恢复普通模型/工具步骤的
对话边界；审批和问答会保存对应工具调用、答案和决策，提交后通过现有聊天流自动接回。接口会在 `resume_blocked_by` 中说明仍无法恢复的状态。
继续任务不会恢复原 Python 栈、外部请求或 Code Mode 进程中的局部变量。已经开始但结果不确定的写入不会自动重放，会以未知结果交给后续模型核对。

清空会话时同时清除恢复状态与消息缓存；最新快照与快照保留按保存顺序选择，避免轮次归零后
重新选中旧任务。每条 followup 在自身回合完成后返回，不等待其他排队回合执行完毕。

## 代码执行边界

`run_code` 在本机子进程中执行，始终有超时与工作区提交管线。「询问」模式还会限制网络、子进程和敏感路径；「跳过」模式允许网络与子进程，不应视为安全隔离。修改工作区工作簿仍应通过授权的 `em.*` SDK，使提交和内容版本沿用直接工具的流程。脚本失败不会自动撤销先前已经提交的调用。

## Thinking（推理深度）

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_THINKING_EFFORT` | 推理深度级别（`none`/`minimal`/`low`/`medium`/`high`/`xhigh`/`max`） | `medium` |
| `EXCELMANUS_THINKING_BUDGET` | 精确 token 预算（> 0 时覆盖 effort 换算值） | `0` |
| `EXCELMANUS_THINKING_EFFORT_OPTIONS` | 可用推理级别列表（逗号分隔）；仅保留已支持的级别，空结果回退完整列表 | `none,minimal,low,medium,high,xhigh,max` |

## OpenAI Responses API

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_USE_RESPONSES_API` | 设为 `1` 启用 Responses API（`/responses` 端点），仅对非 Gemini/Claude 的 OpenAI 兼容 URL 生效 | `0` |

## System One / Jev

Jev 是可选的决策模型，其配置保存在 `config_kv`。在「设置 → 模型 → 供应商」配置 TypeSafe、Vercel 或自定义决策提供商，在「模型 → 模型配置」选择模型及各类策略开关。

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_JEV_ENABLED` | 总开关：`off` 关闭全部环节，`enforce` 全面接入 | `enforce` |
| `EXCELMANUS_JEV_EXPOSURE` | 工具披露与工作区/表格上下文建议：`off` / `enforce` | `enforce` |
| `EXCELMANUS_JEV_OBSERVATION` | 观察结果策略：`off` / `enforce` | `enforce` |
| `EXCELMANUS_JEV_VERIFICATION` | 修改后检查建议：`off` / `enforce` | `enforce` |
| `EXCELMANUS_JEV_RECOVERY` | 错误恢复建议：`off` / `enforce` | `enforce` |
| `EXCELMANUS_JEV_MODE_HINT` | 模式建议卡；关闭后不运行该环节 | `true` |
| `EXCELMANUS_JEV_UI_HINT` | 回合末 UI 面建议；关闭后不运行该环节 | `true` |
| `EXCELMANUS_JEV_MODEL` | 当前决策模型 | `jev-1.13.0` |
| `EXCELMANUS_JEV_ACTIVE_PROVIDER` | 当前决策提供商 id（`typesafe` / `vercel` / `custom-*`） | — |
| `EXCELMANUS_JEV_PROVIDERS` | 决策提供商列表（含密钥，Fernet 加密） | `[]` |
| `EXCELMANUS_JEV_TIMEOUT_SECONDS` | 单次评估超时 | `1.5` |
| `EXCELMANUS_JEV_CALIBRATED` | 旧版标定字段，保留用于兼容，运行时不再作为生效门槛 | `false` |
| `EXCELMANUS_TYPESAFE_API_KEY` | TypeSafe 直连密钥（与提供商列表同步） | — |
| `EXCELMANUS_AI_GATEWAY_API_KEY` | Vercel Gateway 密钥（与提供商列表同步） | — |
| `EXCELMANUS_MODEL_CANONICAL_MATCH` | 「模型 → 模型配置」的模型名智能匹配：保存档案时按置信度把 Model ID 绑定到已知规范模型名，继承其上下文窗口与能力配置；不改写发给上游的 Model ID，开启时会为已有档案补绑 | `true` |

这是可选的决策模型功能，需要 `system-one` extra。`off` 会停用总闸或对应环节；`enforce` 会直接接入开启的环节，系统不再提供仅记录的运行模式。开启总闸时，未单独指定的环节默认全部开启；关闭任一子闸只停用该环节。新增 `context.resolve` 为纯建议题包：总开关和 `EXCELMANUS_JEV_EXPOSURE` 都为 `enforce` 时，将工作区选择、表格/选区定位和最少澄清建议交给主模型，额外评估最多等待一秒，不自动新建/切换工作区或修改文件。旧配置中的 `shadow` 会在读取时迁移为 `enforce`，`EXCELMANUS_JEV_CALIBRATED` 不再阻止已开启环节生效。

## 加密配置

敏感字段（模型 API Key、OAuth Access Token 等）的加密存储。需安装 `cryptography` 依赖。

| 配置键 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_SECRET_KEY` | Fernet 加密密钥种子（定位符，见上文） | 自动生成 |

密钥按以下顺序确定：

1. 使用进程启动时指定的 `EXCELMANUS_SECRET_KEY`，通过 SHA-256 派生。
2. 读取现有的 `{EXCELMANUS_HOME}/.secret_key`。
3. 读取历史 `DATA_ROOT/.secret_key` 或 `~/.excelmanus/data/.secret_key`，并尝试迁移到正式路径。
4. 没有可用密钥时，在正式路径生成新密钥，并限制文件权限。

敏感凭证的加密写入在加密组件不可用时会报错。不要通过删除或重新生成密钥来处理已有数据库的解密失败；应恢复原来配套的密钥。

`FileAccessGuard` 会拒绝读写 `.secret_key`、`excelmanus.db`、`installations.json` 以及磁盘上残留的 `.env` / `config.env`，即使这些文件位于已登记的工作区内。

## 单用户工作区

一份进程只有一份 data home（SQLite 聊天库、记忆、MCP 配置、模型凭证），**不是**多租户。用户可以把多个本机文件夹登记为工作区：每个对话绑定其中一个文件夹，同一文件夹下的多条对话共享该目录里的文件。Agent 的 cwd、文件守卫、版本与 registry 扫描根跟随当前会话的文件夹；记忆和 MCP 仍是进程级共享，不会按文件夹隔离。

默认工作区是 `EXCELMANUS_DATA_ROOT`（若设置）或 `EXCELMANUS_WORKSPACE_ROOT`，未设置时使用 `EXCELMANUS_HOME/data`（默认 `~/.excelmanus/data`），与应用安装/源码目录分开。旧版自动登记的应用根目录不再作为新会话的候选，历史会话及其上传/输出文件保留在原位置，不自动搬移。

文件树、文件发现、@ 提及和工具访问默认忽略产品源码及构建目录。需要处理代码项目时，在对话 tab 的“添加工作区”中显式登记该目录；登记会持久化代码访问许可，重启后仍然生效。自动创建默认工作区不会授予此许可。工作区边界、敏感文件和内部状态目录的保护保持生效。添加工作区仅收编已有本机目录，不会在目标路径上 mkdir，也不会把聊天记录写到 xlsx 旁边。

`EXCELMANUS_AUTH_ENABLED` / `NEXT_PUBLIC_AUTH_ENABLED` / `EXCELMANUS_SESSION_ISOLATION` 已移除。Codex 订阅 OAuth 仍可用（进程级，不绑定登录用户）。

后端默认监听 `127.0.0.1`。单管理员登录用于保护整个实例，不创建用户表、注册入口或用户工作区隔离，模型订阅 OAuth 仍独立工作。在 **设置 → 安全 → 登录保护** 中启用/关闭，设置账号和密码；密码留空保留原值。保存立即生效并撤销所有浏览器会话，重启后保留。明确关闭后，该地址允许直接访问，包括原管理令牌保护的接口。

首次启动 `server` 模式（包括 loopback 反代）或非 loopback 监听时，须先配置管理员密码或管理令牌，或者先在本地设置页保存登录配置。无凭据、短密码或短令牌会拒绝启动；已在设置页明确关闭保护的选择会被保留。服务器应使用 HTTPS，并由 Nginx 同源转发前端和 API。详细配置、反代和恢复方法见 [服务器登录保护](server-login.md)。

登录配置和会话单独保存在 `{EXCELMANUS_HOME}/access.db`，不参与产品配置导入/导出。密码使用带随机盐的 scrypt 哈希，浏览器只持有 HttpOnly、SameSite=Strict 会话 Cookie。登录、状态及最小健康检查可公开访问，其余 API（含文件、SSE、订阅接口和 API 文档）需要认证。健康检查在未登录时不返回模型、引导进度或会话信息。登录限制为每实例每分钟 10 次，多个 worker 共享会话、撤销状态和限速。

### 旧版 `users/` 手动搬迁

不要自动合并多个 `users/{id}`。若本地还留着旧隔离目录：

1. 选出**唯一**要继续用的 `users/{id}/`。
2. 把其中的工作区文件拷到当前 `data_root` / `workspace_root`。
3. 各用户目录下的 `data.db` **不会**自动导入主库；聊天记录与记忆需自行决定是否手工迁移。
4. FileRegistry 扫描会跳过名为 `users` 的目录，避免把归档残骸扫进工作区。

## 变更记录

- 2026-09-21：新增 `EXCELMANUS_WEB_UPGRADE_ENABLED` 服务器网页更新开关、Agent 自我管理（`EXCELMANUS_AGENT_SELF_MANAGEMENT_ENABLED`）与模型名智能匹配（`EXCELMANUS_MODEL_CANONICAL_MATCH`）说明。

- 2026-09-19：同步桌面定位符、4096 token 压缩预算、工具按需加载、能力探测、Jev 检查与恢复设置，以及 OAuth 和密钥迁移说明。

- 2026-09-19：Jev 设置从「运行时」改到「模型 → 供应商 / 模型配置」。TypeSafe 与 Vercel 拆成两个预填卡，并支持自定义决策提供商。
- 2026-09-18：产品设置只走主库 `config_kv` / `model_profiles` 与进程覆盖层。Jev 总闸与密钥也走这条链（Web 设置页运行时项），不再从进程环境或仓库根 `.env` 继承。定位符（`HOME` / `DB_PATH` / `DATA_ROOT` / `DEPLOY_MODE` / 端口 / `MANAGE_TOKEN`）仍由启动脚本写入进程。`deploy/.env.deploy` 与 `web/.env.local` 是运维机清单和 Next.js 运行时 origin。MCP `mcp.json` 的 `$VAR` 只展开给 MCP 子进程。
