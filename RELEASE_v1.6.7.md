# ExcelManus v1.6.7 Release Notes

## 🎯 版本亮点

本版本引入 **新用户引导系统（Onboarding Wizard + Coach Marks）**、**ClawHub 技能市场集成**、**集中数据管理（data_home）**、**自动更新机制**，并对 **模型配置**（Profile 扩展 / 远程模型列表 / Thinking 模式）、**记忆系统**（语义注入 / 过期清理 / LLM 维护代理）、**LLM 调用可靠性**（自动重试 / 内联 thinking 检测）进行了全面增强。**Windows 安装程序**重构为 Vite + React 现代前端，支持快速启动、版本一致性检查、Node.js 版本验证。前端完成设置页大幅扩展、认证流程加固、Excel Diff 改进等 50+ 组件优化。**CI/CD** 新增前端构建任务、安全扫描扩大范围；**Docker** 升级 Node 22 + 非 root 用户运行。

---

## 🆕 新增功能

### 新用户引导系统（Onboarding）

首次使用时提供全屏引导向导，帮助用户完成初始配置：

- **OnboardingWizard** — API Key 配置 / 模型选择 / 功能介绍的分步引导
- **CoachMarks** — 两阶段教练标记（基础功能探索 + 进阶功能探索）
- **TourTooltip / TourOverlay** — 高亮目标元素的引导 UI 组件
- **SettingsTourHints** — 设置页内的上下文提示

涉及文件：`web/src/components/onboarding/`（新增目录）、`web/src/stores/onboarding-store.ts`（新增）、`web/src/app/client-layout.tsx`

### ClawHub 技能市场

集成 ClawHub 在线技能市场，支持搜索、安装、更新第三方技能包：

- **ClawHubClient** — HTTP 客户端（API 直连 + CLI 降级混合模式）
- **ClawHubLockfile** — 兼容 `clawhub` CLI 的 `.clawhub/lock.json` 锁文件管理
- **SkillpackManager** — 集成 ClawHub 操作（search / install / update / list）
- **前端 SkillsTab** — 技能设置页新增市场搜索与安装 UI
- **CLI** — `/clawhub` 子命令（search / install / update / list / info）

涉及文件：`excelmanus/skillpacks/clawhub.py`（新增）、`excelmanus/skillpacks/clawhub_lockfile.py`（新增）、`excelmanus/skillpacks/manager.py`、`web/src/components/settings/SkillsTab.tsx`

### 集中数据管理（data_home）

统一管理用户数据目录 `~/.excelmanus/data`，解耦项目代码与用户数据：

- **安装注册** — 记录安装路径与版本到 `~/.excelmanus/installations.json`
- **配置迁移** — 项目 `.env` 自动提取到 `~/.excelmanus/config.env`（最低优先级）
- **数据迁移** — 项目级 uploads / outputs / users 迁移到集中目录

涉及文件：`excelmanus/data_home.py`（新增）、`excelmanus/config.py`、`excelmanus/api.py`

### 自动更新

支持检测新版本并一键更新：

- **updater** — 版本检测（GitHub API）、代码拉取、依赖安装、数据库迁移预验证
- **API 路由** — `GET /api/v1/version`、`POST /api/v1/update/check`、`POST /api/v1/update/apply`
- **前端 VersionTab** — 版本信息展示与更新操作 UI
- **ServerRestartOverlay** — 更新后服务重启遮罩层
- **三平台更新脚本** — `deploy/update.sh`、`deploy/update.bat`、`deploy/update.ps1`

涉及文件：`excelmanus/updater.py`（新增）、`excelmanus/api_routes_version.py`（新增）、`web/src/components/settings/VersionTab.tsx`（新增）、`web/src/components/ServerRestartOverlay.tsx`（新增）

### VLM 单轮合并提取

强模型（如 Gemini 2.5 Pro）可一次性提取结构 + 数据 + 样式，减少 VLM 调用次数：

