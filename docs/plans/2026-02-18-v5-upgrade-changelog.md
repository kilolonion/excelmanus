# v5 架构升级变更记录

> 日期：2026-02-18
> 状态：Phase 1 已完成，全量回归 1448 passed / 0 failed

## 概述

v5 将 ExcelManus 的 Skill/Tool 架构从"Skill 控制工具授权"重构为三层正交设计：

| 层 | 职责 | 变更前 | 变更后 |
|---|---|---|---|
| **Tool Presentation** | 控制 LLM 看到的 schema 详细度 | 无（全量或 scope 限制） | ToolProfile: core 完整 / extended 摘要 |
| **Skill** | 知识注入（最佳实践指引） | 控制 allowed_tools + 触发路由 | 纯知识注入，不控制工具可见性 |
| **Tool Policy** | 安全拦截（审批/审计） | 与 Skill 耦合 | 独立，不变 |

## 新增文件

### `excelmanus/tools/profile.py`

ToolProfile 层核心定义：

- **`CORE_TOOLS`**: 始终展示完整 OpenAI tool schema 的工具集（数据读取、结构发现、元工具）
- **`EXTENDED_CATEGORIES`**: 7 个扩展类别（`data_write`, `format`, `advanced_format`, `chart`, `sheet`, `code`, `file_ops`）
- **`TOOL_PROFILES`**: 全量工具的 tier + category 映射
- 辅助函数：`get_tier()`, `get_category()`, `get_tools_in_category()`

## 新增方法

### `ToolDef.to_summary_schema()`（registry.py）

为 extended 工具生成摘要 schema：仅 name + description，无参数细节。LLM 能看到工具存在但不知道如何调用，需先 `expand_tools` 获取完整 schema。

### `ToolRegistry.get_tiered_schemas()`（registry.py）

按 ToolProfile 生成分层 tool schemas：
- core → 完整 schema
- extended 且未展开 → 摘要 schema
- extended 且已展开 → 完整 schema
- 不在 profile 中（如 MCP 动态注册）→ 完整 schema

### `AgentEngine._build_v5_tools()`（engine.py）

替代旧的 `_build_tools_for_scope(tool_scope)`。不再按 scope 过滤，而是：
1. 调用 `registry.get_tiered_schemas(expanded_categories)` 生成分层 domain schemas
2. 调用 `_build_meta_tools()` 生成元工具 schemas
3. 合并返回（meta 优先，去重）

### `AgentEngine._handle_expand_tools(category)`（engine.py）

处理 `expand_tools` 元工具调用：将指定类别加入 `_expanded_categories`，后续 `_build_v5_tools()` 会为该类别返回完整 schema。

## 新增元工具

### `activate_skill`

替代旧的 `select_skill`。参数：`skill_name`（enum 枚举所有可用技能）。功能：纯知识注入，激活技能后将其 `SKILL.md` 内容注入对话上下文。

### `expand_tools`

替代旧的 `discover_tools` + `list_skills`。参数：`category`（enum 枚举 7 个扩展类别）。功能：将指定类别的工具从摘要 schema 升级为完整 schema，LLM 获取参数信息后即可调用。

## 删除的文件

| 文件 | 行数 | 说明 |
|---|---|---|
| `excelmanus/skillpacks/pre_router.py` | ~493 | 小模型预路由（adaptive 模式） |
| `excelmanus/tools/skill_tools.py` | ~80 | `list_skills` 工具定义 |
| `tests/test_pre_router.py` | — | 预路由测试 |
| `tests/test_skill_tools.py` | — | list_skills 测试 |

## 删除的方法（engine.py，约 800 行）

| 方法 | 说明 |
|---|---|
| `_get_current_tool_scope()` | 根据 active skills + route 计算当前工具范围 |
| `_build_tools_for_scope()` | 按 scope 组合常规工具和元工具 |
| `_active_skills_tool_union()` | 所有激活 skill 的 allowed_tools 并集 |
| `_build_tool_to_skill_index()` | tool → skill 反向索引 |
| `_try_auto_supplement_tool()` | 自动补充：LLM 调用未授权工具时自动激活 skillpack |
| `_resolve_preroute_target_layered()` | 解析预路由候选（分层加载） |
| `_activate_preroute_candidates()` | 根据预路由候选激活主/副技能 |
| `_apply_preroute_fallback()` | 预路由失败回退 general_excel |
| `_refresh_route_after_skill_switch()` | select_skill 后同步刷新 route 状态 |
| `_handle_discover_tools()` | 旧 discover_tools 元工具处理 |
| `_is_skill_context_text()` | 判断 context 是否为 skill 注入文本 |
| `_expand_tool_scope_patterns()` | 展开 MCP 选择器模式 |
| `_ensure_always_available()` | 确保任务管理工具在 scope 中 |
| `_append_global_mcp_tools()` | 追加全局 MCP 工具到 scope |
| `_apply_window_mode_tool_filter()` | 窗口模式工具过滤 |
| `_merge_with_loaded_skills()` | 合并历史已加载 skill |
| `AutoSupplementResult` dataclass | 自动补充结果 |

