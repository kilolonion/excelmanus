<p align="center">
  <img src="logo.svg" width="320" alt="ExcelManus" />
</p>

<p align="center">
  <strong>v1.6.0</strong> · 用自然语言操作 Excel
</p>

<p align="center">
  中文 · <a href="README_EN.md">English</a>
</p>

LLM 驱动的 Excel Agent —— 读数据、写公式、跑分析、画图表。支持 OpenAI / Claude / Gemini 等 Provider，从 URL 自动识别。

<p align="center">
  <img src="docs/images/webui-desktop.png" width="800" alt="Web UI 桌面端" />
</p>
<p align="center">桌面端 Web UI — 聊天 + Excel 侧边面板实时预览</p>

<p align="center">
  <img src="docs/images/webui-mobile.png" width="360" alt="Web UI 移动端" />
</p>
<p align="center">移动端 — 对话式交互，工具调用与数据变更一目了然</p>

## 功能

- **读写 Excel** — 单元格、公式、VLOOKUP、批量填充，多 sheet
- **数据分析** — 筛选、排序、聚合、透视表；复杂逻辑生成 Python 脚本执行
- **图表** — 柱状图、折线图、饼图等，嵌入 Excel 或导出图片
- **图片识别** — 表格截图 → 结构化数据，支持数据 + 样式两阶段提取
- **跨表操作** — 创建 / 复制 / 重命名 sheet，跨表搬运
- **版本管理** — staging / audit / CoW 版本链，`/undo` 回滚
- **持久记忆** — 记住偏好和操作模式，跨会话可用
- **Skillpack** — 一个 Markdown 文件 = 一个技能，注入领域知识
- **MCP** — 接入外部 MCP Server 扩展工具
- **Subagent** — 大文件或复杂任务委派子代理
- **多用户** — 独立工作区 / 数据库 / 会话，管理员面板控制权限和用量

## 快速开始

**安装**（Python >= 3.10）

```bash
pip install .
```

创建 `.env`：

```dotenv
EXCELMANUS_API_KEY=your-api-key
EXCELMANUS_BASE_URL=https://your-llm-endpoint/v1
EXCELMANUS_MODEL=your-model-id
```

支持任何 OpenAI 兼容 API。URL 指向 Anthropic / Google 时自动切换原生协议。

> 首次运行后配置迁移到本地数据库，后续通过 `/config` 或 Web UI 管理。

```bash
excelmanus            # CLI
excelmanus-api        # REST API + Web UI 后端
```

```
> 读取 sales.xlsx 前10行
> 把 A 列金额求和写到 B1
> 按地区分组统计销售额，生成柱状图
```

## 使用方式

### CLI

终端对话，支持 Dashboard 布局。

| 命令 | 说明 |
| --- | --- |
| `/help` | 帮助 |
| `/skills` | 技能管理 |
| `/model list` | 切换模型 |
| `/undo <id>` | 回滚操作 |
| `/backup list` | 查看备份 |
| `/rules` | 自定义规则 |
| `/memory` | 记忆管理 |
| `/compact` | 上下文压缩 |
| `/config export` | 加密导出配置 |
| `/config import` | 导入配置 |
| `/clear` | 清空对话 |

输入 `/` 自动补全，打错有纠错。

### Web UI

基于 Next.js + Univer.js。

```bash
excelmanus-api                          # 后端
cd web && npm install && npm run dev    # 前端
```

- SSE 流式响应，实时显示思考过程和工具调用
- 内嵌 Excel 查看器，侧边面板预览编辑，支持选区引用
- 写入操作实时 diff 对比
- 多会话、设置面板、管理员面板
- 文件拖拽、`@` 引用文件 / 技能
- 高风险操作审批确认

### REST API

`excelmanus-api` 启动后可用。

| 接口 | 说明 |
| --- | --- |
| `POST /api/v1/chat/stream` | SSE 流式对话 |
| `POST /api/v1/chat` | JSON 对话 |
| `POST /api/v1/chat/abort` | 终止任务 |
| `GET /api/v1/files/excel` | Excel 文件流 |
| `GET /api/v1/files/excel/snapshot` | Excel JSON 快照 |
| `POST /api/v1/backup/apply` | 应用备份 |
| `GET /api/v1/skills` | 技能列表 |
| `POST /api/v1/config/export` | 导出配置 |
| `GET /api/v1/health` | 健康检查 |