- 按模型分级路由：strong → 始终 single_pass，standard → 条件 single_pass，weak → pipeline
- 单轮失败自动回退到 4 阶段 Pipeline

涉及文件：`excelmanus/pipeline/single_pass.py`（新增）、`excelmanus/engine_core/tool_handlers.py`

### 记忆维护代理

LLM 驱动的记忆后台维护，自动去重、合并、清理低质量条目：

- 支持配置触发条件（最小条目数 / 新增阈值 / 最小间隔）
- 维护结果结构化输出（merge / delete / keep 操作）

涉及文件：`excelmanus/memory_maintainer.py`（新增）

### 会话导出

支持将会话历史导出为 **Markdown / 纯文本 / EMX (JSON)** 三种格式。EMX (ExcelManus eXport) 为自定义 JSON 格式，可重新导入为完整会话。

涉及文件：`excelmanus/session_export.py`（新增）

---

## 🐛 关键修复

### 认证中间件 SSE 缓冲

`AuthMiddleware` 从 `BaseHTTPMiddleware` 重写为纯 ASGI 中间件，解决 Starlette `BaseHTTPMiddleware` 对 SSE 长连接的 chunk 缓冲问题，修复 SSE 事件投递延迟。

涉及文件：`excelmanus/auth/middleware.py`

### 降级模式启动

配置缺失（API Key / Base URL / Model）时不再崩溃，改为以降级模式启动，引导用户通过前端设置页完成配置。

涉及文件：`excelmanus/api.py`

### 登录自动登录竞态

修复 `autoLoginAttemptedRef` 在 recentAccounts rehydrate 前被消费导致的二次自动登录问题，改为单次尝试 + mountedRef 保护。

涉及文件：`web/src/app/login/page.tsx`

### Explorer prescan 竞态

新增 `_explore_in_progress` 标记防止 TOCTOU 竞态导致重复探索注入。

涉及文件：`excelmanus/engine.py`

### 数据库迁移安全加固

- `_safe_execute_sql()`: SQLite ALTER TABLE 幂等保护（PRAGMA table_info 检查列是否已存在）
- `_backup_before_migrate()`: 迁移前自动备份 DB + WAL/SHM
- 失败时保留已成功版本的 schema_version

涉及文件：`excelmanus/database.py`

---

## 🔧 改进

### 模型配置增强

- **ModelProfile 扩展** — 新增 `thinking_mode`（thinking 参数格式覆盖）、`model_family`（实际模型族）、`custom_extra_body`、`custom_extra_headers`
- **远程模型列表** — 前端 ModelTab 支持从 API 端点拉取可用模型列表
- **数据库 Migration 17** — model_profiles 表同步新增 4 列
- **ModelTab 大幅重构** — 主模型区移除（统一为 profiles），新增远程模型浏览、profile 高级字段编辑

涉及文件：`excelmanus/config.py`、`excelmanus/database.py`、`excelmanus/stores/config_store.py`、`web/src/components/settings/ModelTab.tsx`

### LLM 调用可靠性

- **自动重试** — 遇到 5xx / 429 / 网络错误时指数退避重试（可配置 `llm_retry_max_attempts` / `base_delay` / `max_delay`），优先使用 `Retry-After` 头
- **内联 Thinking 检测** — `InlineThinkingStateMachine` 流式检测 `<thinking>` 标签（兼容中转站将 extended thinking 混入 text block 的情况）
- **Provider 增强** — Claude / Gemini / OpenAI 三个 Provider 均增强 thinking 内容提取与流式转发

涉及文件：`excelmanus/engine_core/llm_caller.py`、`excelmanus/providers/stream_types.py`、`excelmanus/providers/claude.py`、`excelmanus/providers/gemini.py`、`excelmanus/providers/openai_responses.py`

### 记忆系统增强

