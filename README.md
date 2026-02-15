# ExcelManus v4

基于大语言模型的 Excel 智能代理框架（`Tools + Skillpacks` 双层架构）。

- `Tools`：基础能力执行层（工具函数 + schema + 安全边界）
- `Skillpacks`：策略编排层（`SKILL.md` 元数据 + 路由 + `allowed-tools` 授权）

支持两种运行模式：

- **CLI 模式**：终端交互
- **API 模式**：REST API

## 安装

```bash
pip install .
pip install -e ".[dev]"
```

要求 Python >= 3.10。

## 配置

优先级：环境变量 > `.env` > 默认值。

### 基础配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_API_KEY` | LLM API Key（必填） | — |
| `EXCELMANUS_BASE_URL` | LLM API 地址（必填，可由 `EXCELMANUS_MODELS` 首项继承） | — |
| `EXCELMANUS_MODEL` | 模型名称（必填，可由 `EXCELMANUS_MODELS` 首项继承） | — |
| `EXCELMANUS_MAX_ITERATIONS` | Agent 最大迭代轮数 | `20` |
| `EXCELMANUS_MAX_CONSECUTIVE_FAILURES` | 连续失败熔断阈值 | `3` |
| `EXCELMANUS_SESSION_TTL_SECONDS` | API 会话空闲超时（秒） | `1800` |
| `EXCELMANUS_MAX_SESSIONS` | API 最大并发会话数 | `1000` |
| `EXCELMANUS_WORKSPACE_ROOT` | 文件访问白名单根目录 | `.` |
| `EXCELMANUS_LOG_LEVEL` | 日志级别 | `INFO` |
| `EXCELMANUS_EXTERNAL_SAFE_MODE` | 对外安全模式（隐藏思考/工具细节与路由元信息） | `true` |
| `EXCELMANUS_CORS_ALLOW_ORIGINS` | API CORS 允许来源（逗号分隔） | `http://localhost:5173` |

### Skillpack 路由配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_SKILLS_SYSTEM_DIR` | 内置 Skillpacks 目录 | `excelmanus/skillpacks/system` |
| `EXCELMANUS_SKILLS_USER_DIR` | 用户级 Skillpacks 目录 | `~/.excelmanus/skillpacks` |
| `EXCELMANUS_SKILLS_PROJECT_DIR` | 项目级 Skillpacks 目录 | `<workspace_root>/.excelmanus/skillpacks` |
| `EXCELMANUS_SKILLS_DISCOVERY_ENABLED` | 是否启用通用目录发现 | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_SCAN_WORKSPACE_ANCESTORS` | 是否扫描 cwd→workspace 祖先链 `.agents/skills` | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_AGENTS` | 是否发现 `.agents/skills` | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_CLAUDE` | 是否发现 `.claude/skills` | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_INCLUDE_OPENCLAW` | 是否发现 `.openclaw/skills`/`~/.openclaw/skills` | `true` |
| `EXCELMANUS_SKILLS_DISCOVERY_EXTRA_DIRS` | 额外扫描目录（逗号分隔） | 空 |
| `EXCELMANUS_SYSTEM_MESSAGE_MODE` | system 注入策略（`auto\|merge\|replace`） | `auto` |
| `EXCELMANUS_TOOL_RESULT_HARD_CAP_CHARS` | 工具结果全局硬截断长度（0 表示不限制） | `12000` |
| `EXCELMANUS_MODELS` | 可切换模型档案（JSON 数组，供 `/model` 使用） | 空 |
| `EXCELMANUS_ROUTER_API_KEY` | 路由模型 API Key（未设置时回退主配置） | — |
| `EXCELMANUS_ROUTER_BASE_URL` | 路由模型 Base URL（未设置时回退主配置） | — |
| `EXCELMANUS_ROUTER_MODEL` | 路由模型名称（设置后与主模型解耦） | — |
| `EXCELMANUS_MCP_SHARED_MANAGER` | API 会话是否复用共享 MCP 管理器 | `false` |
| `EXCELMANUS_LARGE_EXCEL_THRESHOLD_BYTES` | 触发大文件 subagent 委派提示的阈值（字节） | `8388608` |
| `EXCELMANUS_SUBAGENT_ENABLED` | 是否启用 subagent 执行 | `true` |
| `EXCELMANUS_SUBAGENT_MODEL` | subagent 模型（为空时回退主模型） | — |
| `EXCELMANUS_SUBAGENT_MAX_ITERATIONS` | subagent 最大迭代轮数 | `120` |
| `EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES` | subagent 连续失败熔断阈值 | `2` |
| `EXCELMANUS_MAX_CONTEXT_TOKENS` | 对话上下文 token 上限 | `128000` |
| `EXCELMANUS_HOOKS_COMMAND_ENABLED` | 是否允许 `command` hook 执行 | `false` |
| `EXCELMANUS_HOOKS_COMMAND_ALLOWLIST` | `command` hook 白名单前缀（逗号分隔） | 空 |
| `EXCELMANUS_HOOKS_COMMAND_TIMEOUT_SECONDS` | `command` hook 超时（秒） | `10` |
| `EXCELMANUS_HOOKS_OUTPUT_MAX_CHARS` | hook 输出截断长度 | `32000` |

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
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_MODE` | 生命周期顾问模式（`rules`/`hybrid`） | `hybrid` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TIMEOUT_MS` | 小模型顾问超时（毫秒） | `800` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_WINDOW_COUNT` | 触发小模型的窗口数阈值 | `3` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_TRIGGER_TURN` | 触发小模型的对话轮次阈值 | `4` |
| `EXCELMANUS_WINDOW_PERCEPTION_ADVISOR_PLAN_TTL_TURNS` | 小模型计划有效轮数（TTL） | `2` |

