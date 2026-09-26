<p align="center">
  <img src="web/public/logo.svg" width="380" alt="ExcelManus" />
</p>

<h3 align="center">用自然语言处理 Excel 与 Word 的开源 AI 助手</h3>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License" /></a>
  <a href="https://github.com/kilolonion/excelmanus"><img src="https://img.shields.io/github/stars/kilolonion/excelmanus?style=social" alt="GitHub Stars" /></a>
  <img src="https://img.shields.io/badge/python-≥3.10-3776AB.svg?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/version-1.8.1-green.svg" alt="Version" />
  <img src="https://img.shields.io/badge/Next.js-16-black?logo=next.js" alt="Next.js" />
</p>

<p align="center">
  中文 · <a href="README_EN.md">English</a> · <a href="docs/README.md">文档导航</a> · <a href="docs/configuration.md">配置参考</a> · <a href="docs/ops-manual.md">运维手册</a>
</p>

<p align="center">
  <img src="docs/images/webui-desktop.png" width="960" alt="ExcelManus 桌面工作台" />
</p>
<p align="center"><sub>统一工作台：在同一界面管理对话、文件、任务与表格</sub></p>

**ExcelManus** 将对话、文件和表格编辑放在同一个工作台中。描述你的任务，助手可以读取工作簿、整理数据、编写公式、调整格式、生成图表，并将结果保存为可继续编辑的文件。

项目提供 Web UI、REST API，以及 Windows / macOS 桌面打包入口。模型、凭证和会话数据由你的部署管理；可连接 OpenAI 兼容接口、Anthropic、Gemini，或配置本地模型服务。实际可用功能取决于模型及接口支持。

> 本文对应当前 **1.8.1 源码**。桌面安装包的提供情况、签名状态和支持架构，以具体发布资产为准。完整构建说明见 [Desktop README](desktop/README.md)。

## 核心能力

| 能力 | 可以做什么 |
| --- | --- |
| Excel 处理 | 读取与编辑单元格、公式、工作表、样式、条件格式和数据验证；筛选、分组、聚合、比较与拆分数据 |
| 数据分析与图表 | 使用内置工具完成常见操作；需要循环、跨文件组合或自定义计算时，通过 Python 调用同一组工具 |
| Word 处理 | 读取、检索、编辑和生成 `.docx` 文档；复杂任务可使用 Python 脚本 |
| 图片转表格 | 将截图交给支持视觉的当前模型识别，再根据结构化规格生成工作簿 |
| 文件预览与历史 | 在工作台预览和编辑表格，查看修改记录、比较版本、恢复历史文件 |
| 选区交互与并发保护 | Agent 可请求确认具体单元格区域，展示准备修改和已修改范围；写入绑定内容版本，避免覆盖其他会话的编辑；保存冲突时可逐单元格核对并合并草稿 |
| 多工作区与多会话 | 登记已有本机文件夹，每个会话绑定一个工作区；同一工作区中的会话共享文件 |
| 后台任务与恢复 | 子代理可在后台执行；在「任务」面板查看进度、补充指令、暂停或继续；中断的主任务可用 `/resume` 恢复 |
| 技能与外部工具 | 用 Skillpack 复用任务方法，通过 MCP 连接搜索及其他外部服务 |

`.xls` / `.xlsb` 上传时会转换为 `.xlsx`；项目也支持处理 `.xlsx`、`.xlsm`、`.csv` 和 `.tsv`。不同格式可承载的样式、公式和对象不同。公式计算、复杂对象保真和界面呈现不应视为与 Microsoft Excel 完全等价；重要文件请保留原件，并检查生成结果。

## 桌面 App

ExcelManus Desktop 把 Web 工作台、API 后端，以及 `run_code` 所需的 Python 和 Node.js 运行时打包在一个应用中。安装后直接打开即可使用，不需要分别启动前端和后端；模型服务仍由你在设置中连接和选择。

- **统一工作区**：对话、文件、后台任务和表格视图在同一个窗口中切换。
- **内置表格工作台**：直接查看和编辑工作簿，通过公式栏、工作表标签和对话输入继续处理数据。
- **选区确认与结果定位**：需要范围时可在表格里直接选择并确认；Agent 可把查看、计划和已修改区域定位到当前工作簿。
- **集中管理模型**：在设置中管理模型提供商、模型配置、订阅账号与授权，以及可选的决策服务。
- **本地数据目录**：主数据库、凭证和默认工作区保存在 App 的独立 profile 中；登记到其他位置的工作区仍保留在原路径。

