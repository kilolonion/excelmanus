<p align="center">
  <img src="logo.svg" width="320" alt="ExcelManus" />
</p>

> **WIP** — 项目仍在积极开发中，API 尚未稳定。欢迎试用和反馈，但请勿用于生产环境。

用自然语言操作 Excel —— 读数据、写公式、跑分析、画图表，说一句话就够了。

ExcelManus 是一个基于大语言模型的 Excel 智能代理。你不需要记住任何函数语法或者写 VBA，只需要告诉它你想做什么，它会自己完成剩下的事。支持 OpenAI、Claude、Gemini 等主流模型，从 URL 自动识别 Provider，无需手动切换。

## 能做什么

- **读写 Excel** — 读取单元格、写入公式、VLOOKUP、批量填充，自动处理多 sheet
- **数据分析** — 筛选、排序、分组聚合、透视表，复杂逻辑自动生成 Python 脚本执行
- **图表生成** — 柱状图、折线图、饼图……描述你想要的样子，嵌入 Excel 或导出图片
- **图片理解** — 贴一张表格截图，它能识别内容并提取为结构化数据（需 VLM 支持）
- **跨表操作** — 创建 / 复制 / 重命名工作表，跨表搬运数据
- **自动备份** — 每次写操作自动保留副本，`/undo` 一键回滚
- **持久记忆** — 记住你的偏好和常见操作模式，跨会话可用
- **技能扩展** — 通过 Skillpack 机制添加领域知识，一个 Markdown 文件就是一个技能
- **MCP 集成** — 接入外部 MCP Server 扩展工具能力
- **Subagent** — 大文件或复杂任务自动委派给子代理处理

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

> `.env` 仅用于首次启动的初始配置。第一次运行后，所有配置会自动迁移到本地数据库（`~/.excelmanus/excelmanus.db`），后续通过 CLI `/config` 命令或 Web UI 设置面板管理即可。

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
- 内嵌 Excel 查看器 — 在侧边面板直接预览和编辑表格，支持选区引用
- 变更追踪 — 每次操作后实时显示 diff 对比
- 多会话管理 — 自动保存对话历史，随时切换
- 设置面板 — 模型配置、技能管理、MCP Server、记忆、规则，全部可视化操作
- 配置分享 — 一键导出所有模型配置（含 API Key），加密后分享给他人导入
- 审批流程 — 高风险操作弹窗确认，支持一键撤销
- 文件拖拽 — 直接拖文件到输入框引用
- `@` 提及 — 输入 `@` 引用文件、工具或技能

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

SSE 流推送 38 种事件类型，覆盖思考过程、工具调用、子代理执行、Excel 预览 / diff、审批请求等。

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
- **操作审批** — 高风险写入需要用户 `/accept` 确认，所有可审计操作记录变更 diff 和快照
- **自动备份** — 默认开启，写操作在 `outputs/backups/` 保留副本，支持 `/undo` 回滚
- **MCP 工具白名单** — 外部 MCP 工具默认需要确认，可配置自动批准

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

协议详见 `docs/skillpack_protocol.md`。

## Bench 测试

内置自动化评测框架，用于批量验证 Agent 表现：

```bash
python -m excelmanus.bench --all                         # 运行全部
python -m excelmanus.bench --suite bench/cases/xxx.json  # 指定 suite
python -m excelmanus.bench --message "读取前10行"          # 单条测试
```

支持多轮对话用例、自动断言校验、结构化 JSON 日志、`--trace` 引擎内部追踪，以及 Suite 级并发执行。

## Docker

```bash
docker build -t excelmanus .
docker run --env-file .env -v $(pwd)/workspace:/workspace excelmanus
```

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