窗口感知顾问行为：
- `rules`：仅使用确定性规则（无小模型调用）。
- `hybrid`：规则兜底 + 异步小模型缓存，失败或超时自动回退规则，不阻塞主链路。

### 破坏性升级迁移清单（本次单期整改）

- `EXCELMANUS_SYSTEM_MESSAGE_MODE=multi` 已移除，启动将直接报错；请改为 `auto` / `merge` / `replace`。
- `EXCELMANUS_BASE_URL`、`EXCELMANUS_MODEL` 不再有隐式默认值；必须显式配置，或由 `EXCELMANUS_MODELS` 首项继承。
- Plan 目录不再自动从 `outputs/plans/` 迁移到 `.excelmanus/plans/`，旧目录需手工迁移一次。
- 会话统计接口移除 `active_count` 旧属性，请统一使用 `await get_active_count()`。
- MCP 管理器已移除 legacy `npx` 直连兜底逻辑，需使用当前 `mcp.json` 配置链路。

最小可运行 `.env` 示例：

```dotenv
EXCELMANUS_API_KEY=your-api-key
EXCELMANUS_BASE_URL=https://your-llm-endpoint/v1
EXCELMANUS_MODEL=your-model-id
# 可选：EXCELMANUS_MODELS=[{"name":"default","model":"your-model-id","api_key":"...","base_url":"..."}]
```

v4 路由简化迁移说明：
- 以下旧变量在 v4 已不生效，现已正式移除：`EXCELMANUS_SKILLS_PREFILTER_TOPK`、`EXCELMANUS_SKILLS_MAX_SELECTED`、`EXCELMANUS_SKILLS_SKIP_LLM_CONFIRM`、`EXCELMANUS_SKILLS_FASTPATH_MIN_SCORE`、`EXCELMANUS_SKILLS_FASTPATH_MIN_GAP`。
- 当前路由行为以 `slash_direct` 与 `fallback/no_skillpack/slash_not_found` 两类主路径为核心，运行时工具权限由 `tool_scope` 严格约束。

`EXCELMANUS_SYSTEM_MESSAGE_MODE` 语义：
- `replace`：多条 system 分段注入。
- `merge`：合并为单条 system。
- `auto`：默认先走 `replace`，遇到 provider 的多 system 兼容错误时自动回退到 `merge`。

多模型与路由模型规则：
- `/model <name>` 仅切换主对话模型（`EXCELMANUS_MODELS` 中的 profile）。
- 当未设置 `EXCELMANUS_ROUTER_MODEL` 时，路由模型会跟随 `/model` 切换。
- 当设置了 `EXCELMANUS_ROUTER_MODEL` 时，路由模型保持独立，不受 `/model` 影响。

### MCP 启动缓存（避免每次重装）

项目根目录 `mcp.json` 已改为使用 `scripts/mcp/*.sh` 启动器：

- 首次启动时按固定版本自动安装到 `./.excelmanus/mcp/`
- 后续启动直接复用本地缓存，不再每次通过 `npx/uvx` 在线安装
- 如需强制重装，删除 `./.excelmanus/mcp/` 后重启即可

