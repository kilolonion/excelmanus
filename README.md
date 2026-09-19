<p align="center">
  <img src="web/public/logo.svg" width="380" alt="ExcelManus" />
</p>

<h3 align="center">用自然语言驾驭 Excel 的开源 AI Agent 框架</h3>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License" /></a>
  <a href="https://github.com/kilolonion/excelmanus"><img src="https://img.shields.io/github/stars/kilolonion/excelmanus?style=social" alt="GitHub Stars" /></a>
  <img src="https://img.shields.io/badge/python-≥3.10-3776AB.svg?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/version-1.7.3-green.svg" alt="Version" />
  <img src="https://img.shields.io/badge/Next.js-16-black?logo=next.js" alt="Next.js" />
  <img src="https://img.shields.io/badge/pytest-included-brightgreen.svg" alt="Tests" />
</p>

<p align="center">
  <a href="README_EN.md">English</a> · 中文 · <a href="docs/configuration.md">配置文档</a> · <a href="docs/ops-manual.md">运维手册</a>
</p>

<p align="center">
  <img src="docs/images/webui-desktop.png" width="720" alt="Web UI" />
</p>

---

**ExcelManus** 是一个完全开源的 LLM 驱动 Excel Agent 框架。用一句话描述你想做的事，它就能自动读取数据、编写公式、运行分析脚本、绘制图表 —— 像一个真正理解 Excel 的 AI 助手。

- **两种交互入口** — Web UI / REST API
- **任意大模型** — OpenAI · Claude · Gemini · DeepSeek · Qwen · Kimi · xAI · 豆包 · 本地 Ollama / vLLM，即插即用
- **生产可用** — 本机 Git 停机升级 · 服务器 deploy.sh · 单用户工作区 · 操作审批 · 版本回滚

> 💡 首次启动打开 Web 设置页添加模型档案（写入主数据库 `model_profiles`）。Web / API 共用同一份档案。

---

## ✨ 核心能力

<table>
<tr>
<td width="50%">

### 📊 Excel 与 Word
单元格读写 · 公式 · VLOOKUP · 批量填充 · 多 Sheet
`.xls` / `.xlsb` 透明转 `.xlsx`；`.xlsx` / `.xlsm` / `.csv` / `.tsv` 原生读写
Word `.docx` 读取、编辑与生成（与 Excel 同为一等能力）

### 📈 数据分析 & 可视化
筛选、排序、聚合、透视表；复杂逻辑自动生成 Python 脚本
柱状图 · 折线图 · 饼图等嵌入 Excel 或导出高清图片

### 🖼️ 视觉识别与提取
表格截图作为附件交给当前模型，产出结构化 Excel 数据
没有独立视觉流水线，也没有附属 VLM 描述步骤

### 🔄 版本管理 & Diff
写入落用户路径；历史在 `.excelmanus/revisions/`，`/undo` 回滚
Excel 修改前后 Diff 可视化，文本文件 unified diff 展示

### ✅ 任务证据与自主检查
为子任务记录可选检查目标，主 Agent 按需回读、比较与检查公式
不运行隐藏验收 Agent；权限、文件安全、备份和回滚机制保持独立

</td>
<td width="50%">

### 🧠 持久记忆 & 会话历史感知
跨会话记忆用户偏好与操作模式；默认由模型通过记忆工具读取，不会在会话开始自动注入。
**会话摘要（可选）**：开启后可在会话结束时生成结构化摘要并落库（`session_summary_enabled` 默认关）。不自动按文件名 / 时间序检索历史会话，也不注入新会话。

### 🧩 Skillpack
一个目录 + `SKILL.md` 即一个技能，自动发现；模型用 `skill` 工具按需加载
支持从本地文件或 GitHub 导入技能包

### 🔌 MCP & Subagent
接入外部 MCP Server 扩展工具集
委派由模型调用 `delegate`；`/subagent` 控制开关，不会因大文件或复杂任务自动委派

