# ExcelManus v1.7.0 Release Notes

**发布日期**: 2026-03-03

---

## 🧠 会话历史感知 — Episodic Memory

本版本新增 **Episodic Memory** 层，让 Agent 能够跨会话记住"上次做了什么"，在新会话首轮自动注入相关历史上下文：

### 数据层

- **session_summaries 表** — 新增 migration 20（SQLite + PostgreSQL），字段包含 `session_id`、`user_id`、`summary_text`、`task_goal`、`files_involved`、`outcome`、`unfinished`、`embedding`、`token_count`
- **SessionSummaryStore** — CRUD + 语义检索（`upsert` / `get_by_session` / `search_by_embedding` / `search_by_files` / `list_recent` / `delete`），支持 `user_id` 隔离

### 摘要生成

- **SessionSummarizer** — LLM 结构化摘要生成，输出 `task_goal` / `files_involved` / `outcome` / `unfinished` / `summary` 五字段 JSON
- 输入截断：12K tokens / 48K chars / 120 条消息上限
- 模型选择：优先 `aux_model` 节省 token，输出 `max_tokens=500`

### 生命周期集成

- `SessionManager` 初始化 `SessionSummaryStore` 并注入 `engine._session_summary_store`
- **4 个触发点**：`cleanup_expired` / `delete` / `clear_all_sessions` / `shutdown`（均在 `extract_and_save_memory` 之后）
- **门控**：`session_summary_enabled` + `session_turn >= min_turns` + 无已有摘要 + embedding 向量化（如可用）
- 自动排除当前会话，避免自引用

### 语义检索 & 注入

- `engine._search_session_history()` — **三路混合检索**：路径 A embedding 语义 Top-K → 路径 B 文件名精确匹配 → 路径 C 时间序兜底
- `chat()` 中与记忆/文件/技能检索并行启动（仅 `session_turn <= 1`）
- `context_builder._build_session_history_notice()` — 首轮/第二轮注入 dynamic prompt，格式："## 历史会话参考" + 时间/目标/文件/结果/摘要
- `session_turn > 1` 时返回空（零开销）

### 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `EXCELMANUS_SESSION_SUMMARY_ENABLED` | `true` | 总开关 |
| `EXCELMANUS_SESSION_SUMMARY_MIN_TURNS` | `3` | 最少对话轮次才生成摘要 |
| `EXCELMANUS_SESSION_SUMMARY_INJECT_TOP_K` | `3` | 注入历史会话数量 |
| `EXCELMANUS_SESSION_SUMMARY_MAX_TOKENS` | `800` | 注入 token 上限 |

## 🤖 多渠道 Bot 框架

全新的统一渠道抽象层，支持 **Telegram** · **QQ** · **飞书** 三种机器人渠道：

### 统一架构

- **ChannelAdapter** ABC — 统一消息收发、文件上传/下载、事件桥接接口
- **ChannelRegistry** — 适配器工厂注册
- **ExcelManusAPIClient** — 统一 REST API 封装（chat / approve / answer / abort）
- **message_handler** — 通用命令路由 + 结果分发
- **session_store** — JSON 持久化会话映射（支持 TTL 过期）

### 多用户绑定

- **ChannelBindManager** — 绑定码生成 / 确认 / TTL 管理
- auth middleware 支持 service token + `X-On-Behalf-Of` 委派认证
- auth router 新增绑定确认 / 查询 / 解绑端点
- Bot 端 `/bind` · `/bindstatus` · `/unbind` 命令

### 三种并发模式

| 模式 | 行为 | 适用场景 |
| --- | --- | --- |
| **Queue** (默认) | FIFO 串行，per-user `asyncio.Lock` | 普通对话 |
| **Steer** | 取消旧任务 + 后端 abort，立即处理新消息 | 快速切换话题 |
| **Guide** | 注入消息到运行中 Agent 的系统上下文 | 任务运行中补充指令 |

通过 `/concurrency` 命令切换，`engine.py` 新增 `_guide_messages` 队列 + `push_guide_message()` / `drain_guide_messages()`。

### 自适应流式输出

