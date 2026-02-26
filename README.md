<p align="center">
  <img src="logo.svg" width="320" alt="ExcelManus" />
</p>

<p align="center">
  <strong>v1.5.7</strong> · 用自然语言操作 Excel —— 读数据、写公式、跑分析、画图表，说一句话就够了。
</p>

<p align="center">
  中文 · <a href="README_EN.md">English</a>
</p>

ExcelManus 是一个基于大语言模型的 Excel 智能代理。你不需要记住任何函数语法或者写 VBA，只需要告诉它你想做什么，它会自己完成剩下的事。支持 OpenAI、Claude、Gemini 等主流模型，从 URL 自动识别 Provider，无需手动切换。

## 能做什么

- **读写 Excel** — 读取单元格、写入公式、VLOOKUP、批量填充，自动处理多 sheet
- **数据分析** — 筛选、排序、分组聚合、透视表，复杂逻辑自动生成 Python 脚本执行
- **图表生成** — 柱状图、折线图、饼图……描述你想要的样子，嵌入 Excel 或导出图片
- **图片理解** — 贴一张表格截图，它能识别内容并提取为结构化数据；支持两阶段结构化提取（数据 + 样式），还原度更高
- **跨表操作** — 创建 / 复制 / 重命名工作表，跨表搬运数据
- **文件版本管理** — 统一版本链追踪（staging / audit / CoW），`/undo` 一键回滚
- **持久记忆** — 记住你的偏好和常见操作模式，跨会话可用；支持文件 / 数据库双后端
- **技能扩展** — 通过 Skillpack 机制添加领域知识，一个 Markdown 文件就是一个技能
- **MCP 集成** — 接入外部 MCP Server 扩展工具能力
- **Subagent** — 大文件或复杂任务自动委派给子代理处理
- **多用户隔离** — 每个用户独立工作区、独立数据库、独立会话，Auth 即隔离
- **管理员面板** — 用户管理、模型权限分配、用量追踪

## 快速开始

**安装**（Python >= 3.10）

```bash
pip install .
```

**配置** — 在项目根目录创建 `.env`，只需要 3 行：

```dotenv
EXCELMANUS_API_KEY=your-api-key
EXCELMANUS_BASE_URL=https://your-llm-endpoint/v1
EXCELMANUS_MODEL=your-model-id
```

支持任何 OpenAI 兼容 API。如果 URL 指向 Anthropic 或 Google，会自动切换到 Claude / Gemini 原生协议。

> `.env` 仅用于首次启动的初始配置。第一次运行后，所有配置会自动迁移到本地数据库，后续通过 CLI `/config` 命令或 Web UI 设置面板管理即可。多用户模式下全局配置（模型 Profiles）由管理员管理，用户级偏好（如当前模型）存储在各自的隔离数据库中。

**启动**

```bash
excelmanus            # CLI 交互模式
excelmanus-api        # REST API + Web UI 后端
```

**试一试**

```
> 读取 sales.xlsx 前10行
> 把 A 列金额求和写到 B1
> 按地区分组统计销售额，生成柱状图
> 这张截图里的表格帮我导出成 Excel @img screenshot.png
```

## 三种使用方式

### CLI

终端里直接对话，支持 Dashboard（分栏仪表盘）和 Classic（经典聊天）两种布局。

常用命令：

| 命令             | 做什么          |
| ---------------- | --------------- |
| `/help`        | 帮助信息        |
| `/skills`      | 查看 / 管理技能 |
| `/model list`  | 切换模型        |
| `/backup list` | 查看备份        |
| `/undo <id>`   | 回滚操作        |
| `/rules`       | 自定义规则      |
| `/memory`      | 持久记忆管理    |
| `/compact`     | 上下文压缩控制  |
| `/config export` | 加密导出模型配置 |
| `/config import` | 导入配置令牌    |
| `/clear`       | 清空对话        |
| `exit`         | 退出            |

输入 `/` 时有自动补全，打错了也会给纠错建议。完整命令列表见 `/help`。

### Web UI

ExcelManus 自带一个完整的 Web 界面，基于 Next.js + Univer.js 构建。

```bash
# 启动后端
excelmanus-api

# 启动前端（需要先安装依赖）
cd web && npm install && npm run dev
```

Web UI 提供：

- 实时聊天界面（SSE 流式响应，实时显示思考过程和工具调用）
- Pipeline 进度条 — 连接 → 路由 → 上下文构建 → 工具执行，每步可视化
- 内嵌 Excel 查看器 — 在侧边面板直接预览和编辑表格，支持选区引用
- 变更追踪 — 每次操作后实时显示 diff 对比
- 多会话管理 — 自动保存对话历史，随时切换
- 设置面板 — 模型配置、技能管理、MCP Server、记忆、规则，全部可视化操作
- 管理员面板 — 用户管理、角色分配、模型权限控制、用量统计
- 配置分享 — 一键导出所有模型配置（含 API Key），加密后分享给他人导入
- 审批流程 — 高风险操作弹窗确认，支持一键撤销
- 文件拖拽 — 直接拖文件到输入框引用
- `@` 提及 — 输入 `@` 引用文件、工具或技能，支持行范围引用（如 `@file.py:10-20`）