可通过环境变量 `EXCELMANUS_MCP_STATE_DIR` 自定义缓存目录。

MCP 配置能力（`mcp.json`）：
- `transport` 支持 `stdio`、`sse`、`streamable_http`（可用 `EXCELMANUS_MCP_ENABLE_STREAMABLE_HTTP` 开关控制）。
- 支持在 `args/env/url/headers` 中使用 `$VAR` / `${VAR}` 环境变量引用（默认开启展开）。
- 未定义环境变量策略可通过 `EXCELMANUS_MCP_UNDEFINED_ENV` 设置为：
  - `keep`（默认）：保留原 token
  - `empty`：替换为空字符串
  - `error`：该 server 跳过加载
- 明文敏感字段默认仅告警；设置 `EXCELMANUS_MCP_STRICT_SECRETS=true` 后会阻断对应 server 加载。
- 可选 `stateDir` / `state_dir` 字段可覆盖进程识别目录（用于回收时与脚本目录对齐）。

API 共享 MCP 生命周期（灰度开关）：
- 设置 `EXCELMANUS_MCP_SHARED_MANAGER=true` 后，API 模式会共享单例 MCP 管理器，避免“每会话重复初始化 MCP”导致的进程/连接线性增长。
- 在共享模式下，会话删除/过期不会触发 MCP 全量关闭，统一在服务生命周期结束时回收。

CLI `/mcp` 状态语义：
- `status=ready`：连接成功且工具发现成功
- `status=connect_failed`：连接失败
- `status=discover_failed`：工具发现失败
- `last_error` 字段显示最近错误摘要，便于排障

> 破坏性变更（MCP/Skills 解耦）：
> 启动时不再自动将 MCP Server 生成并注入为临时 Skillpack。  
> MCP 仅负责注册 `mcp_*` 工具；Skillpack 仅负责策略与授权。  
> 若 Skillpack 需要 MCP，请在 `SKILL.md` 中显式声明 `allowed-tools`（如 `mcp:context7:*`），可选声明 `required-mcp-servers` / `required-mcp-tools`。

MCP 安全扫描（pre-commit/CI）：
- 本地：`scripts/security/scan_secrets.sh`
- pre-commit：`.pre-commit-config.yaml` 已内置 `excelmanus secret scan` 钩子
- CI：`.github/workflows/secret-scan.yml` 会扫描 `mcp.json`、`.env*`、`*.toml` 中疑似明文凭证

## 使用方式

### CLI

```bash
excelmanus
# 或
python -m excelmanus
```

可用命令：`/help`、`/history`、`/clear`、`/skills`、`/skills list`、`/skills get <name>`、`/skills create <name> --json ... | --json-file ...`、`/skills patch <name> --json ... | --json-file ...`、`/skills delete <name> [--yes]`、`/subagent [on|off|status|list]`、`/subagent run -- <task>`、`/subagent run <agent> -- <task>`、`/fullAccess [on|off|status]`、`/accept <id>`、`/reject <id>`、`/undo <id>`、`/plan [on|off|status]`、`/plan approve [plan_id]`、`/plan reject [plan_id]`、`/model`、`/model list`、`/model <name>`、`/<skill_name> [args...]`、`exit`。
输入斜杠命令时支持灰色内联补全（例如输入 `/ful` 会提示补全为 `/fullAccess`，输入 `/subagent s` 会提示 `status`，输入 `/plan a` 会提示 `approve`）。
`/planmode` 与 `/plan_mode` 旧命令别名已移除，请统一使用 `/plan ...`。
Plan 草案文件保存于 `.excelmanus/plans/`。
`subagent` 配置中的 `memory_scope` 已生效，支持 `user` / `project` 两种作用域：
- `user`：`~/.excelmanus/agent-memory/{agent_name}/`
- `project`：`{workspace_root}/.excelmanus/agent-memory/{agent_name}/`
配置解析兼容 `memory_scope` / `memory-scope` / 历史 `memory` 三种写法（冲突配置会被拒绝）。

### 持久记忆

- 全局记忆开关：`EXCELMANUS_MEMORY_ENABLED`（默认 `true`）
- 记忆目录：`EXCELMANUS_MEMORY_DIR`（默认 `~/.excelmanus/memory`）
- 自动加载行数：`EXCELMANUS_MEMORY_AUTO_LOAD_LINES`（默认 `200`，从 `MEMORY.md` 最近行加载）
- 主题文件：
  - `file_patterns.md`
  - `user_prefs.md`
  - `error_solutions.md`
  - `general.md`