<table>
<tr>
<td width="50%"><img src="docs/images/webui-mobile.png" alt="ExcelManus 表格工作台" /></td>
<td width="50%"><img src="docs/images/app-settings.png" alt="ExcelManus 模型与提供商设置" /></td>
</tr>
<tr>
<td align="center"><b>表格工作台</b><br />查看、编辑工作簿，并从当前内容继续对话</td>
<td align="center"><b>模型与连接</b><br />管理提供商、模型、订阅账号与授权</td>
</tr>
</table>

桌面安装包是否可用、支持哪些系统架构以及是否已签名，以对应发布资产为准。构建、签名、运行时和数据迁移说明见 [桌面版文档](desktop/README.md)。

## 快速开始

### 下载安装桌面版

如果当前发布提供适合你的系统和架构的安装包，可从 [GitHub Releases](https://github.com/kilolonion/excelmanus/releases) 获取。桌面包包含前端、后端以及执行代码所需的 Python 和 Node.js 运行时；额外配置的 MCP 命令可能仍需要单独安装。

桌面版使用独立的数据目录。首次启动后，在设置页添加并激活模型；升级时安装新的应用包。源码构建、签名和数据迁移说明见 [桌面版文档](desktop/README.md)。

### 从源码一键启动

需要 **Python ≥ 3.10、Node.js ≥ 20.9 和 Git**。建议使用 [uv](https://docs.astral.sh/uv/) 管理 Python 依赖。启动脚本会检查环境、安装项目依赖并启动前后端。

macOS / Linux：

```bash
git clone https://github.com/kilolonion/excelmanus.git
cd excelmanus
./deploy/start.sh
```

Windows PowerShell：

```powershell
git clone https://github.com/kilolonion/excelmanus.git
cd excelmanus
.\deploy\start.ps1
```

Windows CMD 也可运行 `deploy\start.bat`。国内用户可将克隆地址替换为 [Gitee 仓库](https://gitee.com/kilolonion/excelmanus)。

启动后访问 [http://localhost:3000](http://localhost:3000)，在引导页或「设置 → 模型」中添加模型档案并激活。无需在终端填写模型密钥；未配置模型时仍可打开设置页。

```bash
./deploy/start.sh --prod                  # 构建并运行生产前端
./deploy/start.sh --backend-port 9000     # 修改后端端口
./deploy/start.sh --frontend-port 8080    # 修改前端端口
./deploy/start.sh --backend-only         # 仅启动后端
./deploy/start.sh --help                 # 查看完整选项
```

Windows 生产模式使用 `.\deploy\start.ps1 -Production` 或 `deploy\start.bat --prod`。单机保持 **1 个后端 worker**，避免内存中的会话与运行状态分散到多个进程。

### 手动启动

在仓库根目录安装依赖并启动后端：

```bash
uv sync --frozen --extra web --extra analysis
uv run excelmanus-api --host 127.0.0.1 --port 8000
```

另开一个终端启动前端：

```bash
cd web
npm ci
npm run dev
```

仅使用 REST API 时可只安装 `web` extra。需要 VBA 检查工具或实验性 Jev 功能时，再分别添加 `--extra vba`、`--extra system-one`。前端地址配置见 [Web README](web/README.md)。

### 开始第一个任务

先上传文件，或登记文件所在的本机文件夹，再在对话中描述需求：

```text
读取 sales.xlsx 的前 10 行，告诉我各列含义。
按地区汇总销售额，保存为新工作簿，并生成柱状图。
比较两份报价单，列出价格变化和缺失项目。
把这张表格截图整理成可编辑的 Excel 文件。
```

可以通过 `@` 引用文件、技能或表格选区。需要先讨论方案时使用计划模式；只想查看和分析时使用只读模式。

## 工作台与任务执行

Web 工作台基于 Next.js、React 和 Univer，支持流式对话、文件预览、表格选区引用、单元格编辑、文件历史及后台任务面板，也提供移动端布局。

- **直接工具与代码执行**：同一任务可以交替使用业务工具和 `run_code`，无需切换代码模式。常用工具直接提供，其他能力按需查询和加载。
- **写入与审批**：普通工作区编辑按当前权限执行并记录变更；删除文件、执行 Shell 等操作可能需要确认。并非每次写入都会弹出审批。
- **版本冲突**：工作簿写入使用观察到的内容版本。发生冲突时应重新读取文件后决定如何修改，避免覆盖其他会话或手工编辑。
- **选区确认**：Agent 请求范围时，确认答案会绑定工作区、文件、工作表和用户当前看到的内容版本；旧版本或非法区域会保留问题并要求重新处理。
- **版本与历史**：历史预览、恢复和操作撤销都按文件版本校验；恢复前会检查待保存编辑和冲突，不能把旧页面的坐标套到新版本。
- **后台子代理**：由模型显式调用 `delegate` 启动。主聊天结束后，后台任务可以继续；暂停或取消会保留已经提交的改动。
- **中断恢复**：服务重启后，未完成任务显示为中断。`/resume` 根据已保存的对话与工具结果继续，不会恢复旧进程，也不会自动重放结果不明的写入。
- **记忆与摘要**：记忆按需读取；会话结束摘要默认关闭。启用相关功能可能产生额外模型调用。

## 模型与配置

在设置页管理多个模型档案，选择一个作为当前激活模型。对话、子代理与上下文压缩使用该档案；也可用 `/model <名称>` 切换。

| 接口 | 配置方式 |
| --- | --- |
| OpenAI 兼容 | 填写 Base URL、API Key 和模型 ID；适用于支持该协议的云端或本地服务 |
| OpenAI Responses | 将模型档案协议设为 `openai_responses` |
| Anthropic / Gemini | 使用对应提供商预设，或显式选择 `anthropic` / `gemini` 协议 |
| Codex 订阅连接 | 在「设置 → 模型 → 订阅账号」中使用浏览器授权或设备码；可用模型以连接后的发现结果为准 |

远程部署使用 Codex 连接时，按设置页提示使用设备码，或粘贴浏览器授权后的完整回调地址。当前集成使用固定的本机回调 `http://localhost:1455/auth/callback`；不要替换为部署站点的回调地址。

**产品设置保存在主数据库**，模型档案保存在 `model_profiles`，其他设置保存在 `config_kv`。项目 `.env` 和用户 `config.env` 不再作为产品配置源。`EXCELMANUS_HOME`、监听端口、管理令牌等进程参数用于定位数据或启动服务，详见 [配置参考](docs/configuration.md)。

## 数据与访问边界

ExcelManus 是单用户软件。多个工作区和会话共用进程级模型凭证、记忆和外部服务配置，不提供多租户账号隔离。

默认源码数据目录为 `~/.excelmanus`，工作区文件默认放在其 `data/` 下；登记的其他文件夹保留在原位置。文件修订存放在各工作区的 `.excelmanus/revisions/`。备份时应同时保存主数据库、`.secret_key` 和工作区文件。

文件工具检查工作区边界、敏感文件及符号链接。Python 代码在本机子进程中执行，并受路径检查、代码策略和超时约束；这不等同于虚拟机或容器隔离。模型请求可能包含对话、工具读到的文件内容和图片，搜索及 MCP 调用也会发送对应参数，详见 [隐私政策](docs/privacy-policy.md)。

后端默认监听 `127.0.0.1`。远程访问需配置管理令牌和受控入口；通过反向代理暴露本机后端时也需要访问保护。部署步骤见 [运维手册](docs/ops-manual.md)。

## REST API

后端默认地址为 [http://localhost:8000](http://localhost:8000)。启动后可在 [交互式 API 文档](http://localhost:8000/docs) 查看实际请求参数和响应结构。

| 接口 | 用途 |
| --- | --- |
| `POST /api/v1/chat/stream` | 流式对话 |
| `POST /api/v1/chat` | JSON 对话 |
| `POST /api/v1/chat/abort` | 停止主任务 |
| `POST /api/v1/chat/subscribe` | 订阅或重新连接会话事件流 |
| `POST /api/v1/chat/{session_id}/answer` | 提交问答或选区确认 |
| `POST /api/v1/chat/{session_id}/approve` | 批准或拒绝待审批操作 |
| `POST /api/v1/chat/{session_id}/guide` | 向运行中的会话注入引导消息 |
| `GET /api/v1/sessions/{session_id}/turn` | 查看主任务状态及恢复条件 |
| `GET /api/v1/sessions/{session_id}/subagents` | 查看后台子代理 |
| `POST /api/v1/sessions/{session_id}/subagents/{run_id}` | 补充指令、暂停、取消或继续后台任务 |
| `GET /api/v1/workbooks/observe` | 获取版本绑定的 V2 工作簿观察 |
| `POST /api/v1/workbooks/changes` | 提交版本绑定的 V2 ChangeSet |
| `GET /api/v1/workbooks/compare` | 比较两个 V2 工作簿观察 |
| `POST /api/v1/workbooks/merge-review` | 预览或按单元格选择合并冲突中的 V2 ChangeSet |
| `GET /api/v1/files/word/snapshot` | 获取 Word 文档快照 |
| `POST /api/v1/files/word/write` | 保存 Word 编辑 |
| `GET /api/v1/revisions` | 查询文件修订 |
| `POST /api/v1/revisions/restore` | 恢复文件历史版本 |
| `GET /api/v1/version/check` | 检查已发布版本与更新信息 |
| `GET /api/v1/version/manifest` | 获取前后端版本指纹 |
| `GET /api/v1/version/installations` | 查看本机安装路径及当前/可用/失效状态 |
| `POST /api/v1/version/installations/delete` | 删除旧安装记录，可同时删除安装目录（当前安装受保护） |
| `POST /api/v1/version/installations/cleanup` | 清理已不存在的安装记录（不删除目录） |
| `GET /api/v1/version/upgrade/capability` | 查询网页更新是否可用及原因 |
| `GET /api/v1/version/upgrade/status` | 查询最近一次网页更新状态 |
| `POST /api/v1/version/upgrade` | 在满足条件时启动网页停机更新 |
| `GET /api/v1/health` | 检查服务状态 |

配置了有效管理令牌后，除健康检查和 CORS 预检外，API 请求需携带 `Authorization: Bearer <token>`。健康检查成功只表示服务可响应，不代表模型配置、文件处理或外部工具均已通过验证。

## Skillpack

一个目录和一份带 `name`、`description` 的 `SKILL.md` 即可定义技能。模型通过 `skill` 按需加载，用户也可以用 `/<技能名>` 或 `@` 显式引用。技能可声明参考资源、Hook 和 MCP 依赖；加载技能不会扩大当前会话权限。

<details>
<summary>📦 内置技能</summary>

| 技能 | 用途 |
| --- | --- |
| `spreadsheet_workflow` | ExcelManus V2 表格任务的观察、分析、变更、重算、校验、预览与交付闭环 |
| `data_basic` | 数据读取、分析、筛选与转换 |
| `chart_basic` | 工作簿图表与图片导出 |
| `format_basic` | 样式、条件格式与排版 |
| `file_ops` | 文件管理 |
| `sheet_ops` | 工作表与跨表操作 |
| `excel_code_runner` | 自定义 Python 计算与跨工具组合 |
| `run_code_templates` | 批量读写、分析与格式化模板 |
| `word_basic` | Word 读取、编辑与生成 |
| `word_code_runner` | 复杂 Word 文档处理 |
| `agent_self_management` | 查询自身能力并调整当前会话配置，默认关闭 |

</details>

加载顺序、目录发现、覆盖规则和 Hook 协议见 [Skillpack 文档](docs/skillpack_protocol.md)。

「Agent 自我管理」默认启用，可在设置 → 系统 → 能力中关闭。开关开启时，agent 可加载对应技能，使用 `inspect_agent` 查询能力与配置、`configure_agent` 调整当前会话。开关立即生效；配置修改不会保存为全局默认，也不能更改密钥或审批权限。

## 升级与部署

| 安装形态 | 更新方式 |
| --- | --- |
| 桌面版 | 安装新的应用包，保留并备份用户数据目录 |
| 本机 Git 源码 | 通过设置页停机更新，或停止服务后运行 `./deploy/update.sh` |
| 远程服务器 | 在运维机运行 `bash ./deploy/deploy.sh`；服务器 API 不执行自身升级 |

本机 Git 更新采用 fast-forward，遇到分支冲突时停止更新。服务器部署脚本会同步部署目录，更新前应保留数据备份，避免在部署目录存放未提交的开发工作。当前不提供 Docker 安装流程，也不承诺无中断升级。

网页更新会先检查未保存的表格编辑和运行中的任务；更新期间有停机窗口，只有前后端都恢复后才提示刷新。服务器默认关闭网页更新，需显式设置 `EXCELMANUS_WEB_UPGRADE_ENABLED=1` 并启用管理员登录保护。详见 [升级与部署说明](docs/hot-update-design.md) 和 [运维手册](docs/ops-manual.md)。

## 开发与评测

```bash
uv sync --frozen --extra web --extra analysis --dev
uv run pytest tests/test_engine.py tests/test_api.py
```

前端检查和桌面构建分别见 [Web README](web/README.md)、[Desktop README](desktop/README.md)。修改工具、技能或提示词时，请同步对应说明并运行相关契约测试。

真实模型评测会调用已配置的服务并可能产生费用：

```bash
uv run python -m excelmanus.bench --all
uv run python -m excelmanus.bench --suite bench/cases/suite_realistic.json --case R01
```

运行前按 [Bench 文档](bench/README.md) 准备隔离配置和夹具。`--all` 只包含默认短套件，长套件需显式指定；历史评测报告不能替代当前版本的验证结果。

欢迎通过 [Issues](https://github.com/kilolonion/excelmanus/issues) 和 Pull Request 反馈问题、改进文档或贡献代码。

## 许可证

[Apache License 2.0](LICENSE) © kilolonion。另见 [用户服务协议](docs/terms-of-service.md)。