### 🔄 本机停机升级
设置页一键更新：停进程 → 备份 `$EXCELMANUS_HOME` → git fast-forward → 再拉起
服务器部署请在运维机运行 `deploy.sh`，不要从生产 API 升级

</td>
</tr>
</table>

## 🚀 快速开始

> **前置要求**：Python ≥ 3.10 · Node.js ≥ 20.9（Web UI 需要，Next.js 16）

### 方式一：一键启动（推荐）

自动安装依赖、启动后端和前端，适合大多数用户。

<details open>
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
./deploy/start.sh --prod              # 生产模式（默认 1 worker）
./deploy/start.sh --backend-port 9000 # 自定义端口
./deploy/start.sh --workers 1         # 推荐：会话在进程内存，>1 会跨 worker 打满 prompt cache miss
./deploy/start.sh --help              # 全部选项
```

</details>

<details>
<summary><b>🪟 Windows — start.ps1 / start.bat</b></summary>

```powershell
git clone https://github.com/kilolonion/excelmanus.git
# 国内推荐：git clone https://gitee.com/kilolonion/excelmanus.git
cd excelmanus
.\deploy\start.ps1
```

```bat
deploy\start.bat
```

首次启动会交互式提示填写大模型配置。启动成功后浏览器打开 `http://localhost:3000`。

```powershell
.\deploy\start.ps1 -Production
.\deploy\start.ps1 -BackendPort 9000
deploy\start.bat --prod
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
uv sync --all-extras     # 完整安装：web/analysis（也支持 pip install ".[all]"）

# 3. 配置
# 启动后打开 Web 设置页添加模型档案（保存在主数据库）。

# 4. 启动
uv run excelmanus-api    # Web API（http://localhost:8000）
cd web && npm i && npm run dev   # Web 前端（http://localhost:3000）
```

### 开始对话

在 Web UI 中直接输入自然语言：

```
> 读取 sales.xlsx 前 10 行
> 把 A 列金额求和写到 B1
> 按地区分组统计销售额，生成柱状图
> 把这张表格截图还原成 Excel
```

## 💻 两种交互方式

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
| **号池管理** | 可选的 API 号池与订阅轮换（默认关闭） |
| **Plan 模式** | `/plan` 开关；确认计划后再执行，不会自动拆解复杂任务 |
| **升级通知** | 检测到新版本时提示升级；本机停机更新后探活刷新 |

<p align="center">
  <img src="docs/images/webui-mobile.png" width="300" alt="移动端" />
</p>
<p align="center"><sub>响应式布局 — 移动端同样可用</sub></p>

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
| `GET /api/v1/files/word` | Word 文件流 |
| `GET /api/v1/files/word/snapshot` | Word JSON 快照 |
| `POST /api/v1/files/word/write` | Word 回写 |
| `GET /api/v1/workspaces` | 已登记工作区 |
| `POST /api/v1/workspaces` | 登记本机文件夹 |
| `GET /api/v1/revisions` | 文件修订历史 |
| `POST /api/v1/revisions/restore` | 恢复历史版本 |
| `GET /api/v1/skills` | 技能列表 |
| `GET /api/v1/version/check` | 版本检查 |
| `POST /api/v1/version/upgrade` | 本机停机更新（仅 standalone + loopback） |
| `GET /api/v1/auth/providers/openai-codex/status` | Codex 连接状态 |
| `POST /api/v1/config/export` | 导出配置 |
| `GET /api/v1/health` | 健康检查 |

</details>

## 🤖 模型支持

ExcelManus 通过 URL 自动检测模型提供商，零配置切换：

| Provider | 说明 |
| --- | --- |
| **OpenAI 兼容** | 默认协议。任何 OpenAI 兼容 API 均可——Ollama / vLLM / LM Studio / DeepSeek 等 |
| **Claude (Anthropic)** | URL 含 `anthropic` 自动切换；Claude 5 使用 adaptive thinking |
| **Gemini (Google)** | URL 含 `googleapis` / `generativelanguage` 自动切换 |
| **OpenAI Responses API** | 新一代推理 API，`EXCELMANUS_USE_RESPONSES_API=1` 启用 |
| **OpenAI Codex** | ChatGPT 订阅 OAuth（浏览器 PKCE 或设备码）绑定 Codex，私有模型自动发现，无需手填 Key |
| **MiniMax / 智谱 / 百炼 / Kimi / 豆包 / xAI** | 自动检测 base_url，`/models` 不可用时回退内置推荐列表 |

