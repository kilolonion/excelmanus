<p align="center">
  <img src="web/public/logo.svg" width="380" alt="ExcelManus" />
</p>

<h3 align="center">用自然语言驾驭 Excel 的开源 AI Agent 框架</h3>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License" /></a>
  <a href="https://github.com/kilolonion/excelmanus"><img src="https://img.shields.io/github/stars/kilolonion/excelmanus?style=social" alt="GitHub Stars" /></a>
  <img src="https://img.shields.io/badge/python-≥3.10-3776AB.svg?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/version-1.6.9-green.svg" alt="Version" />
  <img src="https://img.shields.io/badge/Next.js-16-black?logo=next.js" alt="Next.js" />
  <img src="https://img.shields.io/badge/tests-3900+-brightgreen.svg" alt="Tests" />
</p>

<p align="center">
  <a href="README_EN.md">English</a> · 中文 · <a href="docs/configuration.md">配置文档</a> · <a href="docs/ops-manual.md">运维手册</a> · <a href="https://clawhub.ai">ClawHub 市场</a>
</p>

<p align="center">
  <img src="docs/images/webui-desktop.png" width="720" alt="Web UI" />
</p>

---

**ExcelManus** 是一个完全开源的 LLM 驱动 Excel Agent 框架。用一句话描述你想做的事，它就能自动读取数据、编写公式、运行分析脚本、绘制图表 —— 像一个真正理解 Excel 的 AI 助手。

- **三种交互入口** — Web UI / CLI 终端 / Telegram Bot，按需选用
- **任意大模型** — OpenAI · Claude · Gemini · 本地 Ollama / vLLM，即插即用
- **生产可用** — Docker 多架构镜像 · 热更新 · 多用户隔离 · 操作审批 · 版本回滚

> 💡 三个环境变量即可开始：`API_KEY` + `BASE_URL` + `MODEL`

---

## ✨ 核心能力

<table>
<tr>
<td width="50%">

### 📊 全格式 Excel 读写
单元格读写 · 公式 · VLOOKUP · 批量填充 · 多 Sheet 操作
支持 `.xlsx` / `.xls` / `.xlsb` / `.csv` 全格式自动转换

### 📈 数据分析 & 可视化
筛选、排序、聚合、透视表；复杂逻辑自动生成 Python 脚本
柱状图 · 折线图 · 饼图等嵌入 Excel 或导出高清图片

### 🖼️ 视觉识别与提取
表格截图 → 结构化 Excel 数据
4 阶段渐进管线（骨架 → 数据 → 样式 → 公式），支持单轮合并提取与大表格分区提取

### 🔄 版本管理 & Diff
Staging / Audit / CoW 版本链，`/undo` 精确回滚
Excel 修改前后 Diff 可视化，文本文件 unified diff 展示

### ✅ 验证门控
为子任务附加结构化验证条件（行数 / Sheet 存在 / 公式 / 值匹配）
任务完成前自动校验，未通过则阻断 Agent 完成操作

</td>
<td width="50%">

### 🧠 持久记忆 & Playbook
跨会话记忆用户偏好与操作模式；Playbook 自动归纳任务经验
语义去重、智能上下文压缩、按相关性差异化截断