- **Telegram EditStreamStrategy** — 自适应编辑间隔（1.5s→3.0s）、打字光标 ◍、工具进度内联、首次刷新智能阈值
- **QQ BatchSendStrategy** — 段落级渐进发送（200 字符阈值 + 3s 间隔）、工具完成即时反馈、保活进度消息
- **飞书 CardStreamStrategy** — 进度事件集成到卡片底部、超长内容自动续卡、最终状态着色（green/red）
- **chunking** — 超长表格截断（默认 30 行）+ 省略标注

### 健壮性

- **HTTP 重试 + 指数退避** — `AsyncHTTPTransport(retries=2)` 连接级重试 + 应用层指数退避（429 尊重 `Retry-After`，上限 30s + jitter）
- **send_file API 化** — 文件下载改走 `api.download_file()` 获取字节传输
- **PendingInteraction TTL** — 30 分钟自动清理过期待确认项
- **EventBridge** — 跨渠道实时事件推送（审批/问答）

### 渠道配置管理

- **ChannelConfigStore** — 持久化渠道凭证（加密存储）
- **ChannelProfile** — 渠道档案管理
- **launcher 重构** — 支持热启动/停止单个渠道 + 凭证注入

## ⚡ Claude Prompt Cache 优化

解决了 Claude 模型首次请求延迟高的问题，TTFT 从 16s 降至 3-5s：

- **分层 Cache Breakpoint** — System Prompt 拆分为 `stable_prompt`（identity + rules + channel + access，session 级不变）和 `dynamic_prompt`（每请求/迭代变化的 notice）两个独立 system 消息
- **Cache 放置策略** — `cache_control: ephemeral` 从最后一个 block 改为**第一个 block**（稳定前缀），修复了 `runtime_metadata` 每次必变导致整个 system prompt cache 永远 MISS 的结构性问题
- **Session Cache 预热** — `warmup_prompt_cache()` 异步方法，发送 `max_tokens=1` 请求预热稳定前缀（仅对 ClaudeClient 生效）
- **Chitchat 无工具快速路径** — `route_mode="chitchat"` 时跳过 `build_v5_tools`，仅返回 `stable_prompt`，prompt tokens 从 28k 降至 ~3k

## 🔀 Chitchat 分层路由 + LLM 工具路由

### Chitchat 分层路由

- 安全门控：正则全匹配 + 无文件路径 + 无图片 + ≤50 字
- 短路返回 `route_mode="chitchat"`，空 `system_contexts`
- 多轮上下文降级：`active_skills` / `pending` / `tool` 角色 → 回退 `all_tools`
- 节省 ~97% token 开销（~35K → ~1K tokens）

### LLM 工具路由

- **ROUTE_TOOL_SCOPE 白名单** — `data_read` / `write` / `chart` / `vision` / `code` 五类工具范围映射
- **_classify_tool_route_llm()** — AUX 模型分类（2s 超时），按语义意图精准选择工具子集
- `build_v5_tools` 白名单过滤替代黑名单（清理旧版 `TAG_EXCLUDED_TOOLS` / `VISION_TOOLS`）

## 🖥️ 前端 UI 增强

### 渠道管理

- 新增 **ChannelsTab** — 渠道凭证配置 / 启停 / 状态监控
- 新增 **ChannelIcons** — 渠道图标组件
- **ProfilePage** 集成渠道绑定管理
- auth-api 新增渠道绑定 API

### SSE 事件

- 新增 `tool_call_notice` + `reasoning_notice` 事件处理
- 新增 `tool_notice` / `reasoning_notice` block 类型

### 聊天体验

- 上传失败文件跳过，仅成功图片发送 base64
- 恢复消息合成时间戳（轮次间隔 6 分钟）
- AssistantMessage / UserMessage / MessageStream 渲染优化

### 基础设施

- 新增 **GlobalRestartOverlay** — 服务重启遮罩层
- 新增 **connection-store** — 连接状态管理
- 新增 **backend-origin** — 后端地址解析
- `use-server-restart` / `use-version-poll` hooks 改进
- `onboarding-store` auth-aware rehydration 修复
- `runtime-config` 本地开发优先 `:8000` 回退
- **SettingsDialog** 集成 ChannelsTab

