<p align="center">
  <img src="assets/logo.png" width="380" alt="ExcelManus" />
</p>

<h3 align="center">用自然语言驱动 Excel 的 AI Agent</h3>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License" /></a>
  <img src="https://img.shields.io/badge/python-≥3.10-3776AB.svg?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/version-1.6.0-green.svg" alt="Version" />
  <img src="https://img.shields.io/badge/Next.js-15-black?logo=next.js" alt="Next.js" />
</p>

<p align="center">
  <a href="README_EN.md">English</a> · 中文 · <a href="docs/configuration.md">配置文档</a> · <a href="docs/ops-manual.md">运维手册</a>
</p>

<p align="center">
  <img src="docs/images/webui-desktop.png" width="720" alt="Web UI" />
</p>

LLM 驱动的 Excel Agent —— 读数据、写公式、跑分析、画图表。支持 OpenAI / Claude / Gemini 等 Provider。

ExcelManus 是一个 LLM 驱动的 Excel Agent 框架。告诉它你想做什么，它会自动读数据、写公式、跑分析、画图表——支持 CLI 和 Web 双入口，接入 OpenAI / Claude / Gemini 等任意大模型。

## ✨ 核心特性

<table>
<tr>
<td width="50%">

### 📊 读写 Excel
单元格 · 公式 · VLOOKUP · 批量填充 · 多 Sheet 操作

### 📈 数据分析与图表
筛选、排序、聚合、透视表；复杂逻辑自动生成 Python 脚本执行。柱状图、折线图、饼图等嵌入 Excel 或导出图片。

### 🖼️ 图片识别
表格截图 → 结构化数据，4 阶段渐进管线提取数据 + 样式 + 公式

### 🔄 版本管理
Staging / Audit / CoW 版本链，`/undo` 精确回滚到任意操作

</td>
<td width="50%">

### 🧠 持久记忆
跨会话记忆偏好与操作模式，自动调整行为

### 🧩 Skillpack
一个 Markdown = 一个技能。自动发现、按需激活、支持 Hook 和命令分派

### 🔌 MCP & Subagent
接入外部 MCP Server 扩展工具集；大文件和复杂任务自动委派子代理

### 👥 多用户
独立工作区 / 数据库 / 会话隔离，管理员面板管控权限和用量

</td>
</tr>
</table>

## 🚀 快速开始

**1. 安装**

```bash
pip install .
```

**2. 配置** — 创建 `.env`，只需 3 个变量：

```dotenv
EXCELMANUS_API_KEY=your-api-key
EXCELMANUS_BASE_URL=https://your-llm-endpoint/v1
EXCELMANUS_MODEL=your-model-id
```

> 支持任何 OpenAI 兼容 API。URL 包含 `anthropic` 或 `googleapis` 时自动切换原生协议。

**3. 启动**

```bash
excelmanus            # CLI 模式
excelmanus-api        # Web UI + REST API
```

或使用一键启动脚本（同时启动后端 + 前端）：

```bash
./deploy/start.sh                    # macOS / Linux 开发模式
./deploy/start.sh --prod             # 生产模式
./deploy/start.sh --backend-port 9000  # 自定义端口
```

Windows 用户：

```powershell
.\deploy\start.ps1                   # PowerShell
deploy\start.bat                     # CMD
```

**试一试：**

```
> 读取 sales.xlsx 前10行
> 把 A 列金额求和写到 B1
> 按地区分组统计销售额，生成柱状图
```

> 首次运行后配置迁移到本地数据库，后续可通过 `/config` 命令或 Web UI 设置面板管理。

## 💻 使用方式

### CLI

终端对话，支持 Dashboard 布局，`/` 自动补全，打错有纠错。

<details>
<summary>📋 常用命令</summary>

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

</details>

### Web UI

基于 Next.js + Univer.js，提供完整的可视化操作体验。

```bash
# 方式一：一键启动（推荐）
./deploy/start.sh

# 方式二：分别启动
excelmanus-api                          # 后端
cd web && npm install && npm run dev    # 前端
```

- **SSE 流式响应** — 实时显示思考过程、工具调用、子代理执行
- **Excel 侧边面板** — 内嵌查看器，实时预览编辑，支持选区引用
- **写入 Diff** — 每次修改前后对比，一目了然
- **多会话** — 历史持久化，切换无缝
- **文件交互** — 拖拽上传、`@` 引用文件和技能
- **审批机制** — 高风险操作弹窗确认

<p align="center">
  <img src="docs/images/webui-mobile.png" width="300" alt="移动端" />
</p>
<p align="center"><sub>移动端同样可用 — 响应式布局适配</sub></p>

### REST API

`excelmanus-api` 启动后即可使用，SSE 推送 25+ 种事件类型。

<details>
<summary>📋 主要接口</summary>

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

</details>

## 🤖 模型支持

| Provider | 说明 |
| --- | --- |
| **OpenAI 兼容** | 默认协议，支持任何兼容 API |
| **Claude (Anthropic)** | URL 含 `anthropic` 自动切换，支持 extended thinking |
| **Gemini (Google)** | URL 含 `googleapis` / `generativelanguage` 自动切换 |
| **OpenAI Responses API** | `EXCELMANUS_USE_RESPONSES_API=1` 启用 |

可配置**辅助模型（AUX）**用于路由、子代理和窗口管理，主模型与辅助模型独立切换。

## 🔒 安全机制