## 删除的配置项（config.py）

| 配置项 | 说明 |
|---|---|
| `auto_activate_default_skill` | 非斜杠路由时自动激活 general_excel |
| `skill_preroute_mode` | 小模型预路由模式（adaptive） |
| `skill_preroute_api_key` | 预路由 API Key |
| `skill_preroute_base_url` | 预路由 Base URL |
| `skill_preroute_model` | 预路由模型 |
| `skill_preroute_timeout_ms` | 预路由超时 |
| `auto_supplement_enabled` | 工具自动补充开关 |
| `auto_supplement_max_per_turn` | 每轮自动补充上限 |

## 删除的策略常量（policy.py）

| 常量 | 说明 |
|---|---|
| `DISCOVERY_TOOLS` | 基础发现工具集（无 skill 时的默认 scope） |
| `FALLBACK_DISCOVERY_TOOLS` | fallback 兼容别名 |

## 修改的文件

### `engine.py`（核心变更）

- **chat()**: 移除 PreRouter 并行调用，简化为直接路由
- **_tool_calling_loop()**: 用 `_build_v5_tools()` 替代 `_build_tools_for_scope(tool_scope)`，不再使用 `tool_scope` 限制
- **_execute_tool_call()**: dispatch 分支替换 `select_skill`/`discover_tools` → `activate_skill`/`expand_tools`，移除 auto_supplement scope 检查
- **_build_meta_tools()**: 完全重写为 v5 版本（activate_skill + expand_tools + finish_task + delegate_to_subagent + list_subagents + ask_user）
- **_build_tool_index_notice()**: 重写为按 core/extended 分类展示
- **__init__**: 新增 `_expanded_categories: set[str]`，移除 `_tool_to_skill_index_cache`、`_turn_supplement_count`、`_auto_supplement_notice`
- **guard 提示**: `select_skill` → `expand_tools`

### `approval.py`

- `create_pending()`: `tool_scope` 参数改为可选（`Sequence[str] | None = None`）
- `execute_and_audit` 内部 `AppliedApprovalRecord` 构造：`tool_scope` 兼容 None

### `skillpacks/router.py`

- 移除 `DISCOVERY_TOOLS` / `FALLBACK_DISCOVERY_TOOLS` 导入
- `_classify_write_hint` timeout 硬编码为 10s（不再依赖已删除的 `skill_preroute_timeout_ms`）

### `skillpacks/manager.py`

- 移除 `invalidate_pre_route_cache` 导入和调用

## 测试适配

- 删除 ~15 个废弃测试类 + ~8 个废弃方法（~1800 行）
- 修复 `TestMetaToolDefinitions`：`select_skill` → `activate_skill`
- 修复 `TestWriteGuardPrompt`：guard 文本匹配 `expand_tools`
- 修复 `TestFinishTaskInjection`：`_build_v5_tools()` 替代 `_get_current_tool_scope` + `_build_tools_for_scope`
- 修复 `TestSkillCatalogIntegrity`：PBT 元工具 catalog 断言
- 删除 `TestToolScopeTransitions`：tool_scope 状态转换概念已移除
- 修复 `TestApprovalFlow`：`tool_scope=None` 兼容

## 后续工作

| 任务 | 优先级 | 说明 |
|---|---|---|
| SKILL.md 格式迁移 | Medium | 移除 `allowed_tools`、`triggers`、`priority` frontmatter 字段 |
| SkillMatchResult 简化 | Medium | `tool_scope` 字段可选化或移除 |
| general_excel skillpack 删除 | Low | 不再需要 fallback 默认技能 |
| 窗口感知顾问独立 mock | Low | 解决 advisor 共享 _client 的测试隔离问题 |