- **语义记忆动态注入** — 有语义记忆时不再全量静态注入，改为按用户消息相关性检索后注入
- **图片生命周期管理** — `ImageLifecycleManager` 三维策略（keep_rounds / max_active / token_budget），替代粗暴的立即降级
- **记忆过期清理** — `cleanup_expired(days)` 自动清理过期条目
- **提取质量升级** — 反例引导 + 跨会话复用原则，避免提取一次性任务细节
- **FileMemoryBackend** — 主题文件聚合替代 MEMORY.md 直读，消除双写冗余

涉及文件：`excelmanus/memory.py`、`excelmanus/memory_extractor.py`、`excelmanus/persistent_memory.py`、`excelmanus/stores/file_memory_backend.py`

### Compaction 压缩改进

LLM 摘要失败时新增规则化极简摘要兜底（提取工具调用序列 + 关键结果），替代此前的纯硬截断。

涉及文件：`excelmanus/compaction.py`

### 工具系统

- **ToolDef 智能截断** — 二分法 list 缩减 + dict / string 阶段化截断 + 首尾保留（truncate_head_chars / truncate_tail_chars）
- **Sheet 名三级模糊匹配** — 精确 → 忽略大小写 → SequenceMatcher fuzzy（阈值 0.6）
- **SACR 紧凑记录** — 去除 null/NaN 键的 DataFrame → list[dict] 转换，减少 74% token

涉及文件：`excelmanus/tools/registry.py`、`excelmanus/tools/_helpers.py`、`excelmanus/tools/data_tools.py`

### api.py God Module 拆分

api.py 从 6827 → 6042 行（-785 行），提取 3 个子模块：

- `api_sse.py`（418 行）— SSE 序列化 + SessionStreamState
- `api_routes_mcp.py`（284 行）— MCP Server CRUD
- `api_routes_rules.py`（261 行）— Rules CRUD + Memory API

涉及文件：`excelmanus/api_sse.py`（新增）、`excelmanus/api_routes_mcp.py`（新增）、`excelmanus/api_routes_rules.py`（新增）

### 前端设置页全面扩展

- **RuntimeTab** — 大幅扩展，涵盖会话 / 多用户 / 记忆 / VLM / 压缩 / 重试等 30+ 配置项
- **MemoryTab** — 新增记忆维护与过期清理配置
- **SettingsDialog** — 新增 VersionTab 标签页

### 前端 UI 改进

- **ApprovalModal** — 审批弹窗大幅增强
- **ExcelDiffTable** — Diff 展示改进
- **UniverSheet** — 触控适配优化
- **WelcomePage** — 欢迎页改进
- **SessionList** — 会话列表增强
- **凭证加密** — 浏览器端凭证加密存储（credential-crypto）
- **Next.js Middleware** — 路由保护

### 会话管理增强

- `pending_creates` 防重入保护
- `restored_readonly` 懒恢复会话标记，使用更短 TTL
- `broadcast_model_profiles` 向所有活跃会话广播模型档案变更
- `notify_file_deleted/renamed` 并发安全（list 快照 + try-except）

涉及文件：`excelmanus/session.py`

### 部署与安装程序增强

- **Setup UI 现代化重构** — Windows 安装向导前端从内嵌 HTML 字符串重写为 Vite + React + Tailwind CSS 单文件应用，经 `embed_static.py` 嵌入 C# exe
- **快速启动与版本一致性检查** — 新增 `.install_complete` 标记与 `pyproject.toml` 版本比对，版本不一致时自动触发重新部署
- **Node.js 版本验证 (E6)** — 环境检测要求 Node.js ≥ v18，低版本给出明确提示
- **Python 启动器回退 (E2)** — `python` 不可用时自动尝试 `py -3` launcher
- **.env UTF-8 BOM 修复 (E37)** — 使用无 BOM 的 UTF-8 写入 `.env`，修复 python-dotenv 解析首行失败
- **.env 端口配置解析** — 启动时从 `.env` 读取后端/前端端口配置
- **start.sh / start.ps1** — 集中数据目录支持、环境检测增强
- **deploy.sh / deploy.ps1** — macOS sed 兼容性修复、standalone 检测加固
- **应用重命名** — `ExcelManusDeployTool.exe` → `ExcelManus.exe`，统一品牌名称
- **部署向导精简** — 从三步简化为两步（环境检测 → 启动部署），移除前端内置的 LLM 配置步骤