| 机制 | 说明 |
| --- | --- |
| **路径沙盒** | 读写限制在工作目录，路径穿越和符号链接越界被拒绝 |
| **代码审查** | `run_code` 静态分析，按 Green / Yellow / Red 三级自动审批 |
| **Docker 沙盒** | 可选容器隔离（`EXCELMANUS_DOCKER_SANDBOX=1`） |
| **操作审批** | 高风险写入需确认，变更自动记录 diff 和快照 |
| **版本链** | Staging → Audit → CoW，`/undo` 回滚任意版本 |
| **MCP 白名单** | 外部工具默认需逐项确认 |
| **用户隔离** | 多用户模式下工作区和数据库物理隔离 |

## 🧩 Skillpack

一个目录 + 一个 `SKILL.md`（含 `name` 和 `description`）即可创建技能。自动发现，按需激活，支持 Hook、命令分派、MCP 依赖声明。

<details>
<summary>📦 内置技能</summary>

| 技能 | 用途 |
| --- | --- |
| `data_basic` | 读取、分析、筛选、转换 |
| `chart_basic` | 图表（内嵌 + 图片） |
| `format_basic` | 样式、条件格式 |
| `file_ops` | 文件管理 |
| `sheet_ops` | 工作表与跨表操作 |
| `excel_code_runner` | Python 脚本处理大文件 |
| `run_code_templates` | 常用代码模板 |

</details>

协议详见 [`docs/skillpack_protocol.md`](docs/skillpack_protocol.md)。

## 🏗️ 部署

### Docker Compose（推荐）

```bash
cp .env.example .env                      # 编辑 API Key、模型等
docker compose -f deploy/docker-compose.yml up -d   # 后端 + 前端 + PostgreSQL
```

访问 `http://localhost:3000`。加 `--profile production` 启用 Nginx 反向代理后访问 `http://localhost`。

### 手动部署

适用于宝塔面板 / 裸机等不使用 Docker 的场景，详见 [运维手册](docs/ops-manual.md)。

### 一键启动（本地开发）

```bash
# macOS / Linux
./deploy/start.sh              # 开发模式
./deploy/start.sh --prod       # 生产模式（npm run start）
./deploy/start.sh --workers 4  # 多 worker

# Windows PowerShell
.\deploy\start.ps1 -Production

# Windows CMD
deploy\start.bat --prod
```

支持 `--backend-port`、`--frontend-port`、`--log-dir`、`--backend-only`、`--frontend-only` 等选项，详见 `./deploy/start.sh --help`。

脚本自动检测操作系统（macOS / Linux / Windows），在 Linux 上自动识别 apt / dnf / yum / pacman 等包管理器并给出安装提示。

### 远程部署 (deploy.sh / deploy.ps1)

部署脚本在本地运行，通过 SSH 操作远程服务器。支持单机、前后端分离、Docker、本地四种拓扑。

```bash
# 基本部署
./deploy/deploy.sh                         # 完整部署
./deploy/deploy.sh --backend-only          # 只更新后端
./deploy/deploy.sh --frontend-only         # 只更新前端

# 首次部署：推送 .env 模板到远程服务器
./deploy/deploy.sh init-env

# 运维命令
./deploy/deploy.sh check                   # 环境检查 + 前后端互联检测
./deploy/deploy.sh status                  # 查看运行状态
./deploy/deploy.sh rollback                # 回滚上一版本
./deploy/deploy.sh history                 # 部署历史
./deploy/deploy.sh logs                    # 部署日志
```

Windows PowerShell：

```powershell
.\deploy\deploy.ps1                        # 完整部署
.\deploy\deploy.ps1 init-env               # 推送 .env 模板
.\deploy\deploy.ps1 check                  # 环境检查
.\deploy\deploy.ps1 rollback -Force        # 回滚（跳过确认）
```

> 自动排除 `.env`、`data/`、`workspace/`，不覆盖线上数据。部署后自动检测前后端互联、CORS 配置和健康检查。

## 👥 多用户

```dotenv
EXCELMANUS_AUTH_ENABLED=true
EXCELMANUS_JWT_SECRET=your-random-secret-key-at-least-64-chars
```

支持**邮箱密码**、**GitHub OAuth**、**Google OAuth** 三种登录方式。每个用户拥有独立的工作区和数据库，首个注册用户自动成为管理员。

详细配置见 [配置文档](docs/configuration.md)。

## 🧪 评测框架

内置 Bench 评测，支持多轮用例、自动断言、JSON 日志和 Suite 并发：

```bash
python -m excelmanus.bench --all                         # 全部
python -m excelmanus.bench --suite bench/cases/xxx.json  # 指定 suite
python -m excelmanus.bench --message "读取前10行"          # 单条
```

## 📖 配置参考

快速开始只需 3 个环境变量。完整配置（窗口感知、安全策略、Subagent、MCP、VLM、Embedding 等）见 [配置文档](docs/configuration.md)。

## 🖥️ 平台支持

| 平台 | 状态 | 说明 |
| --- | --- | --- |
| **macOS** | ✅ 完整支持 | 开发主平台 |
| **Linux** | ✅ 完整支持 | Ubuntu / Debian / CentOS / Fedora / Arch 等 |
| **Windows** | ✅ 完整支持 | PowerShell 5.1+ 或 CMD，需安装 Python + Node.js |

启动脚本自动检测 OS 和包管理器，缺少依赖时给出精确的安装命令。

## 🛠️ 开发

```bash
pip install -e ".[dev]"
pytest
```

## 📄 许可证

[Apache License 2.0](LICENSE) © kilolonion
