# ExcelManus v1.7.1 Release Notes

**发布日期**: 2026-03-04

## 🔄 模型目录更新

- 从运行时上下文映射中移除已弃用的模型 ID（OpenAI 旧版、Gemini 1.5/2.0 代、Claude 3.x 别名）
- 新增显式迁移引导：用户切换/配置/导入已弃用模型 ID 时自动提示
- 更新默认值与预设推荐：
  - `EXCELMANUS_EMBEDDING_MODEL` 默认值改为 `text-embedding-3-small`
  - Anthropic/OpenRouter 预设使用 `claude-sonnet-4-6`
  - 已弃用的 Codex 预设 `codex-mini-latest` 替换为 `gpt-5-codex-mini`

---
## 🚀 部署基础设施升级

### Blue/Green 部署

- 新增 **Blue/Green 部署模式** — 候选实例启动在独立端口，健康检查通过后切换流量，旧实例优雅排水
- 参数支持：`BackendCandidatePort`、`BackendDrainSeconds`
- `deploy.ps1` / `deploy.sh` 双平台对齐实现

### Canary 金丝雀发布

- 新增 **Canary 分步发布** — Nginx upstream weight 分流，按步骤递增流量比例（默认 10% → 50% → 100%）
- 每步观察期（默认 60s），异常时自动回滚到 active 实例
- `Set-NginxCanaryWeight` / `Write-CanaryState` / `Test-CandidateHealth` 函数
- API 端点：`POST /deploy/canary/start`、`POST /deploy/canary/abort`

### 构建安全

- **Pre-build 备份** — 构建前备份 `.next/BUILD_ID`、`routes-manifest.json`、`standalone/`、`static/`
- 构建失败时自动从备份恢复，避免 `.next` 被部分覆盖导致运行版本损坏
- PM2 零停机 reload（已有进程用 `pm2 reload`，新进程用 `pm2 start`）

### 跨机器部署锁

- 远程部署锁机制，防止多人/多机同时部署冲突
- `GET /deploy/lock/status` — 查询锁状态（holder_host / elapsed / expired）
- `.env.deploy.example` 示例配置文件

### 版本管理增强

- Version manifest 新增 `min_frontend_build_id` / `min_backend_version` 兼容性字段
- **指纹检测重启** — `connection-store` 捕获重启前版本指纹，Phase 1 快速检测版本变化（无需等待后端下线）
- `use-version-poll` 自适应调度（`schedulePoll` 替代固定 `setInterval`）
- `RollbackPanel` Canary UI 增强

## 🤖 渠道管理 & Bot UX 增强

### 渠道设置 API

- **完整设置面板** — `GET/PUT /api/v1/channels/settings` 支持以下配置项：

| 字段 | 说明 |
| --- | --- |
| `admin_users` | 管理员用户列表 |
| `group_policy` | 群聊策略（open / whitelist / blacklist） |
| `group_whitelist` / `group_blacklist` | 群聊白/黑名单 |
| `allowed_users` | 允许使用的用户列表 |
| `default_concurrency` | 默认并发模式 |
| `default_chat_mode` | 默认聊天模式 |
| `public_url` | 公开访问地址（用于下载链接） |
| `tg_edit_interval_min/max` | Telegram 编辑间隔范围 |
| `qq_progressive_chars/interval` | QQ 渐进发送参数 |
| `feishu_update_interval` | 飞书卡片更新间隔 |

- 枚举验证 + 数值范围验证 + 环境变量锁定字段保护
- `_propagate_channel_settings` 热更新到运行中的 handler

### 群聊策略 & 管理员控制

- **Group Policy** — `open` / `whitelist` / `blacklist` 三种群聊准入策略
- 群聊拒绝冷却机制，避免重复提示
- `/admin` 命令及子命令（运行时用户/群组管理）
- 管理员自动放行 + 动态用户管理

### 渠道绑定 UI 重构

- `ChannelBindSection` 提取为独立组件
- 新增 `ChannelsTab` 设置面板（集成到 SettingsDialog）
- `ChannelsPanel` 渠道管理面板

### 文件下载链接

- 短效 JWT 下载令牌（30 分钟有效期）
- `POST /api/v1/files/download/link` 生成链接
- `GET /api/v1/files/dl/{token}` 公开下载端点
- Bot 端文件回传：直接发送 → 失败回退下载链接 → 再回退 Web 提示

