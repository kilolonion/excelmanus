# ExcelManus v3

基于大语言模型的 Excel 智能代理框架（`Tools + Skillpacks` 双层架构）。

- `Tools`：基础能力执行层（工具函数 + schema + 安全边界）
- `Skillpacks`：策略编排层（`SKILL.md` 元数据 + 路由 + `allowed_tools` 授权）

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
| `EXCELMANUS_BASE_URL` | LLM API 地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `EXCELMANUS_MODEL` | 模型名称 | `qwen-max-latest` |
| `EXCELMANUS_MAX_ITERATIONS` | Agent 最大迭代轮数 | `20` |
| `EXCELMANUS_MAX_CONSECUTIVE_FAILURES` | 连续失败熔断阈值 | `3` |
| `EXCELMANUS_SESSION_TTL_SECONDS` | API 会话空闲超时（秒） | `1800` |
| `EXCELMANUS_MAX_SESSIONS` | API 最大并发会话数 | `1000` |
| `EXCELMANUS_WORKSPACE_ROOT` | 文件访问白名单根目录 | `.` |
| `EXCELMANUS_LOG_LEVEL` | 日志级别 | `INFO` |
| `EXCELMANUS_EXTERNAL_SAFE_MODE` | 对外安全模式（隐藏思考/工具细节与路由元信息） | `true` |

### Skillpack 路由配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `EXCELMANUS_SKILLS_SYSTEM_DIR` | 内置 Skillpacks 目录 | `excelmanus/skillpacks/system` |
| `EXCELMANUS_SKILLS_USER_DIR` | 用户级 Skillpacks 目录 | `~/.excelmanus/skillpacks` |
| `EXCELMANUS_SKILLS_PROJECT_DIR` | 项目级 Skillpacks 目录 | `<workspace_root>/.excelmanus/skillpacks` |
| `EXCELMANUS_SYSTEM_MESSAGE_MODE` | system 注入策略（`auto\|multi\|merge`） | `auto` |
| `EXCELMANUS_LARGE_EXCEL_THRESHOLD_BYTES` | 触发大文件 fork 提示的阈值（字节） | `8388608` |
| `EXCELMANUS_SUBAGENT_ENABLED` | 是否启用 fork 子代理执行 | `true` |
| `EXCELMANUS_SUBAGENT_MODEL` | fork 子代理模型（为空时回退主模型） | — |
| `EXCELMANUS_SUBAGENT_MAX_ITERATIONS` | fork 子代理最大迭代轮数 | `6` |
| `EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES` | fork 子代理连续失败熔断阈值 | `2` |

v3 路由简化迁移说明：
- 以下旧变量在 v3 已不生效，现已正式移除：`EXCELMANUS_SKILLS_PREFILTER_TOPK`、`EXCELMANUS_SKILLS_MAX_SELECTED`、`EXCELMANUS_SKILLS_SKIP_LLM_CONFIRM`、`EXCELMANUS_SKILLS_FASTPATH_MIN_SCORE`、`EXCELMANUS_SKILLS_FASTPATH_MIN_GAP`。
- 当前路由行为以 `slash_direct` 与 `fallback/no_skillpack/slash_not_found` 两类主路径为核心，运行时工具权限由 `tool_scope` 严格约束。

### MCP 启动缓存（避免每次重装）

项目根目录 `mcp.json` 已改为使用 `scripts/mcp/*.sh` 启动器：

- 首次启动时按固定版本自动安装到 `./.excelmanus/mcp/`
- 后续启动直接复用本地缓存，不再每次通过 `npx/uvx` 在线安装
- 如需强制重装，删除 `./.excelmanus/mcp/` 后重启即可

可通过环境变量 `EXCELMANUS_MCP_STATE_DIR` 自定义缓存目录。

## 使用方式

### CLI

```bash
excelmanus
# 或
python -m excelmanus
```

可用命令：`/help`、`/history`、`/clear`、`/skills`、`/skills list`、`/skills get <name>`、`/skills create <name> --json ... | --json-file ...`、`/skills patch <name> --json ... | --json-file ...`、`/skills delete <name> [--yes]`、`/subagent [on|off|status|list]`、`/subagent run -- <task>`、`/subagent run <agent> -- <task>`、`/fullAccess [on|off|status]`、`/accept <id>`、`/reject <id>`、`/undo <id>`、`/plan [on|off|status]`、`/plan approve [plan_id]`、`/plan reject [plan_id]`、`/<skill_name> [args...]`、`exit`。
输入斜杠命令时支持灰色内联补全（例如输入 `/ful` 会提示补全为 `/fullAccess`，输入 `/subagent s` 会提示 `status`，输入 `/plan a` 会提示 `approve`）。

`/skills` 子命令示例：

```bash
/skills list
/skills get data_basic
/skills create api_skill --json '{"description":"api 创建","allowed_tools":["read_excel"],"triggers":[],"instructions":"说明"}'
/skills patch api_skill --json '{"description":"api 更新"}'
/skills delete api_skill --yes
```

### Accept 门禁与审计

- 非 `fullAccess` 状态下，高风险写操作不会立即执行，而是先进入待确认队列。
- 使用 `/accept <id>` 执行待确认操作，`/reject <id>` 放弃操作。
- 每次已执行的高风险操作都会在 `outputs/approvals/<id>/` 下保存审计产物：
  - `manifest.json`：操作元数据与变更摘要
  - `changes.patch`：文本文件 unified diff（若有）
  - `snapshots/`：回滚快照（按需）
- 对支持回滚的记录可执行 `/undo <id>`。
- `run_code` 仍会进入 accept 流程并落盘审计，但默认不支持自动回滚代码执行副作用。

### API

```bash
excelmanus-api
```

接口：

- `POST /api/v1/chat`
  - 请求：`message`、`session_id?`
  - 响应：`session_id`、`reply`、`skills_used`、`tool_scope`、`route_mode`
- `GET /api/v1/skills`
  - 响应：Skillpack 摘要列表（`name`、`description`、`source`、`writable`、`argument_hint`）
- `GET /api/v1/skills/{name}`
  - `external_safe_mode=true` 时返回摘要，关闭后返回完整详情
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
- `GET /api/v1/health`
  - 响应：`status`、`version`、`tools`、`skillpacks`

说明：
- 当 `EXCELMANUS_EXTERNAL_SAFE_MODE=true` 时，`POST/PATCH/DELETE /api/v1/skills*` 会返回 `403`。
- 当 `EXCELMANUS_EXTERNAL_SAFE_MODE=true` 时，`GET /api/v1/skills/{name}` 返回摘要；关闭后返回完整详情（含 `instructions` / `resource_contents` 等字段）。
- Skillpack 写操作仅允许 project 层，system/user 层仅可读取。

示例：

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "读取 sales.xlsx 前10行"}'
```

## Skillpack 扩展

Skillpack 使用目录结构：

```text
<dir>/<skill_name>/SKILL.md
```

`SKILL.md` frontmatter 必填字段：

- `name`
- `description`
- `allowed_tools`
- `triggers`（必填，但允许空列表 `[]`，用于“仅兜底不触发”的技能）

可选字段：`file_patterns`、`resources`、`priority`、`version`、`disable_model_invocation`、`user_invocable`。

加载优先级：`project > user > system`。

当前内置（system）Skillpacks：
- `general_excel`：通用兜底
- `data_basic`：读取/分析/筛选/转换
- `chart_basic`：图表生成
- `format_basic`：样式调整
- `file_ops`：文件操作
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
```

## 许可证

MIT