### 自动更新增强

- **Gitee API 优先** — 版本检测优先走 Gitee API，失败后回退 GitHub API
- **GitHub remote 回退** — `git fetch origin` 失败时自动添加并尝试 `github` remote
- **版本读取绕过模块缓存** — `_read_version_from_disk()` 直接从磁盘读取 `__init__.py` / `pyproject.toml`，避免 git pull 后 Python 模块缓存返回旧版本
- **备份失败清理** — 备份过程失败时自动清理残留目录
- **Git stash 容错** — stash 失败不再阻断更新流程
- **数据库迁移预检简化** — 仅检查连接可达性与 schema 版本，迁移 SQL 由服务启动时自动执行

### CI/CD 与 Docker

- **前端构建 CI** — `python-ci.yml` 新增 `frontend-build` 任务（Node 22 + `npm ci` + `npm run build`）
- **安全扫描扩大范围** — `security-secrets.yml` 移除 path 过滤，所有 PR/push 均触发扫描
- **Docker 版本解析修复** — `docker-multiarch.yml` 中 `grep -oP` 替换为 `sed`，兼容非 GNU 环境
- **Docker 安全加固** — 后端 Dockerfile 新增非 root 用户 `appuser`，依赖安装改为 `.[all]`
- **Node.js 升级** — 前端 Docker 基础镜像从 node:20-alpine 升级至 node:22-alpine

### 文档与品牌

- **README 双源推荐** — 国内用户优先推荐 Gitee clone，Releases 同时提供 Gitee + GitHub 链接
- **引导文案更新** — 技能包导入来源更新为「Gitee/GitHub」

### 提示词策略更新

- 核心法则 Think-Act 协议精简、信号驱动分级
- 新增合并单元格处理策略（`merged_cell_handling.md`）
- 记忆策略无条件注入（`memory_strategy.md`）
- 沙盒感知无条件注入（移除 full_access 条件）

---

## 📦 文件变更统计（vs v1.6.6）

```text
231 files changed, 29881 insertions(+), 6863 deletions(-)
```

### 新增文件