### 🧩 Skillpack & ClawHub 市场
一个 Markdown = 一个技能，自动发现、按需激活
内置 [ClawHub](https://clawhub.ai) 技能市场，一键搜索 / 安装 / 更新社区技能

### 🔌 MCP & Subagent
接入外部 MCP Server 扩展工具集
大文件和复杂任务自动委派子代理并行处理

### 🔍 窗口感知 & 语义检索
自适应窗口感知引擎，智能管理上下文焦点
词嵌入驱动的语义记忆 / 文件 / 技能并行检索，零额外延迟

### 🤖 Telegram Bot
通过 Telegram 直接与 ExcelManus 对话，支持文件收发
用户白名单控制访问权限

### � 应用内热更新
Web UI 一键检测新版本 → 备份 → 更新 → 自动重启
支持版本兼容校验、蓝绿部署、回滚窗口保护

</td>
</tr>
</table>

## 🚀 快速开始

> **前置要求**：Python ≥ 3.10 · Node.js ≥ 18（Web UI 需要）

### 方式一：一键启动（推荐）

自动安装依赖、启动后端和前端，适合大多数用户。

<details open>
<summary><b>🪟 Windows — 图形化部署工具（最简单）</b></summary>

**无需提前 clone 仓库**，直接从 [Releases](https://github.com/kilolonion/excelmanus/releases) 下载 `ExcelManus.exe` 双击运行即可。

两步向导式界面，全程引导：
1. **环境检测** — 自动检测 Python / Node.js / Git，缺失时 winget 自动安装
2. **一键部署** — 克隆仓库 → 安装依赖 → 启动服务，进度条实时展示

部署完成后浏览器自动打开 `http://localhost:3000`。

> 🇨🇳 **国内网络友好**：GitHub 失败自动回退 Gitee 镜像 · npm 淘宝源 · pip 清华源

</details>

<details>
<summary><b>🍎 macOS / 🐧 Linux — 启动脚本</b></summary>

```bash
git clone https://github.com/kilolonion/excelmanus.git
# 国内推荐：git clone https://gitee.com/kilolonion/excelmanus.git
cd excelmanus
chmod +x ./deploy/start.sh
./deploy/start.sh
```

首次启动会交互式提示填写大模型配置（API Key、Base URL、模型名称）。启动成功后浏览器自动打开 `http://localhost:3000`。

```bash
./deploy/start.sh --prod              # 生产模式
./deploy/start.sh --backend-port 9000 # 自定义端口
./deploy/start.sh --workers 4         # 多 worker
./deploy/start.sh --help              # 全部选项
```

</details>

### 方式二：手动安装（uv）

适合想精确控制依赖的用户。[uv](https://docs.astral.sh/uv/) 比 pip 快 10–100 倍。

```bash
# 1. 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. 克隆并安装
git clone https://github.com/kilolonion/excelmanus.git
# 国内推荐：git clone https://gitee.com/kilolonion/excelmanus.git
cd excelmanus
uv sync --all-extras     # 完整安装（也支持 pip install ".[all]"）

# 3. 配置
cp .env.example .env     # 编辑 .env 填写 API Key / Base URL / Model

# 4. 启动
uv run excelmanus        # CLI 终端模式
uv run excelmanus-api    # Web API（http://localhost:8000）
cd web && npm i && npm run dev   # Web 前端（http://localhost:3000）
```

### 开始对话

在 Web UI 或 CLI 中直接输入自然语言：

```
> 读取 sales.xlsx 前 10 行
> 把 A 列金额求和写到 B1
> 按地区分组统计销售额，生成柱状图
> 把这张表格截图还原成 Excel
```

## 💻 三种交互方式

### Web UI

基于 **Next.js + Univer.js**，提供完整的可视化操作体验。

| 能力 | 说明 |
| --- | --- |
| **SSE 流式响应** | 实时显示思考过程、工具调用、子代理执行；断连自动重连 |
| **Excel 侧边面板** | 内嵌 Univer 查看器，实时预览编辑，支持选区引用和全屏模式 |
| **Excel & Text Diff** | 每次写入前后对比，行/列/值变化一目了然 |
| **多会话管理** | SQLite + IndexedDB 三级缓存，刷新/重启不丢失 |
| **文件交互** | 拖拽上传、`@` 引用文件和技能；`.xls` / `.xlsb` 自动转 `.xlsx` |
| **操作审批** | 高风险操作弹窗确认，变更自动记录快照 |
| **乐观 UI** | 消息即时显示，写操作乐观更新 + 失败自动回滚 |
| **错误引导** | 失败时展示可操作建议卡片（重试 / 检查设置 / 复制诊断 ID） |
| **ClawHub 市场面板** | 侧边栏内嵌技能市场，一键搜索 / 安装 / 更新 |
| **管理后台** | 用户管理 + 按提供商/模型可视化 LLM 用量统计 |
| **Plan 模式** | 复杂任务自动拆解规划，交互确认后执行 |
| **热更新通知** | 检测到新版本时提示升级，升级后自动探活刷新 |

<p align="center">
  <img src="docs/images/webui-mobile.png" width="300" alt="移动端" />
</p>
<p align="center"><sub>响应式布局 — 移动端同样可用</sub></p>

### CLI

终端对话模式，Dashboard 布局，`/` 自动补全，输入纠错。

<details>
<summary>📋 常用命令速查</summary>

| 命令 | 说明 |
| --- | --- |
| `/help` | 帮助 |
| `/skills` | 技能管理（列出 / 安装 / 激活 / 禁用） |
| `/clawhub search <关键词>` | ClawHub 市场搜索 |
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
| `/playbook` | Playbook 任务经验管理 |
| `/compact` | 上下文压缩 |
| `/config export` | 加密导出配置 |
| `/config import` | 导入配置 |
| `/export` | 导出会话（Markdown / 纯文本 / EMX） |
| `/clear` | 清空对话 |
| `/rollback` | 回滚会话到指定轮次 |

</details>

### Telegram Bot

通过 Telegram 与 ExcelManus 交互，支持消息对话和文件收发：

```bash
EXCELMANUS_TG_TOKEN=xxx python3 excelmanus_tg_bot.py
```

| 环境变量 | 说明 |
| --- | --- |
| `EXCELMANUS_TG_TOKEN` | Telegram Bot Token（必填） |
| `EXCELMANUS_API_URL` | 后端地址（默认 `http://localhost:8000`） |
| `EXCELMANUS_TG_USERS` | 允许使用的 user ID，逗号分隔（留空 = 不限制） |

### REST API

`excelmanus-api` 启动后即可调用，SSE 推送 30+ 种事件类型。

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
| `GET /api/v1/files/excel` | Excel 文件流 |
| `GET /api/v1/files/excel/snapshot` | Excel JSON 快照 |
| `POST /api/v1/files/excel/write` | 侧边面板回写 |
| `GET /api/v1/skills` | 技能列表 |
| `GET /api/v1/clawhub/*` | ClawHub 市场（搜索 / 安装 / 更新） |
| `GET /api/v1/version/check` | 版本检查 & 热更新 |
| `POST /api/v1/version/update` | 执行在线更新 |
| `GET /api/v1/auth/codex/status` | Codex 连接状态 |
| `POST /api/v1/config/export` | 导出配置 |
| `GET /api/v1/health` | 健康检查 |

</details>

## 🤖 模型支持

ExcelManus 通过 URL 自动检测模型提供商，零配置切换：

| Provider | 说明 |
| --- | --- |
| **OpenAI 兼容** | 默认协议。任何 OpenAI 兼容 API 均可——Ollama / vLLM / LM Studio / DeepSeek 等 |
| **Claude (Anthropic)** | URL 含 `anthropic` 自动切换，支持 extended thinking |
| **Gemini (Google)** | URL 含 `googleapis` / `generativelanguage` 自动切换 |
| **OpenAI Responses API** | 新一代推理 API，`EXCELMANUS_USE_RESPONSES_API=1` 启用 |
| **OpenAI Codex** | Device Code Flow 绑定 Codex 订阅，私有模型自动发现，无需手填 Key |
| **MiniMax** | 自动检测 base_url，内置推荐模型列表 |

### 辅助模型（AUX）

可独立配置一个更轻量的辅助模型，用于**意图路由、子代理、窗口感知顾问**，在不影响任务质量的前提下显著降低成本：

```dotenv
EXCELMANUS_AUX_API_KEY=sk-xxxx
EXCELMANUS_AUX_BASE_URL=https://api.openai.com/v1
EXCELMANUS_AUX_MODEL=gpt-4o-mini
```

### 模型能力探测

首次使用新模型时，ExcelManus 自动探测其能力边界（视觉、函数调用、上下文窗口等），据此动态调整工具策略，无需手动配置。

## 🔍 窗口感知 & 语义引擎

ExcelManus 内置**自适应窗口感知引擎**和**词嵌入语义检索系统**，让 Agent 在长对话中始终保持精准上下文：

| 模块 | 说明 |
| --- | --- |
| **窗口感知管理器** | 25 个子模块协同，动态管理上下文焦点、投影、策略适配 |
| **语义记忆检索** | 用户偏好和历史操作向量化存储，新任务自动召回相关记忆 |
| **语义文件注册表** | 对工作区文件建立 embedding 索引，按语义相关性注入上下文 |
| **语义技能路由** | 对 Skillpack 描述建立向量索引，自动匹配最优技能 |
| **错误解决方案库** | 错误 → 解决方案向量索引，同类错误自动召回历史解法 |
| **智能上下文压缩** | 按语义相关性评分差异化截断，高相关消息保留更多细节 |

所有语义检索通过 `asyncio.gather` 并行执行，零额外延迟。`EXCELMANUS_EMBEDDING_ENABLED=false` 时全部降级为无操作。

## 🔒 安全机制

| 机制 | 说明 |
| --- | --- |
| **路径沙盒** | 读写限制在工作目录，路径穿越和符号链接越界被拒绝 |
| **代码审查** | `run_code` 静态分析，按 Green / Yellow / Red 三级自动审批 |
| **Docker 沙盒** | 可选容器隔离执行用户代码（`EXCELMANUS_DOCKER_SANDBOX=1`） |
| **操作审批** | 高风险写入需用户确认，变更自动记录 diff 和快照 |
| **版本链** | Staging → Audit → CoW，`/undo` 回滚任意历史版本 |
| **MCP 白名单** | 外部工具默认需逐项确认 |
| **请求速率限制** | 内置 API 速率限制，防止滥用 |
| **用户隔离** | 多用户模式下工作区、数据库、会话物理隔离 |

## 🧩 Skillpack & ClawHub

一个目录 + 一个 `SKILL.md`（含 `name` 和 `description`）即可创建技能。自动发现、按需激活，支持 Hook、命令分派、MCP 依赖声明。

### ClawHub 技能市场

内置 [ClawHub](https://clawhub.ai) 集成，在 Web UI 侧边栏或 CLI 中搜索、安装、更新社区技能包：

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
| `chart_basic` | 图表（内嵌 + 图片导出） |
| `format_basic` | 样式、条件格式、高级排版 |
| `file_ops` | 文件管理 |
| `sheet_ops` | 工作表与跨表操作 |
| `excel_code_runner` | Python 脚本处理大文件 |
| `run_code_templates` | 常用代码模板 |

</details>

协议详见 [`docs/skillpack_protocol.md`](docs/skillpack_protocol.md)。

## 🧠 Playbook — 任务经验学习

Playbook 自动分析每轮任务的成功/失败模式，提炼为可复用的操作经验：

- **自动归纳** — 任务结束后后台生成 PlaybookDelta，写入 SQLite
- **语义去重** — 相似经验合并，过时经验淘汰
- **自动注入** — 后续相关任务开始前自动注入匹配条目，减少重复犯错
- **管理命令** — `/playbook list` 查看 · `/playbook clear` 清空

## 👥 多用户 & 管理

```dotenv
EXCELMANUS_AUTH_ENABLED=true
EXCELMANUS_JWT_SECRET=your-random-secret-key-at-least-64-chars
```

支持 **邮箱密码** · **GitHub OAuth** · **Google OAuth** · **QQ OAuth** 四种登录方式。
每个用户拥有独立工作区和数据库，首个注册用户自动成为管理员。

**管理后台** (`/admin`)：
- 查看所有用户 LLM 用量（按提供商 / 模型分组）
- 管理登录方式开关
- 设置模型白名单和系统级配置

**OpenAI Codex 订阅**：用户可通过 Device Code Flow 绑定 Codex 订阅，私有模型自动发现，无需手填 API Key。

> **前后端分离部署**：OAuth 回调已优化为前端页面接收 + 浏览器直连后端交换 token，需将重定向 URI 设为 `https://your-domain/auth/callback`。

详细配置见 [配置文档](docs/configuration.md)。

## 🏗️ 部署

### Docker Compose（推荐）

```bash
cp .env.example .env
docker compose -f deploy/docker-compose.yml up -d
```

访问 `http://localhost:3000`。加 `--profile production` 启用 Nginx 反向代理。

镜像支持 **amd64** + **arm64** 双架构：

```bash
docker pull kilol/excelmanus-api:1.6.9       # 后端 API
docker pull kilol/excelmanus-sandbox:1.6.9   # 代码沙盒（可选）
docker pull kilol/excelmanus-web:1.6.9       # 前端 Web
```

### Windows 图形化部署

从 [Releases](https://github.com/kilolonion/excelmanus/releases) 下载 `ExcelManus.exe`，双击运行。两步向导式界面（Vite + React + Tailwind CSS），自动完成环境检测和部署。零外部依赖，源码：`deploy/ExcelManusSetup.cs`。

### 一键启动脚本（本地开发）

```bash
./deploy/start.sh              # macOS / Linux 开发模式
./deploy/start.sh --prod       # 生产模式
.\deploy\start.ps1 -Production # Windows PowerShell
deploy\start.bat --prod        # Windows CMD
```

支持 `--backend-port` · `--frontend-port` · `--workers` · `--backend-only` 等选项。

### 远程部署

部署脚本通过 SSH 操作远程服务器，支持单机 / 前后端分离 / Docker 等拓扑：

```bash
./deploy/deploy.sh                    # 完整部署
./deploy/deploy.sh --backend-only     # 仅后端
./deploy/deploy.sh --frontend-only    # 仅前端
./deploy/deploy.sh rollback           # 回滚上一版本
./deploy/deploy.sh check              # 环境 + 互联检测
```

<details>
<summary>🔐 部署安全机制</summary>

三层防护，避免部署导致线上 502：

| 层 | 机制 | 说明 |
| --- | --- | --- |
| **构建退出码** | 不使用管道吞掉退出码 | 构建失败立即中止 |
| **产物校验** | BUILD_ID + routes-manifest.json | 不完整产物拒绝重启 |
| **启动降级** | standalone vs next start 自动检测 | 兼容不同 Next.js 输出 |

构建失败时保留当前运行版本，不会触发 PM2 重启。自动排除 `.env`、`data/`、`workspace/`。

</details>

### 热更新

ExcelManus 内置应用级热更新能力：

- **版本检查** — 定期轮询 Gitee / GitHub Tags API，TTL 缓存避免频繁请求
- **数据备份** — 更新前自动备份 `.env`、用户数据、上传文件
- **代码更新** — git pull + 依赖重装，互斥锁防止并发更新
- **Web UI 集成** — 设置面板一键检查更新 → 确认 → 执行，进度实时展示
- **重启探活** — 更新后前端自动探测后端恢复，无缝刷新

详见 [热更新设计文档](docs/hot-update-design.md)。

手动部署详见 [运维手册](docs/ops-manual.md)。

## ⚡ 性能优化

| 优化项 | 效果 |
| --- | --- |
| **SACR 稀疏压缩** | 工具结果去除 null 键，高空值率数据最高节省 **74% token** |
| **单轮合并提取** | 强 VLM 模型单次调用完成 4 阶段视觉提取 |
| **图片生命周期管理** | 自动管理多轮对话中的图片保留/降级，避免重复传输 |
| **辅助模型分离** | 路由/子代理走轻量 AUX 模型，主模型专注推理 |
| **上下文预算管理** | 动态分配预算，语义相关性评分驱动差异化截断 |
| **语义并行检索** | `asyncio.gather` 并行执行记忆/文件/技能检索，零额外延迟 |
| **SSE 事件去重** | 前端统一 `dispatchSSEEvent` 处理器 |
| **数据库 WAL 模式** | SQLite 启用 WAL，并发读写不阻塞 |

## 📖 配置参考

快速开始只需 3 个环境变量。常用配置分类：

| 类别 | 关键配置 |
| --- | --- |
| **基础** | `EXCELMANUS_API_KEY` / `BASE_URL` / `MODEL` |
| **辅助模型** | `EXCELMANUS_AUX_API_KEY` / `AUX_BASE_URL` / `AUX_MODEL` |
| **VLM（视觉）** | `EXCELMANUS_VLM_MODEL` / `VLM_EXTRACTION_TIER` |
| **多用户** | `EXCELMANUS_AUTH_ENABLED` / `JWT_SECRET` |
| **安全** | `EXCELMANUS_DOCKER_SANDBOX` / `GUARD_MODE` |
| **性能** | `EXCELMANUS_WINDOW_PERCEPTION_*` / `IMAGE_KEEP_ROUNDS` |
| **Playbook** | `EXCELMANUS_PLAYBOOK_ENABLED` |
| **ClawHub** | `EXCELMANUS_CLAWHUB_ENABLED` / `CLAWHUB_REGISTRY_URL` |
| **Embedding** | `EXCELMANUS_EMBEDDING_ENABLED` / `EMBEDDING_MODEL` |

完整配置列表见 [配置文档](docs/configuration.md)。

## 🖥️ 平台支持

| 平台 | 状态 | 说明 |
| --- | --- | --- |
| **macOS** | ✅ 完整支持 | 开发主平台 |
| **Linux** | ✅ 完整支持 | Ubuntu / Debian / CentOS / Fedora / Arch 等 |
| **Windows** | ✅ 完整支持 | PowerShell 5.1+ 或 CMD |

启动脚本自动检测 OS 和包管理器，缺少依赖时给出精确的安装命令。

## 🧪 评测框架

内置 Bench 评测，支持多轮用例、自动断言、JSON 日志和 Suite 并发：

```bash
uv run python -m excelmanus.bench --all                         # 全部
uv run python -m excelmanus.bench --suite bench/cases/xxx.json  # 指定 suite
uv run python -m excelmanus.bench --message "读取前10行"          # 单条
```

## 🛠️ 开发 & 贡献

```bash
uv sync --all-extras --dev    # 完整安装 + 测试依赖
uv run pytest                 # 运行全部测试（3900+ 用例）
uv run pytest tests/test_engine.py tests/test_api.py  # 针对性测试
```

欢迎提交 PR 和 Issue！请确保新代码附带测试，且 `uv run pytest` 全部通过。

## ⭐ Star History

如果 ExcelManus 对你有帮助，请给我们一个 Star 🌟

<p align="center">
  <a href="https://github.com/kilolonion/excelmanus/stargazers">
    <img src="https://starchart.cc/kilolonion/excelmanus.svg?variant=adaptive" width="600" alt="Star History" />
  </a>
</p>

## 📄 许可证

[Apache License 2.0](LICENSE) © kilolonion
