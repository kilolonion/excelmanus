# ExcelManus v1.6.8 Release Notes

## 🎯 版本亮点

本版本带来三大核心升级：**OpenAI Codex 订阅集成**（Device Code Flow 无密钥连接私有模型）、**OpenAI Responses API 全链路支持**（新一代推理 API）、**聊天体验全面提升**（乐观 UI + 停滞检测 + 错误引导系统）。同时完成了 **Setup UI 现代化重构**（Vite + React + Tailwind CSS）、**uv 包管理器全面迁移**，并新增 11 个 LLM 提供商图标和大量 UI 精细化打磨。

---

## 🆕 新增功能

### OpenAI Codex 订阅集成

通过 **Device Code Flow**（RFC 8628）连接用户的 OpenAI Codex 订阅，自动发现私有模型，无需手填 API Key：

1. 用户在个人中心点击「连接 Codex」→ 获取 6 位验证码
2. 在 [auth.openai.com/codex/device](https://auth.openai.com/codex/device) 输入验证码授权
3. 连接成功后系统自动枚举并注册用户的私有 Codex 模型

**后端实现：**
- `excelmanus/auth/providers/` — 新增 OAuth 提供商抽象层（`base.py` / `openai_codex.py` / `credential_store.py` / `resolver.py`）
- `CredentialStore` — Fernet 加密存储 `auth_profiles` 表，支持 CRUD
- `CredentialResolver` — 运行时凭证优先级：OAuth > user_key > system
- 数据库 v18 迁移：新增 `auth_profiles` 表（SQLite + PostgreSQL）
- 5 个新 API 端点：`/auth/codex/device-code/start` / `poll` / `status` / `refresh` / `disconnect`

**前端实现：**
- `web/src/app/auth/codex/` — Device Code 页面 + popup 回调处理
- `ProfilePage` 新增 `CodexProviderSection` 组件（验证码展示 + 轮询状态 + 已连接管理）
- 新手引导优化：OAuth 登录用户自动检查 Codex 连接，未连接时展示引导入口

> 需要在 ChatGPT 设置 → 安全 中开启 "Enable device code authentication for Codex"。

涉及文件：`excelmanus/auth/providers/`（新增）、`excelmanus/auth/router.py`、`excelmanus/session.py`、`excelmanus/database.py`、`web/src/app/auth/codex/`（新增）、`web/src/components/profile/ProfilePage.tsx`

---

### OpenAI Responses API 全链路支持

新增 `OpenAIResponsesClient` 适配新一代 `/v1/responses` 推理 API，支持 streaming / thinking 模式：

- `excelmanus/providers/openai_responses.py` — 全新 Responses API 客户端
- 所有 LLM 调用路径（engine / shortcuts / subagent）迁移到新 API
- `EXCELMANUS_USE_RESPONSES_API=1` 启用（或检测到 Codex 模型时自动切换）
- `CredentialResolver` 统一处理 Codex OAuth token 与普通 API Key 的选择逻辑

涉及文件：`excelmanus/providers/openai_responses.py`（新增）、`excelmanus/engine.py`、`excelmanus/shortcuts.py`、`excelmanus/subagent/`

---

### 错误引导系统（Error Guidance）

任务失败时自动分析错误类型，通过 `FAILURE_GUIDANCE` SSE 事件推送可操作建议：

- 覆盖场景：认证失败 / API 配额超限 / 网络超时 / 参数错误 / 权限不足 / 模型不可用等
- `FailureGuidance` 数据类携带：`category` / `code` / `title` / `message` / `retryable` / `diagnostic_id` / `actions`
- 前端 `FailureGuidanceCard` 组件展示错误标题 + 操作按钮（立即重试 / 检查模型设置 / 复制诊断 ID）

涉及文件：`excelmanus/error_guidance.py`（新增）、`excelmanus/events.py`、`excelmanus/api.py`、`web/src/components/chat/FailureGuidanceCard.tsx`（新增）

---

### 聊天体验优化

**乐观 UI：**
- 消息发送前立即显示用户气泡 + 助手加载状态
- 文件上传结果同步收集，无需等待异步完成
- `currentSessionId` 提前同步，避免 SessionSync 误清空消息

**停滞检测：**
- 新增 90 秒无数据自动中止机制
- 改进错误提示：区分连接失败 vs 响应停滞

**工具调用精准更新：**
- 工具调用事件携带 `tool_call_id`，前端按 ID 精准更新对应卡片，不再按顺序错位

涉及文件：`web/src/lib/chat-actions.ts`、`web/src/components/chat/ChatInput.tsx`、`excelmanus/engine_core/tool_dispatcher.py`

---

### Excel Store 乐观更新

所有写操作引入**快照 + 乐观更新 + 失败回滚**机制：

- `applyFile` / `applyAll` / `discardFile` / `discardAll` — 操作前自动快照，失败时恢复
- `undoOperationById` — 立即禁用 UI 按钮，失败时恢复原状态
- `mergeAppliedFilesByOriginal` — 去重合并逻辑，避免重复条目

涉及文件：`web/src/stores/excel-store.ts`

---

### Setup UI 现代化重构

Windows 图形化部署工具的内嵌 Web UI 从旧版迁移至现代技术栈：

- **Vite + React + Tailwind CSS** — 更快的热重载与更现代的 UI 风格
- 快速启动版本一致性检查
- Node.js ≥ v18 校验，Python 启动器 fallback
- `.env` UTF-8 BOM 问题修复，端口配置解析优化

涉及文件：`deploy/setup-ui/`（全面重构）

---

### 会话标题即时生成

对话标题从"等 AI 生成"改为**首轮立即截取用户消息**作为初始标题，后台异步 AI 精炼优化：

- 无感延迟：用户发消息后侧边栏立即显示标题
- 后台 AI 生成更语义化的标题后静默更新

涉及文件：`excelmanus/api.py`、`excelmanus/session_title.py`

---

## 🔧 改进

### 模型配置增强

- `ModelInfo` 接口扩展：新增 `display_name` / `resolved_model` / `provider` / `user_scoped` 字段
- `ModelSelector` 区分系统模型 vs 用户私有模型，视觉差异化展示
- `TopModelSelector` 改进响应式布局
- `StatusFooter` 显示当前模型 provider 信息
- `ModelTab` 支持 Codex 私有模型展示和选择
- 新增 MiniMax provider 支持：自动检测 base_url，内置推荐模型列表

涉及文件：`web/src/components/settings/ModelTab.tsx`、`web/src/components/chat/TopModelSelector.tsx`、`web/src/components/sidebar/StatusFooter.tsx`、`web/src/lib/api.ts`

### 管理/设置页面增强

- **Admin 用量展示** — 按用户/提供商/模型三级展示 LLM 调用量（calls / prompt tokens / completion tokens / 最后使用时间），按总 token 排序
- **LoginConfigTab** — 改进登录配置选项布局，新增 OAuth 提供商开关
- **MCPTab / MemoryTab / RulesTab / SkillsTab** — 统一设置页交互模式，改进错误处理
- Admin OAuth 配置按钮移动端紧凑模式适配

涉及文件：`web/src/app/admin/page.tsx`、`web/src/components/settings/`

### 11 个 LLM 提供商图标

新增完整的 SVG 图标覆盖主流 LLM 提供商：

| 图标文件 | 提供商 |
|---------|--------|
| `AlibabaCloud.svg` | 阿里云百炼 |
| `Baidu.svg` | 百度文心 |
| `ByteDance.svg` | 字节跳动豆包 |
| `Huawei.svg` | 华为盘古 |
| `HuggingFace.svg` | Hugging Face |
| `Meta.svg` | Meta Llama |
| `Mistral.svg` | Mistral AI |
| `Nvidia.svg` | NVIDIA NIM |
| `Perplexity.svg` | Perplexity AI |
| `QQ.svg` | 腾讯混元 |
| `X.svg` | xAI Grok |

涉及文件：`web/public/providers/`（新增 11 个 SVG）

### uv 包管理器全面迁移

所有部署脚本和 CI/CD 流程优先使用 [uv](https://docs.astral.sh/uv/)（依赖安装速度提升 10-100x）：

- `.python-version` — 锁定 Python 3.12
- CI/CD — 使用 `astral-sh/setup-uv@v4` 替代 setup-python + pip
- `deploy/start.sh` / `start.ps1` — 优先 `uv sync`，保留 pip fallback
- `deploy/update.sh` / `update.ps1` — 优先 `uv sync --project`
- `deploy/deploy.sh` / `deploy.ps1` — 远程服务器检测 uv 可用时优先使用

涉及文件：`.python-version`（新增）、`.github/workflows/python-ci.yml`、`deploy/start.sh`、`deploy/start.ps1`、`deploy/update.sh`、`deploy/update.ps1`、`deploy/deploy.sh`、`deploy/deploy.ps1`

### UI 组件精细化

- **AssistantMessage / ChatInput** — 优化交互流程与状态管理
- **CodePreviewModal / ImagePreviewModal** — 统一预览弹窗样式
- **MessageActions** — 增强消息操作按钮可访问性
- **ExcelFilesBar / SessionList** — 侧边栏列表渲染性能优化
- **FlatFileListView / TreeNodeItem** — 文件树组件细节打磨
- 认证页面全面响应式改进（登录/注册/Admin），移动端行内 OAuth 按钮紧凑布局

### Memory Store 优化

- 改进内存存储性能与资源管理
- 优化 `ImageLifecycleManager` 资源释放逻辑

涉及文件：`excelmanus/memory.py`

---

## 🐛 关键修复

### 移动端聊天附件/操作按钮溢出
修复移动端 chat 输入框区域操作 chip 和附件 chip 在小屏幕上溢出布局的问题，改为紧凑内联展示。

### Admin OAuth 移动端配置按钮
Admin 页面 OAuth 提供商配置按钮在移动端自动切换为紧凑布局，避免按钮超出容器。

### 欢迎页 Demo 卡片重复发送
修复快速连点 demo 卡片时可能重复触发发送的问题，加入防抖与禁用逻辑。

### MiniMax /models 404 回退
MiniMax provider 的 `/models` 接口返回 404 时，自动返回内置推荐模型列表，"自动检测模型" 功能不再报错。

---

## 🗑️ 移除

- `deploy/build_setup.bat` — Nuitka 构建脚本已废弃（Setup UI 已迁移 Vite）
- `deploy/requirements-setup.txt` — setup 专用依赖已整合
- `deploy/setup_app.py` — Python GUI 安装器已由 C# + Vite UI 替代

---

## 📦 文件变更统计（vs v1.6.7）

```text
~180 files changed, 12000+ insertions(+), 4000+ deletions(-)
```

### 新增文件

| 文件 | 说明 |
|------|------|
| `excelmanus/auth/providers/__init__.py` | OAuth 提供商模块入口 |
| `excelmanus/auth/providers/base.py` | AuthProvider 基类 + 数据类 |
| `excelmanus/auth/providers/openai_codex.py` | OpenAI Codex OAuth 提供商 |
| `excelmanus/auth/providers/credential_store.py` | 加密凭证存储 |
| `excelmanus/auth/providers/resolver.py` | 凭证优先级解析器 |
| `excelmanus/providers/openai_responses.py` | Responses API 客户端 |
| `excelmanus/error_guidance.py` | 结构化失败引导引擎 |
| `excelmanus/session_title.py` | 会话标题生成模块 |
| `.python-version` | Python 版本锁定（3.12） |
| `web/src/app/auth/codex/page.tsx` | Device Code 主页面 |
| `web/src/app/auth/codex/callback.tsx` | Device Code 回调处理 |
| `web/src/components/chat/FailureGuidanceCard.tsx` | 错误引导卡片组件 |
| `web/public/providers/AlibabaCloud.svg` | 阿里云图标 |
| `web/public/providers/Baidu.svg` | 百度图标 |
| `web/public/providers/ByteDance.svg` | 字节跳动图标 |
| `web/public/providers/Huawei.svg` | 华为图标 |
| `web/public/providers/HuggingFace.svg` | HuggingFace 图标 |
| `web/public/providers/Meta.svg` | Meta 图标 |
| `web/public/providers/Mistral.svg` | Mistral 图标 |
| `web/public/providers/Nvidia.svg` | NVIDIA 图标 |
| `web/public/providers/Perplexity.svg` | Perplexity 图标 |
| `web/public/providers/QQ.svg` | QQ 图标 |
| `web/public/providers/X.svg` | xAI 图标 |
| `tests/test_codex_responses_migration.py` | Codex/Responses API 测试 |
| `tests/test_auth_providers.py` | Auth 提供商测试（33 个） |
| `tests/test_auth_admin_usage.py` | Admin 用量统计测试 |

### 主要修改文件

| 文件 | 说明 |
|------|------|
| `excelmanus/api.py` | Codex 端点 / 标题生成 / 错误引导 / Responses API |
| `excelmanus/auth/router.py` | Device Code Flow 端点 |
| `excelmanus/database.py` | v18 迁移：auth_profiles 表 |
| `excelmanus/session.py` | CredentialStore 集成 / Codex 模型解析 |
| `excelmanus/engine.py` | Responses API 客户端切换 |
| `excelmanus/memory.py` | 性能优化 |
| `web/src/components/profile/ProfilePage.tsx` | Codex 连接 UI |
| `web/src/components/settings/ModelTab.tsx` | 私有模型展示 |
| `web/src/app/admin/page.tsx` | LLM 用量可视化 |
| `web/src/stores/excel-store.ts` | 乐观更新 + 快照 |
| `web/src/lib/chat-actions.ts` | 乐观 UI + 停滞检测 + 错误引导 |
| `web/src/lib/api.ts` | Codex API + ModelInfo 扩展 |
| `deploy/setup-ui/` | Vite + React + Tailwind 重构 |
| `.github/workflows/python-ci.yml` | uv 迁移 |
| `deploy/start.sh` / `start.ps1` | uv 优先 |

---

## ⬆️ 升级说明

v1.6.7 → v1.6.8 **无破坏性变更**，直接拉取更新即可：

```bash
git pull origin main
uv sync --all-extras      # 更新依赖（新增 cryptography 依赖于 auth_profiles 表）
uv run excelmanus-api     # 重启后端（自动执行 DB v18 迁移）
```

如使用 Docker：

```bash
docker pull kilol/excelmanus-api:1.6.8
docker pull kilol/excelmanus-web:1.6.8
docker compose -f deploy/docker-compose.yml up -d
```