- 核心文件 `MEMORY.md` 保留，保存时会与主题文件同步写入（用于会话启动自动加载）。
- 首次启动会自动执行布局迁移并保留备份到 `migration_backups/`（幂等可重入）。
- 若迁移失败，系统会回滚备份并降级为只读加载模式（记录告警日志，不中断主流程）。

`/skills` 子命令示例：

```bash
/skills list
/skills get data_basic
/skills create api_skill --json '{"description":"api 创建","allowed-tools":["read_excel"],"triggers":[],"instructions":"说明"}'
/skills patch api_skill --json '{"description":"api 更新"}'
/skills delete api_skill --yes
```

### Accept 门禁与审计

- 审批策略采用破坏性分层：Tier A（破坏性写入）需 `/accept`，Tier B（可审计写入）默认直行并强制审计。
- 非 `fullAccess` 状态下，Tier A 工具不会立即执行，而是先进入待确认队列。
- MCP 工具仅在白名单内自动批准，非白名单 MCP 会进入待确认队列。
- 使用 `/accept <id>` 执行待确认操作，`/reject <id>` 放弃操作。
- 每次已执行的高风险操作都会在 `outputs/approvals/<id>/` 下保存审计产物（执行成功/失败都会落盘）：
  - `manifest.json`（V2）：`version/approval/execution/artifacts/changes` 分层结构
  - `changes.patch`：文本文件 unified diff（若有）
  - `snapshots/`：回滚快照（按需）
- `manifest.json` V2 为破坏性升级，不再兼容旧平铺字段。
- 对支持回滚的记录可执行 `/undo <id>`，且支持进程重启后按 `approval_id` 回滚。
- `run_code` 仍会进入 accept 流程并落盘审计，但默认不支持自动回滚代码执行副作用。

### API

```bash
excelmanus-api
```

接口：

- `POST /api/v1/chat`
  - 请求：`message`、`session_id?`
  - 响应：`session_id`、`reply`、`skills_used`、`tool_scope`、`route_mode`
  - 错误：`409`（同会话并发冲突）、`429`（会话数超限）
- `GET /api/v1/skills`
  - 响应：Skillpack 摘要列表（`name`、`description`、`source`、`writable`、`argument-hint`）
- `GET /api/v1/skills/{name}`
  - `external_safe_mode=true` 时返回摘要，关闭后返回完整详情（标准字段：如 `allowed-tools`、`command-dispatch`、`hooks` 等）
- `external_safe_mode=false` 时仍会对 `tool_calls.arguments` 与 SSE `tool_call_start.arguments` 进行脱敏处理（token/cookie/绝对路径）。
- `POST /api/v1/skills`
  - 请求：`name` + `payload`
  - 错误：`403`（safe mode 开启）、`409`（冲突）、`422`（payload 非法）
- `PATCH /api/v1/skills/{name}`
  - 请求：`payload`（字段级更新）
  - 错误：`403`（safe mode 开启）、`404`（不存在）、`409`（非 project 来源）、`422`（payload 非法）
- `DELETE /api/v1/skills/{name}`
  - 软删除并归档到 `.excelmanus/skillpacks_archive/`
  - 错误：`403`（safe mode 开启）、`404`（不存在）、`409`（非 project 来源）
- `DELETE /api/v1/sessions/{session_id}`
  - 错误：`404`（会话不存在）、`409`（会话正在处理中）
- `GET /api/v1/health`
  - 响应：`status`、`version`、`tools`、`skillpacks`、`active_sessions`

说明：
- 当 `EXCELMANUS_EXTERNAL_SAFE_MODE=true` 时，`POST/PATCH/DELETE /api/v1/skills*` 会返回 `403`。
- 当 `EXCELMANUS_EXTERNAL_SAFE_MODE=true` 时，`GET /api/v1/skills/{name}` 返回摘要；关闭后返回完整详情（含 `instructions` / `resource_contents` 等字段）。
- `/api/v1/skills*` 的写入请求同时接受 `snake_case` 与 `kebab-case` 字段；响应统一使用标准别名字段（`kebab-case`）。
- Skillpack 写操作仅允许 project 层，system/user 层仅可读取。
- `SessionManager` 会在 API 生命周期内自动启动后台 TTL 清理任务；服务关闭时统一停止并回收会话资源。

