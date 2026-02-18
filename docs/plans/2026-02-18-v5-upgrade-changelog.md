# v5 架构升级变更记录

> 日期：2026-02-18
> 状态：**Phase 1–4 全部完成（v5 升级终结）**，全量回归 1441 passed / 0 failed

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

---

## Phase 2: 废弃字段清理 + 全链路术语对齐

> 里程碑 commit: `b9b1c27`

### WI-1: SKILL.md 格式迁移

- 从 6 个系统 SKILL.md（data_basic / chart_basic / format_basic / excel_code_runner / file_ops / sheet_ops）中移除 `allowed_tools`、`triggers`、`priority` frontmatter 字段（-172 行）
- 删除 `general_excel/` 整个 skillpack 目录（无代码引用、`user_invocable: false`）
- 更新 `README.md`：移除预路由行为描述，更新系统 skillpack 列表

### WI-2: Model / Loader / Router 瘦身

| 文件 | 改动 |
|---|---|
| `skillpacks/router.py` | `_build_result()` 不再从 `skill.allowed_tools` 构建 `tool_scope`（始终返回空列表）；更新 `_build_fallback_result` docstring |
| `skillpacks/loader.py` | 删除 `_validate_allowed_tools_soft()` 和 `_is_allowed_tool_selector()`（-75 行） |
| `engine.py` | `_adapt_guidance_only_slash_route()` 中 `skill.allowed_tools` 判断替换为 `skill.command_dispatch == "tool"` |

### WI-3: tool_scope + 旧术语全链路清理

| 文件 | 改动 |
|---|---|
| `engine.py` | `_execute_tool_call` / `_call_registry_tool` 的 `tool_scope` 参数改为 `Sequence[str] \| None`；`ToolNotAllowedError` 分支兼容 None |
| `engine.py` | `_handle_select_skill` → `_handle_activate_skill`；`_is_select_skill_ok` → `_is_activate_skill_ok` |
| `subagent/executor.py` | `_SUBAGENT_BLOCKED_META_TOOLS` 更新为 `activate_skill` + `expand_tools` |
| `subagent/builtin.py` | full 子代理系统提示：`select_skill` → `activate_skill` |
| `renderer.py` | `_META_TOOL_DISPLAY` 映射更新为 `activate_skill` + `expand_tools`；`_meta_tool_hint` 适配 |
| `memory.py` | 系统提示工具策略段：`select_skill` → `activate_skill` / `expand_tools` |

### Phase 2 测试适配

- `test_pbt_llm_routing.py`：`_handle_select_skill` → `_handle_activate_skill`（3 处）
- `test_engine.py`：MCP 依赖测试适配（2 处）；slash pass-through 测试检查 `call_args_list[0]`
- `test_skillpacks.py`：`tool_scope == ["create_chart"]` → `tool_scope == []`；删除 2 个 `_validate_allowed_tools_soft` 测试
- `test_system_skill_reachability.py`：重写为 v5 兼容断言（不再检查 `allowed_tools` 内容，确认 `general_excel` 不存在）

---

## Phase 3: Bench Tracer 修复 + SkillMatchResult.tool_scope 清理

> 里程碑 commit: `c423a05`

### 修复 bench.py `_EngineTracer` 运行时崩溃

`_EngineTracer` 通过 monkey-patch 拦截 engine 方法记录 trace 数据。Phase 1 删除了 `_get_current_tool_scope`，但 tracer 仍引用该方法，导致 bench 跑测时 `AttributeError` 崩溃。

- 删除 `_traced_scope` 方法及相关 monkey-patch（`__init__` 和 `restore()`）
- 更新 docstring 移除对已删除方法的引用

### `SkillMatchResult.tool_scope` 改为可选默认空列表

- `models.py`：`tool_scope` 从必填字段改为 `field(default_factory=list)`，字段顺序调整到 `route_mode` 之后
- 全代码库 22 处 `tool_scope=[]` 冗余传参批量清理（engine.py ×5、router.py ×2、bench.py ×2、test_engine.py ×7、test_write_guard.py ×4、test_bench.py ×1、test_cli.py ×1）

### `tool_scope` 残留位置（保留不动）

| 位置 | 原因 |
|---|---|
| `api.py` ChatResponse | 公共 API 向后兼容，始终返回 `[]` |
| `events.py` ToolCallEvent | 可观测性 |
| `bench.py` TurnResult/CaseResult | bench 指标记录 |
| `subagent/executor.py` + `tool_filter.py` | 子代理运行期工具隔离（不同概念） |
| `approval.py` PendingApproval/AppliedApprovalRecord | 审计记录（已兼容 None） |
| `cli.py` `/status` 显示 | 显示工具数量（v5 中始终为 0） |