### REST API

```bash
excelmanus-api
```

核心接口：

| 接口                                 | 说明                 |
| ------------------------------------ | -------------------- |
| `POST /api/v1/chat/stream`         | SSE 流式对话         |
| `POST /api/v1/chat`                | 完整 JSON 对话       |
| `POST /api/v1/chat/abort`          | 终止进行中的任务     |
| `GET /api/v1/files/excel`          | Excel 文件流         |
| `GET /api/v1/files/excel/snapshot` | Excel 轻量 JSON 快照 |
| `POST /api/v1/backup/apply`        | 应用备份到原文件     |
| `GET /api/v1/skills`               | 技能列表             |
| `POST /api/v1/skills`              | 创建 / 导入技能      |
| `POST /api/v1/config/export`       | 加密导出模型配置     |
| `POST /api/v1/config/import`       | 导入配置令牌         |
| `GET /api/v1/health`               | 健康检查             |

SSE 流推送 25 种事件类型，覆盖思考过程、工具调用、子代理执行、Pipeline 进度、Excel 预览 / diff、审批请求、记忆提取等。

## 模型支持

| Provider             | 说明                                                      |
| -------------------- | --------------------------------------------------------- |
| OpenAI 兼容          | 默认协议，适用于所有 OpenAI API 兼容服务                  |
| Claude (Anthropic)   | URL 含 `anthropic` 时自动切换，支持 extended thinking   |
| Gemini (Google)      | URL 含 `googleapis` / `generativelanguage` 时自动切换 |
| OpenAI Responses API | 设置 `EXCELMANUS_USE_RESPONSES_API=1` 启用              |

运行时可通过 `/model` 命令或 Web UI 切换模型。可配置辅助模型（AUX）用于路由判断、子代理执行和窗口生命周期管理。

## 配置分享

支持将所有模型配置（主模型 / 辅助模型 / VLM / 多模型 Profiles，含 API Key）一键导出为加密令牌，发给他人即可导入使用。

**两种加密模式：**

| 模式 | 安全性 | 说明 |
| ---- | ------ | ---- |
| 口令加密（默认） | 高 | AES-256-GCM + PBKDF2，没有密码无法解密 |
| 简单分享 | 中 | 内置密钥，无需密码即可导入，适合信任环境 |

**使用方式：**

```bash
# CLI
/config export                       # 口令加密（交互式输入密码）
/config export --simple              # 简单分享模式
/config import EMX1:P:xxxx...        # 导入令牌

# Web UI
# 设置 → 模型配置 → 底部「配置导出/导入」面板
```

导出时可勾选要包含的配置区块，密码支持一键随机生成。令牌格式为 `EMX1:<P|S>:<base64>`，可通过聊天、邮件等任意方式传递。

## 安全机制

ExcelManus 对文件操作和代码执行做了多层防护：

- **路径沙盒** — 所有读写限制在工作目录内，路径穿越和符号链接越界会被拒绝
- **代码策略引擎** — `run_code` 执行前做静态分析，按 Green / Yellow / Red 三级自动审批
- **Docker 沙盒** — 可选启用 Docker 容器隔离执行用户代码（`EXCELMANUS_DOCKER_SANDBOX=1`）
- **操作审批** — 高风险写入需要用户 `/accept` 确认，所有可审计操作记录变更 diff 和快照
- **文件版本管理** — 统一版本链（staging / audit / CoW），支持 `/undo` 回滚到任意版本
- **MCP 工具白名单** — 外部 MCP 工具默认需要确认，可配置自动批准
- **用户隔离** — 多用户模式下工作区和数据库物理隔离，互不可见

## Skillpack 扩展

Skillpack 让你在不改代码的情况下给 Agent 注入领域知识。

创建一个目录，放一个 `SKILL.md`，写上 `name` 和 `description`，ExcelManus 就会自动发现并在合适的时机激活它。支持 Hook（拦截工具调用）、命令分派、MCP 依赖声明等高级特性。

内置技能：

| 技能                  | 用途                              |
| --------------------- | --------------------------------- |
| `data_basic`        | 读取、分析、筛选与转换            |
| `chart_basic`       | 图表生成（Excel 内嵌 + 独立图片） |
| `format_basic`      | 样式调整、条件格式                |
| `file_ops`          | 文件管理                          |
| `sheet_ops`         | 工作表管理与跨表操作              |
| `excel_code_runner` | 用 Python 脚本处理大文件          |
| `run_code_templates` | run_code 常用代码模板库          |

协议详见 `docs/skillpack_protocol.md`。

## Bench 测试

内置自动化评测框架，用于批量验证 Agent 表现：

```bash
python -m excelmanus.bench --all                         # 运行全部
python -m excelmanus.bench --suite bench/cases/xxx.json  # 指定 suite
python -m excelmanus.bench --message "读取前10行"          # 单条测试
```

支持多轮对话用例、自动断言校验、结构化 JSON 日志、`--trace` 引擎内部追踪，以及 Suite 级并发执行。

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