### 模型档案

在设置里添加多个模型档案，激活其中一个即可用于对话、子代理和上下文压缩。`/model <名称>` 切换当前激活档案。

### 模型能力探测

首次使用新模型时，ExcelManus 自动探测其能力边界（视觉、函数调用、上下文窗口等），据此动态调整工具策略，无需手动配置。

## 🔒 安全机制

| 机制 | 说明 |
| --- | --- |
| **路径沙盒** | 读写限制在工作目录，路径穿越和符号链接越界被拒绝 |
| **代码审查** | `run_code` 静态分析，按 Green / Yellow / Red 三级自动审批 |
| **本机代码围栏** | `run_code` 在本机子进程中执行（路径守卫、受限 builtins、超时）；不依赖 Docker |
| **操作审批** | 高风险写入需用户确认，变更自动记录 diff 和快照 |
| **版本链** | 写入落用户路径；历史在 `.excelmanus/revisions/`，`/undo` 回滚 |
| **MCP 白名单** | 外部工具默认需逐项确认 |
| **工作区边界** | 凭证与记忆是进程级一份；可登记多个本机文件夹作工作区。多对话不是多租户 |

## 🧩 Skillpack

一个目录 + 一个 `SKILL.md`（含 `name` 和 `description`）即可创建技能。自动发现；激活走模型工具 `skill`（或斜杠 `/<name>` / `@skill`）。支持 Hook、命令分派、MCP 依赖声明。可从本地路径或 GitHub URL 导入。

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
| `word_basic` | Word 读取、编辑与内容生成 |
| `word_code_runner` | 复杂 Word 操作用 python-docx 脚本 |

</details>

协议详见 [`docs/skillpack_protocol.md`](docs/skillpack_protocol.md)。

## 单用户架构

凭证与记忆是进程级一份；可把多个本机文件夹登记为工作区，每个对话绑定其中一个文件夹。多对话（多会话）仍然支持。
Codex 订阅 OAuth 在「设置 → 模型 → 订阅与 OAuth」中配置，不依赖登录账号。

旧版 `users/{id}/` 不会自动合并；请手工把要用的目录拷到 `data_root` / `workspace_root`，各用户 `data.db` 不自动导入。详见 [配置说明](docs/configuration.md)。

**OpenAI Codex 订阅**：用户可通过浏览器 PKCE 或设备码绑定 ChatGPT/Codex 订阅，私有模型自动发现，无需手填 API Key。

> **前后端分离部署**：OAuth 回调已优化为前端页面接收 + 浏览器直连后端交换 token，需将重定向 URI 设为 `https://your-domain/auth/codex/callback`。

详细配置见 [配置文档](docs/configuration.md)。

## 🏗️ 部署

### 本机启动（推荐）

```bash
./deploy/start.sh              # macOS / Linux 开发模式
./deploy/start.sh --prod       # 生产模式
.\deploy\start.ps1 -Production # Windows PowerShell
deploy\start.bat --prod        # Windows CMD
```

访问 `http://localhost:3000`。支持 `--backend-port` · `--frontend-port` · `--workers` · `--backend-only` 等选项。

本机升级：设置页「执行更新」，或先停服务再 `./deploy/update.sh`。升级会停掉进程组、备份 `$EXCELMANUS_HOME`、fast-forward 拉代码后再拉起。

**原 Compose / 镜像用户**：把卷里的数据库和上传文件拷到 `$EXCELMANUS_HOME`（默认 `~/.excelmanus`）后，改用 `./deploy/start.sh` 或服务器上的 PM2 / systemd。产品不再提供 Docker 安装轨。