SSE 推送 25 种事件类型（思考、工具调用、子代理、Excel diff、审批等）。

## 模型

| Provider | 说明 |
| --- | --- |
| OpenAI 兼容 | 默认协议 |
| Claude (Anthropic) | URL 含 `anthropic` 自动切换，支持 extended thinking |
| Gemini (Google) | URL 含 `googleapis` / `generativelanguage` 自动切换 |
| OpenAI Responses API | `EXCELMANUS_USE_RESPONSES_API=1` |

可配置辅助模型（AUX）用于路由、子代理和窗口管理。运行时 `/model` 或 Web UI 切换。

## 安全

- **路径沙盒** — 读写限制在工作目录，路径穿越和符号链接越界被拒绝
- **代码静态分析** — `run_code` 按 Green / Yellow / Red 三级审批
- **Docker 沙盒** — 可选容器隔离（`EXCELMANUS_DOCKER_SANDBOX=1`）
- **操作审批** — 高风险写入需 `/accept` 确认，变更留 diff 和快照
- **版本链** — staging / audit / CoW，`/undo` 回滚任意版本
- **MCP 白名单** — 外部工具默认需确认
- **用户隔离** — 多用户模式下工作区和数据库物理隔离

## Skillpack

一个目录 + 一个 `SKILL.md`（含 `name` 和 `description`）即可。自动发现，按需激活。支持 Hook、命令分派、MCP 依赖声明。

内置技能：

| 技能 | 用途 |
| --- | --- |
| `data_basic` | 读取、分析、筛选、转换 |
| `chart_basic` | 图表（内嵌 + 图片） |
| `format_basic` | 样式、条件格式 |
| `file_ops` | 文件管理 |
| `sheet_ops` | 工作表与跨表操作 |
| `excel_code_runner` | Python 脚本处理大文件 |
| `run_code_templates` | 常用代码模板 |

协议详见 `docs/skillpack_protocol.md`。

## Bench

内置评测框架：

```bash
python -m excelmanus.bench --all                         # 全部
python -m excelmanus.bench --suite bench/cases/xxx.json  # 指定 suite
python -m excelmanus.bench --message "读取前10行"          # 单条
```

支持多轮用例、自动断言、JSON 日志、`--trace` 追踪、Suite 并发。

## 部署

### Docker Compose（推荐）

```bash
cp .env.example .env
# 编辑 .env 配置 API Key、模型等

docker compose up -d                          # 启动（后端 + 前端 + PostgreSQL）
docker compose --profile production up -d     # 带 Nginx 反向代理
```

服务启动后访问 `http://localhost`（Nginx）或 `http://localhost:3000`（直接前端）。

### 手动部署（宝塔面板 / 裸机）

适用于不使用 Docker 的场景，详见 [docs/ops-manual.md](docs/ops-manual.md)。

### 远程更新

```bash
./deploy.sh                  # 完整部署
./deploy.sh --backend-only   # 只更新后端
./deploy.sh --frontend-only  # 只更新前端
```

> 自动排除 `.env`、`data/`、`workspace/`，不覆盖线上数据。低内存服务器、Nginx SSE 配置等详见 [docs/ops-manual.md](docs/ops-manual.md)。

## 多用户

```dotenv
EXCELMANUS_AUTH_ENABLED=true
EXCELMANUS_JWT_SECRET=your-random-secret-key-at-least-64-chars
```

支持邮箱密码、GitHub OAuth、Google OAuth 三种登录。每个用户独立工作区和数据库（`users/{user_id}/data.db`），首个注册用户为管理员。

OAuth 等详细配置见 [docs/configuration.md](docs/configuration.md)。

## 配置参考

快速开始只需 3 个环境变量。完整配置（窗口感知、安全策略、Subagent、MCP、VLM、Embedding 等）见 [docs/configuration.md](docs/configuration.md)。

## 开发

```bash
pip install -e ".[dev]"
pytest
```

## 许可证

MIT