### 一键更新

项目提供 `deploy.sh` 脚本，在本地执行即可同步代码并重启远程服务器：

```bash
./deploy.sh                  # 完整部署（后端 + 前端）
./deploy.sh --backend-only   # 只更新后端（最快）
./deploy.sh --frontend-only  # 只更新前端
./deploy.sh --skip-build     # 跳过前端构建，仅重启
./deploy.sh --frontend-only --frontend-artifact ./web-dist/frontend-standalone.tar.gz  # 使用本地/CI 前端制品（推荐低内存机器）
./deploy.sh --frontend-only --cold-build  # 远端冷构建（仅排障）
```

> 脚本自动排除 `.env`、`data/`、`workspace/` 等数据目录，不会覆盖服务器上的配置和用户数据。


### 部署注意事项

#### Next.js Standalone 静态资源

前端使用 Next.js standalone 模式构建时，`public/` 和 `.next/static/` 目录不会自动复制到 `.next/standalone/` 输出目录，需要手动复制：

```bash
cd web
npm run build
cp -r public .next/standalone/
cp -r .next/static .next/standalone/.next/
```

否则会导致 logo、图片、CSS 等静态资源 404 或 500 错误。建议在 `deploy.sh` 中自动化这一步骤。

#### 低内存服务器发布建议（强烈推荐）

当服务器内存较小（如 1~2G）时，不建议在服务器上直接 `npm run build`。推荐流程：

1. 在本地或 CI 先构建：`cd web && npm run build`（必要时可用 `npm run build:webpack` 兜底）。
2. 打包运行时最小集合（`.next/standalone`、`.next/static`、`public`）：

   ```bash
   cd web
   tar -czf ../web-dist/frontend-standalone.tar.gz .next/standalone .next/static public
   ```

3. 使用 `./deploy.sh --frontend-only --frontend-artifact <tar.gz>` 上传并原子切换。

如果必须远端构建，请优先保留 `.next/cache`，仅在明确排障时使用 `--cold-build`。

#### Nginx SSE 流式响应配置

如果前端通过 Nginx 代理访问后端，需要为 SSE 端点单独配置，避免流式响应被缓冲：

```nginx
# SSE 流式接口专用配置
location /api/v1/chat/stream {
    proxy_pass http://backend-server:8000;
    proxy_http_version 1.1;
    proxy_set_header Connection '';  # 关键：清空 Connection 头部（SSE 不需要 upgrade）
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 600s;
    chunked_transfer_encoding on;
}

# 其他 API 请求
location /api/ {
    proxy_pass http://backend-server:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection 'upgrade';  # WebSocket 用
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;
}
```

**关键点**：SSE（Server-Sent Events）不是 WebSocket，不需要 `Connection: upgrade` 头部。如果对所有 `/api/` 请求都设置 `Connection: upgrade`，会导致 SSE 连接失败，表现为发送消息时显示 fail to fetch。

## 多用户与认证

ExcelManus 支持多用户模式，启用认证后自动启用用户隔离（Auth 即隔离），每个用户拥有独立的工作区目录和数据库。

```dotenv
EXCELMANUS_AUTH_ENABLED=true
EXCELMANUS_JWT_SECRET=your-random-secret-key-at-least-64-chars
```

支持三种登录方式：

- **邮箱 + 密码** — 注册后直接使用
- **GitHub OAuth** — 需在 [GitHub Developer Settings](https://github.com/settings/developers) 创建 OAuth App
- **Google OAuth** — 需在 [Google Cloud Console](https://console.cloud.google.com) 创建 OAuth 凭据

OAuth 配置：

```dotenv
# GitHub
EXCELMANUS_GITHUB_CLIENT_ID=your-client-id
EXCELMANUS_GITHUB_CLIENT_SECRET=your-client-secret
EXCELMANUS_GITHUB_REDIRECT_URI=https://your-domain/api/v1/auth/oauth/github/callback

# Google
EXCELMANUS_GOOGLE_CLIENT_ID=your-client-id
EXCELMANUS_GOOGLE_CLIENT_SECRET=your-client-secret
EXCELMANUS_GOOGLE_REDIRECT_URI=https://your-domain/api/v1/auth/oauth/google/callback

# 国内服务器访问 Google API 需配置代理
# EXCELMANUS_OAUTH_PROXY=socks5://127.0.0.1:1080
```

多用户模式下，每个用户拥有独立的工作区、独立的 SQLite 数据库（`users/{user_id}/data.db`）、对话历史和 token 用量追踪，首个注册用户自动成为管理员。管理员可在 Web UI 管理面板中分配用户角色和模型使用权限。

## 配置参考

快速开始只需要 3 个环境变量。如果需要精细调整，ExcelManus 提供了丰富的配置项，覆盖窗口感知、安全策略、Subagent、MCP、VLM、Embedding 语义检索等方面。

完整配置文档：[docs/configuration.md](docs/configuration.md)

## 开发

```bash
pip install -e ".[dev]"
pytest
```

## 许可证

MIT