| 文件 | 说明 |
| ---- | ---- |
| `excelmanus/data_home.py` | 集中数据目录管理 |
| `excelmanus/updater.py` | 自动更新检测与执行 |
| `excelmanus/api_routes_version.py` | 版本与更新 API 路由 |
| `excelmanus/api_sse.py` | SSE 序列化 + StreamState |
| `excelmanus/api_routes_mcp.py` | MCP Server CRUD 路由 |
| `excelmanus/api_routes_rules.py` | Rules/Memory CRUD 路由 |
| `excelmanus/memory_maintainer.py` | LLM 记忆维护代理 |
| `excelmanus/session_export.py` | 会话导出 |
| `excelmanus/shortcuts.py` | 快捷操作注册表 |
| `excelmanus/pipeline/single_pass.py` | VLM 单轮合并提取 |
| `excelmanus/skillpacks/clawhub.py` | ClawHub 客户端 |
| `excelmanus/skillpacks/clawhub_lockfile.py` | ClawHub lockfile 管理 |
| `excelmanus/engine_core/llm_client_manager.py` | LLM 客户端管理器 |
| `excelmanus/prompts/strategies/merged_cell_handling.md` | 合并单元格策略 |
| `deploy/update.sh` | Linux/macOS 更新脚本 |
| `deploy/update.bat` | Windows 更新脚本 |
| `deploy/update.ps1` | PowerShell 更新脚本 |
| `deploy/icon.ico` | 安装程序图标 |
| `deploy/embed_static.py` | Vite 构建产物 → C# 嵌入文件生成器 |
| `deploy/EmbeddedAssets.cs` | 自动生成的前端 HTML 嵌入文件 |
| `deploy/setup-ui/` | Vite + React + Tailwind 安装向导前端项目 |
| `web/src/components/onboarding/` | 新用户引导组件目录（10+ 文件） |
| `web/src/stores/onboarding-store.ts` | 引导状态 Store |
| `web/src/components/ServerRestartOverlay.tsx` | 服务重启遮罩 |
| `web/src/components/settings/VersionTab.tsx` | 版本管理标签页 |
| `web/src/hooks/use-server-restart.ts` | 服务重启 hook |
| `web/src/lib/credential-crypto.ts` | 凭证加密工具 |
| `web/src/middleware.ts` | Next.js 中间件 |
| `web/public/providers/` | Provider 图标资源 |
| `tests/test_clawhub.py` | ClawHub 测试 |
| `tests/test_migration_safety.py` | 迁移安全测试（14 个） |
| `tests/test_claude_provider.py` | Claude Provider 测试 |
| `tests/test_compare_excel.py` | Excel 比对测试 |
| `tests/test_l1_reliability_fixes.py` | L1 可靠性修复测试 |
| `tests/test_l3_fixes.py` | L3 修复测试 |
| `tests/test_raw_result_sidechannel.py` | 原始结果侧通道测试 |
| `tests/test_session_export.py` | 会话导出测试 |

### 主要修改文件

| 文件 | 变更量 | 说明 |
| ---- | ------ | ---- |
| `excelmanus/api.py` | +2985/-1200 | 降级启动 / data_home / ClawHub 端点 / 配额 |
| `excelmanus/engine.py` | +791/-200 | 重试 / 语义记忆 / 图片生命周期 / prescan 竞态 |
| `excelmanus/session.py` | +383/-180 | 并发安全 / broadcast / restored_readonly |
| `excelmanus/memory.py` | +302/-30 | ImageLifecycleManager |
| `excelmanus/engine_core/context_builder.py` | +368/-50 | Playbook / 验证 / Panorama |
| `excelmanus/engine_core/tool_handlers.py` | +283/-60 | 单轮提取策略 / ClawHub |
| `excelmanus/compaction.py` | +103/-10 | 规则化摘要兜底 |
| `excelmanus/tools/registry.py` | +142/-50 | 智能截断增强 |
| `excelmanus/database.py` | +146/-30 | Migration 17 / 幂等保护 / 备份 |
| `excelmanus/auth/middleware.py` | +116/-60 | 纯 ASGI 重写 |
| `deploy/ExcelManusSetup.cs` | +1321/-200 | Windows 安装程序扩展（Vite UI / 版本检查 / Node ≥ 18 / py launcher） |
| `excelmanus/updater.py` | +180/-60 | Gitee API 优先 / 版本缓存绕过 / 备份清理 |
| `web/src/components/settings/ModelTab.tsx` | +988/-200 | 远程模型 / Profile 扩展 |
| `web/src/components/settings/RuntimeTab.tsx` | +914/-100 | 30+ 配置项扩展 |
| `web/src/components/settings/SkillsTab.tsx` | +1400/-600 | ClawHub 市场集成 |
| `web/src/app/login/page.tsx` | +177/-60 | 自动登录修复 / 凭证加密 |
| `web/src/app/globals.css` | +373/-10 | 引导系统样式 |
| `web/src/lib/api.ts` | +265/-20 | 新 API 函数 |
| `.github/workflows/` | +30/-20 | CI 前端构建 / 安全扫描扩大 / Docker 版本解析修复 |
| `deploy/Dockerfile` | +10/-3 | 非 root 用户 + `.[all]` 依赖 |
| `web/Dockerfile` | +2/-2 | Node 20 → 22 |
