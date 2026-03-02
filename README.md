<p align="center">
  <img src="web/public/logo.svg" width="380" alt="ExcelManus" />
</p>

<h3 align="center">用自然语言驱动 Excel 的 AI Agent</h3>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License" /></a>
  <img src="https://img.shields.io/badge/python-≥3.10-3776AB.svg?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/version-1.6.8-green.svg" alt="Version" />
  <img src="https://img.shields.io/badge/Next.js-16-black?logo=next.js" alt="Next.js" />
</p>

<p align="center">
  <a href="README_EN.md">English</a> · 中文 · <a href="docs/configuration.md">配置文档</a> · <a href="docs/ops-manual.md">运维手册</a>
</p>

<p align="center">
  <img src="docs/images/webui-desktop.png" width="720" alt="Web UI" />
</p>

ExcelManus 是一个 LLM 驱动的 Excel Agent 框架。告诉它你想做什么，它会自动读数据、写公式、跑分析、画图表——支持 CLI 和 Web 双入口，接入 OpenAI / Claude / Gemini 等任意大模型。

## ✨ 核心特性

<table>
<tr>
<td width="50%">

### 📊 读写 Excel
单元格 · 公式 · VLOOKUP · 批量填充 · 多 Sheet 操作；支持 `.xlsx` / `.xls` / `.xlsb` / `.csv` 全格式自动转换

### 📈 数据分析与图表
筛选、排序、聚合、透视表；复杂逻辑自动生成 Python 脚本执行。柱状图、折线图、饼图等嵌入 Excel 或导出图片。

### 🖼️ 视觉识别与提取
表格截图 → 结构化数据；4 阶段渐进管线（骨架→数据→样式→公式），支持单轮合并提取与大表格分区提取

### 🔄 版本管理 & Diff
Staging / Audit / CoW 版本链，`/undo` 精确回滚；Excel 修改前后 Diff 可视化；文本文件精准编辑 + unified diff 展示

</td>
<td width="50%">

### 🧠 持久记忆 & Playbook
跨会话记忆偏好与操作模式；Playbook 自动归纳任务经验，失败教训复用到后续任务