### SSE & 输出增强

- Safe-mode 渠道旁路：tool_call 通知绕过 safe_mode 过滤
- 工具参数摘要 + 错误反馈增强
- 批量进度、子代理事件、LLM 重试事件处理

## 🔧 引擎改进 & 错误处理

### ResponsesAPIError 状态码

- `OpenAIResponsesClient` 抛出的 `ResponsesAPIError` 现在携带 `status_code` 属性
- retry / `classify_failure` 管线能正确识别 429 / 5xx / 401 等状态码
- 修复此前所有 HTTP 错误被视为不可分类 "内部错误" 的回归问题

### 文本工具调用恢复

- 新增 `_extract_text_tool_calls` — 当模型以纯文本输出 JSON 工具调用时，自动解析恢复
- `_find_balanced_json` — 平衡大括号匹配
- `_match_tool_in_dict` — 工具名/参数提取

### Prompt 策略更新

- `35_run_code_patterns.md` / `complex_task.md` / `large_data_write.md` 优化
- `explorer.md` / `subagent.md` 子代理提示词更新

## 🎨 Web UI 优化

- **middleware → proxy 迁移** — `middleware.ts` 重命名为 `proxy.ts`（逻辑不变，命名更清晰）
- **Provider 品牌色** — `provider-brand.ts` 单一来源，TopModelSelector 使用品牌色视觉区分
- **MessageActions** — UI 交互细节优化
- **ModelTab** — 样式改进
- **Dockerfile** — 多阶段构建优化 + `.dockerignore` 减小构建上下文

## 🔄 模型目录更新

- 从运行时上下文映射中移除已弃用的模型 ID（OpenAI 旧版、Gemini 1.5/2.0 代、Claude 3.x 别名）
- 新增显式迁移引导：用户切换/配置/导入已弃用模型 ID 时自动提示
- 更新默认值与预设推荐：
  - `EXCELMANUS_EMBEDDING_MODEL` 默认值改为 `text-embedding-3-small`
  - Anthropic/OpenRouter 预设使用 `claude-sonnet-4-6`
  - 已弃用的 Codex 预设 `codex-mini-latest` 替换为 `gpt-5-codex-mini`

---
## 升级指南

```bash
# Docker 用户
docker pull kilol/excelmanus-api:1.7.1
docker pull kilol/excelmanus-web:1.7.1
docker pull kilol/excelmanus-sandbox:1.7.1

# 源码用户
git pull
uv sync --all-extras
cd web && npm install
```

### 新增环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `EXCELMANUS_PUBLIC_URL` | `""` | 公开访问地址（Bot 下载链接使用） |
| `BACKEND_BLUEGREEN` | `false` | 启用 Blue/Green 部署 |
| `BACKEND_CANARY` | `false` | 启用 Canary 发布 |
| `BACKEND_CANDIDATE_PORT` | `8001` | 候选实例端口 |
| `BACKEND_DRAIN_SECONDS` | `30` | 旧实例排水时间 |
| `CANARY_STEPS` | `"10,50,100"` | 金丝雀流量阶梯 |
| `CANARY_OBSERVE_SECONDS` | `60` | 每步观察时间 |

### 注意事项

- Blue/Green 和 Canary 部署需要 Nginx 配置 `upstream backend` 块
- 渠道设置通过 Web UI ChannelsTab 或 API 配置，环境变量锁定的字段不可通过 API 修改
- 文本工具调用恢复对所有 provider 自动生效，无需额外配置

## 🧪 测试

- 新增 **test_deploy_improvements.py** — 部署锁 / env 解析 / 构建回滚测试
- 新增 **test_manifest_compat.py** — 版本兼容性字段测试
- 新增 **test_channel_settings.py** — 渠道扩展设置 API 测试
- 新增 **test_group_policy.py** — 群聊策略 & 管理员控制测试
- 新增 **test_responses_api_error_handling.py** — ResponsesAPIError 错误处理回归测试
- 新增 **test_text_tool_call_recovery.py** — 文本工具调用恢复测试
- 更新既有测试：test_channels / test_multiuser_channel / test_require_bind_setting / test_codex_responses_migration

