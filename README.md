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
| `EXCELMANUS_SKILLS_PREFILTER_TOPK` | 预筛候选数量 | `6` |
| `EXCELMANUS_SKILLS_MAX_SELECTED` | 每轮最多命中技能包数 | `3` |
| `EXCELMANUS_SKILLS_SKIP_LLM_CONFIRM` | 是否跳过 LLM 二次确认 | `false` |
| `EXCELMANUS_SKILLS_FASTPATH_MIN_SCORE` | 快速路径最低分 | `6` |
| `EXCELMANUS_SKILLS_FASTPATH_MIN_GAP` | 快速路径分差阈值 | `3` |
| `EXCELMANUS_SYSTEM_MESSAGE_MODE` | system 注入策略（`auto\|multi\|merge`） | `auto` |
| `EXCELMANUS_LARGE_EXCEL_THRESHOLD_BYTES` | 触发大文件 fork 提示的阈值（字节） | `8388608` |
| `EXCELMANUS_SUBAGENT_ENABLED` | 是否启用 fork 子代理执行 | `true` |
| `EXCELMANUS_SUBAGENT_MODEL` | fork 子代理模型（为空时回退主模型） | — |
| `EXCELMANUS_SUBAGENT_MAX_ITERATIONS` | fork 子代理最大迭代轮数 | `6` |
| `EXCELMANUS_SUBAGENT_MAX_CONSECUTIVE_FAILURES` | fork 子代理连续失败熔断阈值 | `2` |

## 使用方式

### CLI

```bash
excelmanus
# 或
python -m excelmanus
```

可用命令：`/help`、`/history`、`/clear`、`/skills`、`/subagent [on|off|status]`、`/fullAccess [on|off|status]`、`/<skill_name> [args...]`、`exit`。
输入斜杠命令时支持灰色内联补全（例如输入 `/ful` 会提示补全为 `/fullAccess`，输入 `/subagent s` 会提示 `status`）。

### API

```bash
excelmanus-api
```

接口：

- `POST /api/v1/chat`
  - 请求：`message`、`session_id?`、`skill_hints?`
  - 响应：`session_id`、`reply`、`skills_used`、`tool_scope`、`route_mode`
- `DELETE /api/v1/sessions/{session_id}`
- `GET /api/v1/health`
  - 响应：`status`、`version`、`tools`、`skillpacks`

示例：

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "读取 sales.xlsx 前10行", "skill_hints": ["data_basic"]}'
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
- `triggers`

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
- `run_python_script` 始终使用软沙盒执行（最小环境变量白名单、`-I` 隔离、进程隔离、Unix 资源限制尽力应用）
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