### 🧩 Skillpack & ClawHub 市场
一个 Markdown = 一个技能。自动发现、按需激活；内置 [ClawHub](https://clawhub.ai) 技能市场，一键搜索/安装/更新社区技能

### 🔌 MCP & Subagent
接入外部 MCP Server 扩展工具集；大文件和复杂任务自动委派子代理；支持 OpenAI Codex 订阅私有工具

### ✅ 验证门控
结构化验证条件（行数/Sheet 存在/公式/值匹配），任务完成前自动校验，阻断带未通过验证条件的任务

### 📤 会话导出 & 历史
支持导出 Markdown / 纯文本 / EMX (JSON) 三种格式；会话历史持久化（SQLite + IndexedDB 三级缓存），刷新不丢失

### 👥 多用户 & 管理
独立工作区 / 数据库 / 会话隔离；管理员面板按用户/模型可视化 LLM 用量；支持 OAuth 订阅凭证管理

</td>
</tr>
</table>

## 🚀 快速开始

### 方式一：一键启动（推荐）

最简单的方式，自动安装依赖、启动后端和前端。

**第一步：克隆项目**

```bash
# 国内推荐 Gitee（更快）
git clone https://gitee.com/kilolonion/excelmanus.git
# 或使用 GitHub
# git clone https://github.com/kilolonion/excelmanus.git
cd excelmanus
```

**第二步：启动**

<details open>
<summary><b>Windows — 图形化部署工具</b></summary>

**无需提前 clone 仓库**，直接从 [Gitee Releases](https://gitee.com/kilolonion/excelmanus/releases) 或 [GitHub Releases](https://github.com/kilolonion/excelmanus/releases) 下载 `ExcelManus.exe` 双击运行即可。

工具采用**两步向导式界面**，全程引导完成部署：

1. **Step 1 — 环境检测**：自动检测 Python、Node.js、Git，缺失时通过 winget 自动安装，失败时提供手动下载链接
2. **Step 2 — 一键部署**：自动克隆仓库、安装后端/前端依赖、启动服务，进度条实时展示当前步骤

部署完成后浏览器会自动打开 **http://localhost:3000**。

> **国内网络友好**：GitHub 克隆失败自动回退 Gitee 镜像；npm 默认使用淘宝镜像源；pip 失败回退清华源。
> 该工具为零依赖单文件 exe，基于 .NET Framework 4.0（Windows 内置）。源码：`deploy/ExcelManusSetup.cs`。
> 也可以先 `git clone` 后将 exe 放入项目根目录运行，此时跳过克隆步骤。

</details>

<details>
<summary><b>macOS / Linux — 启动脚本</b></summary>

```bash
chmod +x ./deploy/start.sh  # 首次使用需添加执行权限
./deploy/start.sh
```

首次启动时脚本会交互式提示填写大模型配置（API Key、Base URL、模型名称），之后再启动无需重复配置。

启动成功后浏览器会自动打开 **http://localhost:3000**。

**常用选项：**

```bash
./deploy/start.sh --prod             # 生产模式（性能更好）
./deploy/start.sh --backend-port 9000  # 自定义后端端口
./deploy/start.sh --workers 4         # 多 worker
./deploy/start.sh --backend-only      # 只启动后端
./deploy/start.sh --help               # 查看全部选项
```

</details>

**第三步：开始对话**

在 Web UI 或 CLI 中输入自然语言指令：

```
> 读取 sales.xlsx 前10行
> 把 A 列金额求和写到 B1
> 按地区分组统计销售额，生成柱状图
```

> 首次运行后配置会迁移到本地数据库，后续可通过 Web UI 设置面板或 `/config` 命令管理。

---

### 方式二：手动安装（uv，推荐）

适合已有 Python 环境（≥3.10）、想精确控制依赖的用户。使用 [uv](https://docs.astral.sh/uv/) 管理依赖，速度比 pip 快 10-100x。

**1. 安装 uv（如尚未安装）**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
# Windows: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**2. 克隆并安装**

```bash
# 国内推荐 Gitee（更快）
git clone https://gitee.com/kilolonion/excelmanus.git
# 或使用 GitHub
# git clone https://github.com/kilolonion/excelmanus.git
cd excelmanus
uv sync --all-extras          # 完整安装（CLI + Web + 全部可选依赖）
# 或者按需选择：
# uv sync --extra cli          # 仅 CLI 模式（轻量，不含 Web UI）
# uv sync --extra web          # 仅 Web API 模式（不含 CLI dashboard）
```

> 也支持传统 pip：`pip install ".[all]"`

**2. 创建配置文件**

```bash
cp .env.example .env
```

用编辑器打开 `.env`，找到最上方的 3 行必填项并修改：

```dotenv
EXCELMANUS_API_KEY=sk-xxxxxxxxxxxxxxxx        # 你的 LLM API Key
EXCELMANUS_BASE_URL=https://api.openai.com/v1  # 模型接口地址
EXCELMANUS_MODEL=gpt-4o                        # 模型名称
```

> 其余配置均有默认值，初次使用无需修改。完整配置说明见 [配置文档](docs/configuration.md)。

**3. 启动**

```bash
uv run excelmanus            # CLI 终端交互模式
uv run excelmanus-api        # Web API 模式（后端监听 http://localhost:8000）
```

如需 Web UI 前端，还需单独启动：

```bash
cd web && npm install && npm run dev    # 前端开发服务器（http://localhost:3000）
```

## 💻 使用方式

### CLI

终端对话，支持 Dashboard 布局，`/` 自动补全，打错有纠错。

<details>
<summary>📋 常用命令</summary>

| 命令 | 说明 |
| --- | --- |
| `/help` | 帮助 |
| `/skills` | 技能管理（列出 / 安装 / 激活 / 禁用） |
| `/clawhub search <关键词>` | 在 ClawHub 市场搜索技能 |
| `/clawhub install <slug>` | 安装市场技能 |
| `/clawhub update` | 更新已安装技能 |
| `/model list` | 切换模型 |
| `/model aux` | 配置辅助模型（AUX） |
| `/plan` | 切换 Plan 模式 |
| `/undo <id>` | 回滚操作 |
| `/registry` | 查看文件注册表 |
| `/backup list` | 查看备份 |
| `/rules` | 自定义规则 |
| `/memory` | 记忆管理 |
| `/playbook` | 查看 / 管理任务经验 Playbook |
| `/compact` | 上下文压缩 |
| `/config export` | 加密导出配置 |
| `/config import` | 导入配置 |
| `/export` | 导出会话（Markdown / 纯文本 / EMX） |
| `/clear` | 清空对话 |
| `/rollback` | 回滚会话到指定轮次 |

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

- **SSE 流式响应** — 实时显示思考过程、工具调用、子代理执行；断连后自动重连并恢复消息
- **Excel 侧边面板** — 内嵌 Univer 查看器，实时预览编辑，支持选区引用；侧边栏快捷文件栏，双击进入全屏模式
- **Excel Diff** — 每次写入前后对比，行/列/值变化一目了然；文本文件同样展示 unified diff
- **多会话** — 历史持久化（SQLite + IndexedDB 三级缓存），刷新/重启不丢失，切换无缝
- **文件交互** — 拖拽上传、`@` 引用文件和技能；`.xls` / `.xlsb` 自动转换
- **审批机制** — 高风险操作弹窗确认，变更自动记录快照
- **乐观 UI** — 消息发送立即显示，写操作乐观更新 + 失败回滚
- **错误引导** — 失败时展示可操作建议卡片（重试 / 检查设置 / 复制诊断 ID）
- **ClawHub 市场** — 侧边栏内嵌技能市场面板，搜索/安装/更新
- **管理后台** — 用户管理 + 按提供商/模型可视化 LLM 用量
- **Plan 模式** — 复杂任务自动规划，可交互确认后执行

<p align="center">
  <img src="docs/images/webui-mobile.png" width="300" alt="移动端" />
</p>
<p align="center"><sub>移动端同样可用 — 响应式布局适配</sub></p>

### REST API

`excelmanus-api` 启动后即可使用，SSE 推送 30+ 种事件类型（含 `excel_diff` / `text_diff` / `failure_guidance` 等）。

<details>
<summary>📋 主要接口</summary>

| 接口 | 说明 |
| --- | --- |
| `POST /api/v1/chat/stream` | SSE 流式对话 |
| `POST /api/v1/chat` | JSON 对话 |
| `POST /api/v1/chat/abort` | 终止任务 |
| `POST /api/v1/chat/subscribe` | 重连并恢复会话流 |
| `POST /api/v1/chat/rollback` | 回滚会话到指定轮次 |
| `GET /api/v1/sessions` | 会话列表（支持归档过滤） |
| `GET /api/v1/sessions/{id}/messages` | 分页获取历史消息 |
| `GET /api/v1/files/excel` | Excel 文件流（Univer 加载） |
| `GET /api/v1/files/excel/snapshot` | Excel JSON 快照（内嵌预览） |
| `POST /api/v1/files/excel/write` | 侧边面板回写 |
| `POST /api/v1/backup/apply` | 应用备份 |
| `GET /api/v1/skills` | 技能列表 |
| `GET /api/v1/clawhub/search` | ClawHub 市场搜索 |
| `POST /api/v1/clawhub/install` | 安装市场技能 |
| `GET /api/v1/clawhub/updates` | 检查可用更新 |
| `GET /api/v1/auth/codex/status` | Codex 连接状态 |
| `POST /api/v1/config/export` | 导出配置 |
| `GET /api/v1/health` | 健康检查 |

</details>

## 🤖 模型支持

| Provider | 说明 |
| --- | --- |
| **OpenAI 兼容** | 默认协议，支持任何 OpenAI 兼容 API（本地 Ollama / vLLM 等均可） |
| **Claude (Anthropic)** | URL 含 `anthropic` 自动切换，支持 extended thinking |
| **Gemini (Google)** | URL 含 `googleapis` / `generativelanguage` 自动切换 |
| **OpenAI Responses API** | 新一代推理 API，`EXCELMANUS_USE_RESPONSES_API=1` 启用 |
| **OpenAI Codex 订阅** | 通过 Device Code Flow 连接 Codex 账号，私有模型自动发现，无需手填 API Key |
| **MiniMax** | 自动检测 base_url，内置推荐模型列表（M2.5 / M2.1 / M2） |

### 辅助模型（AUX）

可独立配置辅助模型用于**意图路由、子代理、窗口感知顾问**，主模型与辅助模型互不影响：

```dotenv
EXCELMANUS_AUX_API_KEY=sk-xxxx
EXCELMANUS_AUX_BASE_URL=https://api.openai.com/v1
EXCELMANUS_AUX_MODEL=gpt-4o-mini
```

辅助模型可比主模型更轻量，在不影响任务质量的前提下显著降低成本。

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

## 🧩 Skillpack & ClawHub

一个目录 + 一个 `SKILL.md`（含 `name` 和 `description`）即可创建技能。自动发现，按需激活，支持 Hook、命令分派、MCP 依赖声明。

### ClawHub 技能市场

内置 [ClawHub](https://clawhub.ai) 集成，直接在 UI 侧边栏或 CLI 搜索、安装、更新社区发布的技能包：

```bash
/clawhub search 财务报表      # 搜索市场技能
/clawhub install <slug>       # 安装
/clawhub update               # 更新全部已安装技能
```

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

## 🎯 验证门控

为任务子项添加**结构化验证条件**，任务完成前自动执行校验：

```
> 把销售数据导入 Sheet1，要求：行数等于源文件、B 列有 SUM 公式、C1 值为 "合计"
```

支持的验证类型：`row_count` / `sheet_exists` / `formula_exists` / `value_match` / `custom`

带未通过验证条件的任务会**阻断 finish_task**，强制 Agent 修复后才能完成。

## 🧠 Playbook — 任务经验归纳

Playbook 会自动分析每轮任务的成功/失败模式，提炼为可复用的操作经验：

- **自动归纳**：任务结束后后台生成 PlaybookDelta，写入 SQLite 存储
- **语义去重**：相似经验合并，过时经验淘汰
- **自动注入**：后续相关任务开始前自动注入匹配的 Playbook 条目，减少重复犯错
- **管理命令**：`/playbook list` 查看、`/playbook clear` 清空

## 🏗️ 部署

### Windows 图形化部署工具

Windows 用户可直接从 [Gitee Releases](https://gitee.com/kilolonion/excelmanus/releases) 或 [GitHub Releases](https://github.com/kilolonion/excelmanus/releases) 下载 `ExcelManus.exe`，**无需提前 clone 仓库**。

```text
ExcelManus.exe    ← 下载后双击即可，放在任意目录
```

工具采用**两步向导式界面**，全程引导完成部署。Setup UI 已重构为 **Vite + React + Tailwind CSS**，体验更现代：

| 步骤 | 说明 |
|------|------|
| **Step 1 — 环境检测** | 自动检测 Python（≥3.10）、Node.js（≥18）、Git，缺失时 winget 自动安装，失败提供手动下载链接 |
| **Step 2 — 一键部署** | 克隆仓库 → 安装依赖 → 启动服务，进度条 + 可折叠日志；自动检查版本一致性 |

**国内网络优化**：
- Git clone 先试 GitHub，失败自动回退 **Gitee 镜像**
- npm install 默认使用**淘宝镜像源**（npmmirror），失败回退官方源
- uv/pip 安装失败自动回退**清华镜像源**

> 该工具为纯 C# 编译的单文件 exe，基于 .NET Framework 4.0（Windows 内置），零外部依赖。源码位于 `deploy/ExcelManusSetup.cs`。

### Docker 部署（推荐）

镜像已发布到 Docker Hub，支持 **amd64**（Intel/AMD）和 **arm64**（Apple Silicon / AWS Graviton）双架构，`docker pull` 时自动匹配：

```bash
docker pull kilol/excelmanus-api:1.6.8       # 后端 API
docker pull kilol/excelmanus-sandbox:1.6.8   # 代码沙盒（可选）
docker pull kilol/excelmanus-web:1.6.8       # 前端 Web
```

#### Docker Compose 一键启动

```bash
cp .env.example .env                      # 编辑 API Key、模型等
docker compose -f deploy/docker-compose.yml up -d   # 后端 + 前端 + PostgreSQL
```

访问 `http://localhost:3000`。加 `--profile production` 启用 Nginx 反向代理后访问 `http://localhost`。

#### 多平台镜像构建

如需自行构建多架构镜像：

```bash
# Linux / macOS
./deploy/build_multiarch.sh --push

# Windows
deploy\build_multiarch.bat --push
```

默认构建 `linux/amd64` + `linux/arm64`，可通过环境变量 `REGISTRY`、`PLATFORMS` 自定义。

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

# 前后端分离 + 双密钥
./deploy/deploy.sh --backend-host 1.2.3.4 --frontend-host 5.6.7.8 \
    --backend-key ~/.ssh/backend.pem --frontend-key ~/.ssh/frontend.pem

# 低内存服务器：本地构建前端制品后上传
./deploy/deploy.sh --frontend-only --frontend-artifact ./frontend-standalone.tar.gz

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

<details>
<summary>部署安全机制</summary>

脚本内置三层防护，避免部署导致线上 502：

| 层 | 机制 | 说明 |
| --- | --- | --- |
| **构建退出码** | 不使用 `\| tail` 管道 | 避免管道吞掉 `npm run build` 的真实退出码 |
| **产物校验** | 校验 BUILD_ID + routes-manifest.json | Turbopack 产物不完整时拒绝重启 |
| **启动降级** | 自动检测 standalone vs next start | 无论 Next.js 是否生成 standalone 都能启动 |

构建失败或产物不完整时，脚本会中止并保留当前运行版本，不会触发 PM2 重启。

</details>

> 自动排除 `.env`、`data/`、`workspace/`，不覆盖线上数据。部署后自动检测前后端互联、CORS 配置和健康检查。

## 👥 多用户

```dotenv
EXCELMANUS_AUTH_ENABLED=true
EXCELMANUS_JWT_SECRET=your-random-secret-key-at-least-64-chars
```

支持**邮箱密码**、**GitHub OAuth**、**Google OAuth**、**QQ OAuth** 四种登录方式。每个用户拥有独立工作区和数据库，首个注册用户自动成为管理员。

### OpenAI Codex 订阅集成

已登录用户可在个人中心通过 **Device Code Flow** 绑定自己的 OpenAI Codex 订阅：

1. 点击「连接 Codex」→ 页面显示 6 位验证码
2. 在 [auth.openai.com/codex/device](https://auth.openai.com/codex/device) 输入验证码
3. 连接成功后系统自动发现你的私有 Codex 模型，无需手填 API Key

> 需要在 ChatGPT 设置 → 安全 中开启 "Enable device code authentication for Codex"。

### 管理后台

管理员可在 `/admin` 页面：
- 查看所有用户的 LLM 用量（按提供商 / 模型分组，calls / tokens / 最后使用时间）
- 管理登录方式开关（邮箱注册 / 各 OAuth 提供商）
- 设置模型白名单和系统级配置

> **前后端分离部署注意**：Google/GitHub OAuth 回调已优化为前端页面接收 + 浏览器直连后端交换 token，避免跨服务器代理链超时。需在 OAuth 提供商控制台将重定向 URI 设为 `https://your-domain/auth/callback`。

详细配置见 [配置文档](docs/configuration.md)。

## 🧪 评测框架

内置 Bench 评测，支持多轮用例、自动断言、JSON 日志和 Suite 并发。Bench Reporter 新增**推理质量指标**（沉默调用率、推理字符统计）：

```bash
uv run python -m excelmanus.bench --all                         # 全部
uv run python -m excelmanus.bench --suite bench/cases/xxx.json  # 指定 suite
uv run python -m excelmanus.bench --message "读取前10行"          # 单条
```

## 📖 配置参考

快速开始只需 3 个环境变量。以下是常用配置分类：

| 类别 | 关键配置 |
| --- | --- |
| **基础** | `EXCELMANUS_API_KEY` / `BASE_URL` / `MODEL` |
| **辅助模型** | `EXCELMANUS_AUX_API_KEY` / `AUX_BASE_URL` / `AUX_MODEL` |
| **VLM（图像识别）** | `EXCELMANUS_VLM_MODEL` / `VLM_EXTRACTION_TIER` |
| **多用户** | `EXCELMANUS_AUTH_ENABLED` / `JWT_SECRET` |
| **安全** | `EXCELMANUS_DOCKER_SANDBOX` / `GUARD_MODE` |
| **性能** | `EXCELMANUS_WINDOW_PERCEPTION_*` / `IMAGE_KEEP_ROUNDS` |
| **Playbook** | `EXCELMANUS_PLAYBOOK_ENABLED` |
| **ClawHub** | `EXCELMANUS_CLAWHUB_ENABLED` / `CLAWHUB_REGISTRY_URL` |

完整配置列表见 [配置文档](docs/configuration.md)。

## ⚡ 性能优化

| 优化项 | 效果 |
| --- | --- |
| **SACR 稀疏压缩** | 工具结果去除 null 键，高空值率数据最高节省 **74% token** |
| **单轮合并提取** | 视觉提取强模型（Gemini 2.5 Pro 等）单次调用完成 4 阶段提取 |
| **图片生命周期管理** | 自动管理多轮对话中的图片保留/降级，避免重复传输 |
| **辅助模型分离** | 路由/子代理走轻量 AUX 模型，主模型专注推理 |
| **SSE 事件去重** | 前端统一 `dispatchSSEEvent` 处理器，消除 3 份重复代码 |
| **数据库 WAL 模式** | 聊天历史 SQLite 启用 WAL，并发读写不阻塞 |

## 🖥️ 平台支持

| 平台 | 状态 | 说明 |
| --- | --- | --- |
| **macOS** | ✅ 完整支持 | 开发主平台 |
| **Linux** | ✅ 完整支持 | Ubuntu / Debian / CentOS / Fedora / Arch 等 |
| **Windows** | ✅ 完整支持 | PowerShell 5.1+ 或 CMD，需安装 Python + Node.js |

启动脚本自动检测 OS 和包管理器，缺少依赖时给出精确的安装命令。

## 🛠️ 开发

```bash
uv sync --all-extras --dev    # 完整安装 + 测试依赖
uv run pytest                 # 运行全部测试（约 3900+ 用例）

# 只运行相关测试（推荐，速度快）
uv run pytest tests/test_engine.py tests/test_api.py
```

## 📄 许可证

[Apache License 2.0](LICENSE) © kilolonion