## ⚙️ 引擎核心改进

- **Guide 消息注入** — `_guide_messages` 队列 + `push_guide_message()` / `drain_guide_messages()`，Bot Guide 模式下运行中注入用户上下文
- **Chitchat 快速通道** — `max_iter=1`、`tools=[]`、跳过 FileRegistry 扫描
- **route_tool_tags 传递** — 路由结果的工具标签传递到 `build_v5_tools`
- **语义检索并行区** — `asyncio.gather` 集成记忆/文件/技能/历史会话四路并行检索
- **session cache 预热** — `acquire_for_chat` Phase 5 添加 fire-and-forget `warmup_prompt_cache()`
- **API 增强** — `ChatRequest` 新增 `channel` 字段；新增 `POST /chat/{session_id}/guide` 端点；EventBridge 推送审批/问答事件到 Bot 渠道
- **launcher 重构** — 始终创建，合并环境变量 + 持久化配置统一启动
- **_restart_reason** — draining 期间传递重启原因

## 📝 记忆与会话增强

- **MemoryExtractor 语义去重** — `_dedup_against_existing()` cosine 比对（阈值 0.88），自动过滤重复记忆
- **memory_maintainer** — 维护逻辑优化
- **session_title** — 标题生成改进
- **playbook/reflector** — 反思器优化
- **session_export** — 导出/恢复功能增强，支持 EMX v2 格式（含 Excel 预览）

## 🏗️ 部署

- Dockerfile 默认启用 telegram 渠道
- `deploy.sh` / `deploy.ps1` 默认启用 qq 渠道，追加 `EXCELMANUS_CHANNELS` 到 `.env`
- `docker-compose.yml` 传递 `EXCELMANUS_CHANNELS` 环境变量
- `start.sh` / `start.ps1` / `start.bat` 默认启用 qq 渠道
- `pyproject.toml` 版本号 1.6.9 → 1.7.0

## 🧪 测试

- 新增 **test_session_history.py** — 18 个 Episodic Memory 测试（SessionSummarizer 解析 / 检索 / 注入门控）
- 新增 **test_tiered_routing.py** + **test_tool_routing.py** — 78 个路由测试
- 新增 **test_channels.py** — 12 个渠道测试文件（并发模式 / Guide / Steer）
- 新增 **test_bot_ux_optimization.py** — 31 个 Bot UX 测试
- 新增 **test_cache_optimization.py** — 10 个 Cache 优化测试
- 更新 **test_session_export_restore** — 新增 EMX v2 excel_previews 导入测试

## 🗑️ 移除

- 清理旧版 `TAG_EXCLUDED_TOOLS` / `VISION_TOOLS` 遗留常量（policy.py），已由白名单路由替代

---

## 升级指南

```bash
# Docker 用户
docker pull kilol/excelmanus-api:1.7.0
docker pull kilol/excelmanus-web:1.7.0
docker pull kilol/excelmanus-sandbox:1.7.0

# 源码用户
git pull
uv sync --all-extras
cd web && npm install
```

### 新增环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `EXCELMANUS_CHANNELS` | `""` | 启用的 Bot 渠道（`telegram`,`qq`,`feishu`） |
| `EXCELMANUS_SESSION_SUMMARY_ENABLED` | `true` | 会话摘要总开关 |
| `EXCELMANUS_SESSION_SUMMARY_MIN_TURNS` | `3` | 最少轮次才生成摘要 |
| `EXCELMANUS_SESSION_SUMMARY_INJECT_TOP_K` | `3` | 注入历史会话数量 |
| `EXCELMANUS_SESSION_SUMMARY_MAX_TOKENS` | `800` | 注入 token 上限 |

### 数据库迁移

启动时自动执行 migration 20（新增 `session_summaries` 表），无需手动操作。

### 注意事项

- 会话历史感知需要 `EXCELMANUS_EMBEDDING_ENABLED=true`，未启用时降级为时间序兜底检索
- 多渠道 Bot 需要配置对应渠道凭证，通过 Web UI 设置页或环境变量配置
- Claude prompt cache 优化对所有 Claude 用户自动生效，无需额外配置