---

## Phase 4: Skillpack Model 字段移除 + 全链路破坏性清理（终结）

> 里程碑 commit: `2933421` | **-454 行**

### Skillpack dataclass 字段移除

从 `models.py` 的 `Skillpack` 中删除 `allowed_tools: list[str]`、`triggers: list[str]`、`priority: int` 三个字段。

### 全链路适配

| 文件 | 改动 |
|---|---|
| `skillpacks/loader.py` | 停止解析 `allowed_tools`/`triggers`/`priority`；移除 `_CANONICAL_FIELD_ALIASES` 中对应别名 |
| `skillpacks/manager.py` | `_SUPPORTED_FIELDS`/`_FIELD_ALIASES`/`_DEFAULTS` 中移除；`_base_payload`/`_to_frontmatter_dict`/`_normalize_patch_payload`/`_skill_to_detail` 中移除 |
| `skillpacks/context_builder.py` | 排序由 `(-priority, name)` 改为 `name`（按名称字母序） |
| `tools/policy.py` | `READ_ONLY_SAFE_TOOLS` 移除 `list_skills` |
| `api.py` | `SkillpackDetailResponse` 移除 `allowed_tools`/`triggers`/`priority` 字段；`_to_skill_detail` 构造移除 |
| `cli.py` | `_to_standard_skill_detail` 移除 `allowed_tools` 别名 |
| `engine.py` | dispatch 分支移除 `select_skill` 兼容（仅保留 `activate_skill`）；清理注释 |
| `scripts/migrate_skills_to_standard.py` | `_build_frontmatter` 移除 `allowed_tools`/`triggers`/`priority` |

### Phase 4 测试适配

- 删除 3 个废弃测试（`test_create_skillpack_allows_empty_triggers`/`_allowed_tools`/`_patch_allows_empty_triggers`）
- 删除 2 个废弃测试（`test_triggers_allows_empty_list`/`test_allowed_tools_empty_list_is_allowed`）
- 重写 `test_sorted_by_priority` → `test_sorted_by_name`
- 批量移除 11 个测试文件中的 `allowed_tools=`/`triggers=`/`priority=` 参数传递（~100 处）

---

## Phase 5: 深度清理 + 窗口感知顾问隔离 + v4 残余扫净（终结）

> 里程碑 commits: `64a5149` → `573b5b3`

### 窗口感知顾问独立 client

`engine.py` 中 `_advisor_client` 原来在凭证相同时与 `_client` 共享同一对象，导致测试 mock 互相干扰。改为始终创建独立 client。

### 版本标识 v4→v5

- `excelmanus/__init__.py` — docstring + `__version__ = "5.0.0"`
- `excelmanus/tools/__init__.py` — docstring
- `excelmanus/skillpacks/__init__.py` — docstring
- `excelmanus/config.py` — 移除注释中的 `（v4）` 标记

### 过时注释清理

- `engine.py` — 移除 10 处 `# v5:` 过渡注释前缀（保留描述本体）
- `router.py` — 移除 3 处 v5 过渡注释
- `engine.py` — `ToolNotAllowedError` 错误 JSON 移除 `allowed_tools` 字段

### 测试术语全面对齐

- `test_engine.py` — `select_skill` → `activate_skill`（3 个测试方法重命名 + 内部引用）
- `test_pbt_llm_routing.py` — `select_skill` → `activate_skill`（全文替换）
- `test_write_guard.py` — `select_skill` → `activate_skill`
- `test_bench_validator.py` — `general_excel` → `other_skill`（修复 mismatch 测试逻辑）
- `test_bench_reporter.py` — `general_excel` → `data_basic`
- `test_skillpacks.py` — `general_excel` → `test_fallback`（测试 fixture 重命名）
- `test_tool_policy.py` — 移除 `list_skills`（已从 `READ_ONLY_SAFE_TOOLS` 删除）
- `test_pbt_unauthorized_tool.py` — 错误响应 JSON 必需键移除 `allowed_tools`

### 损坏脚本修复/归档

| 脚本 | 处理 |
|---|---|
| `scripts/dump_token_budget.py` | 修复：`_build_v5_tools()` 替代已删除方法 |
| `scripts/dump_agent_prompt.py` | 修复：同上 |
| `scripts/bench_phase2_ab.py` | 归档至 `scripts/archive/`（引用已删除的 preroute 配置 + 硬编码 API key） |
| `scripts/bench_skill_preroute.py` | 归档至 `scripts/archive/` |

---

## 后续工作

> **v5 架构升级已全部完成，无已知遗留项。**