SSE 事件协议说明：
- `TASK_LIST_CREATED` 与 `TASK_ITEM_UPDATED` 都统一映射为 `event: task_update`。
- `safe_mode` 开启或关闭时，上述 `task_update` 映射保持不变，仅 payload 字段做安全过滤。

示例：

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "读取 sales.xlsx 前10行"}'
```

## Skillpack 扩展

Skillpack 使用目录结构：

```text
<dir>/<namespace_or_name>/SKILL.md
```

> 协议单一事实源（SSOT）：以 `docs/skillpack_protocol.md` 为准。README 仅保留摘要说明。

`SKILL.md` frontmatter 必填字段：

- `name`
- `description`

可选字段（标准键）：

- `allowed-tools`、`triggers`、`file-patterns`、`resources`
- `priority`、`version`
- `disable-model-invocation`、`user-invocable`
- `argument-hint`
- `hooks`、`model`、`metadata`
- `command-dispatch`（`none`/`tool`）、`command-tool`
- `required-mcp-servers`、`required-mcp-tools`（技能激活前的 MCP 依赖校验）

Hook 配置要点：
- 事件键支持三种写法：`PreToolUse` / `preToolUse` / `pre_tool_use`（其余事件同理）。
- handler 决策优先级：`deny > ask > allow > continue`。
- `ASK` 仅对 `PreToolUse` 生效，其他事件会自动降级为 `continue`。
- `ALLOW` 在 `PreToolUse` 下会跳过确认门禁，但不会绕过 `tool_scope` 与审计约束。
- `command` hook 仅在 `EXCELMANUS_HOOKS_COMMAND_ENABLED=true` 时可执行；
  allowlist 仅允许单段命令，链式命令（`;`/`&&`/`||`/`|`）不会被放行。
- `agent` hook 支持字段：`agent_name`、`task`、`on_failure`、`inject_summary_as_context`。

已移除字段：
- `context`、`agent` 不再支持。
- API `create/patch` 传入上述字段会返回 `422`。
- frontmatter 出现 `context: fork` 会加载失败，请改为常规技能并在执行阶段显式调用 `delegate_to_subagent(agent_name=...)`。

命名规范：

- 支持命名空间（例如 `team/data-cleaner`）
- 分段正则：`[a-z0-9][a-z0-9._-]{0,63}`
- 全名长度最大 255

目录发现与加载优先级（高→低）：

1. workspace 显式目录：`.excelmanus/skillpacks`、`.agents/skills`、`.claude/skills`、`.openclaw/skills`
2. 祖先链 `.agents/skills`（从 workspace 根到 cwd，越近越高）
3. 用户目录：`~/.excelmanus/skillpacks`、`~/.claude/skills`、`~/.openclaw/skills`
4. 系统目录：`excelmanus/skillpacks/system`

同名技能按优先级覆盖，最终只保留一个生效版本。

OpenClaw 项目目录迁移：
- 历史目录 `workspace/skills` 已停止作为 OpenClaw 项目级发现目录。
- 如仍使用旧目录，请迁移到 `workspace/.openclaw/skills`。

兼容说明：

- 历史 `snake_case` frontmatter 仍可作为输入读取（兼容模式）
- 产品行为按新协议执行，推荐统一使用标准 `kebab-case`
- 可使用迁移脚本：

```bash
python scripts/migrate_skills_to_standard.py --workspace-root .
```

当前内置（system）Skillpacks：
- `general_excel`：通用兜底
- `data_basic`：读取/分析/筛选/转换
- `chart_basic`：图表生成
- `format_basic`：样式调整
- `file_ops`：文件操作
- `sheet_ops`：工作表管理与跨表操作
- `excel_code_runner`：写脚本并运行 Python 处理大体量 Excel

## 安全边界

- 所有文件读写仍受 `WORKSPACE_ROOT` 限制
- 路径穿越与符号链接越界会被拒绝
- 代码 Skillpack 默认受限（`excel_code_runner`），仅可通过会话级 `/fullAccess` 临时解锁
- `run_code` 始终使用软沙盒执行（最小环境变量白名单、`-I` 隔离、进程隔离、Unix 资源限制尽力应用）
- `allowed_tools` 两阶段校验
  - Loader 启动期软校验：未知工具仅告警
  - Engine 运行期硬校验：未授权调用返回 `TOOL_NOT_ALLOWED`

## 开发

```bash
pip install -e ".[dev]"
pytest
pytest tests/test_skillpack_docs_contract.py -q
```

## 许可证

MIT