### 远程部署

部署脚本在**运维机**上通过 SSH 同步远程服务器，支持单机 / 前后端分离 / 本地拓扑。生产进程（`EXCELMANUS_DEPLOY_MODE=server`）不能自己升级或远程部署。

```bash
./deploy/deploy.sh                    # 完整部署
./deploy/deploy.sh --backend-only     # 仅后端
./deploy/deploy.sh --frontend-only    # 仅前端
./deploy/deploy.sh rollback           # 回滚上一版本
./deploy/deploy.sh rollback-to --commit <hash>
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

### 升级

本机（standalone）停机升级：设置页一键更新，或先停服务再 `./deploy/update.sh`。Helper 杀掉 start 进程组，备份 `$EXCELMANUS_HOME`，fast-forward 拉代码后再拉起。冲突不会 `reset --hard`。

服务器（`EXCELMANUS_DEPLOY_MODE=server`）请在运维机运行 `./deploy/deploy.sh`；生产 API 拒绝自己升级。回滚：`./deploy/deploy.sh rollback-to --commit <hash>`。

详见 [升级与部署](docs/hot-update-design.md)。

手动部署详见 [运维手册](docs/ops-manual.md)。

## ⚡ 性能优化

| 优化项 | 效果 |
| --- | --- |
| **Claude 分层 Cache** | System Prompt 拆分为稳定前缀 + 动态块，第 2 次请求 TTFT 降至 3-5s |
| **SACR 稀疏压缩** | 工具结果去除 null 键；测试中稀疏数据节省超过 50% token |
| **图片生命周期管理** | 自动管理多轮对话中的图片保留/降级，避免重复传输 |
| **单一激活模型** | 对话、子代理与压缩共用当前激活档案，切换一次全部生效 |
| **上下文预算管理** | 动态分配预算，均匀截断旧消息 |
| **SSE 事件去重** | 前端统一 `dispatchSSEEvent` 处理器 |
| **数据库 WAL 模式** | SQLite 启用 WAL，并发读写不阻塞 |

## 📖 配置参考

模型与运行时选项在 **Web 设置页** 写入主数据库。常用配置分类：

| 类别 | 关键配置 |
| --- | --- |
| **基础** | `EXCELMANUS_API_KEY` / `EXCELMANUS_BASE_URL` / `EXCELMANUS_MODEL` |
| **视觉** | `EXCELMANUS_MAIN_MODEL_VISION` / `EXCELMANUS_IMAGE_PIXEL_BUDGET` |
| **安全** | `EXCELMANUS_CODE_POLICY_*` / `EXCELMANUS_MANAGE_TOKEN` |
| **性能** | `EXCELMANUS_IMAGE_PIXEL_BUDGET` |
| **会话摘要** | `EXCELMANUS_SESSION_SUMMARY_ENABLED` / `EXCELMANUS_SESSION_SUMMARY_MIN_TURNS` |

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
uv run python -m excelmanus.bench --all                         # 默认短套件
uv run python -m excelmanus.bench --suite bench/cases/xxx.json  # 指定 suite
uv run python -m excelmanus.bench --message "读取前10行"          # 单条
```

体验向长套件（无标准答案）见 `bench/README.md`，不进入 `--all`。

## 🛠️ 开发 & 贡献

```bash
uv sync --all-extras --dev    # 完整安装（web/analysis）+ 测试依赖
uv run pytest tests/test_engine.py tests/test_api.py  # 针对性测试
```

欢迎提交 PR 和 Issue！请确保新代码附带测试，并跑通与改动相关的测试。

## ⭐ Star History

如果 ExcelManus 对你有帮助，请给我们一个 Star 🌟

<p align="center">
  <a href="https://github.com/kilolonion/excelmanus/stargazers">
    <img src="https://starchart.cc/kilolonion/excelmanus.svg?variant=adaptive" width="600" alt="Star History" />
  </a>
</p>

## 📄 许可证

[Apache License 2.0](LICENSE) © kilolonion
