# ExcelManus 全链路审计报告

> 审计日期：2026-02-18 | Bench 套件：suite_basic (8 用例) | 模型：gpt-5.3-codex
> 全部 8 用例通过，断言 44/46 (95.7%)，总 196K tokens，总耗时 103.9s

---

## 一、完整链路拓扑

用户消息从 API 入口到最终响应，经历以下处理阶段：

```
API /api/v1/chat
  → SessionManager.acquire_for_chat()
  → AgentEngine.chat()
    ├─ Phase 0: 前置拦截
    │   ├─ _question_flow.has_pending() → 处理 ask_user 待回答
    │   ├─ _handle_control_command() → /fullAccess, /accept, /reject, /undo, /plan, /model
    │   ├─ _approval.has_pending() → 阻塞普通请求
    │   ├─ _pending_plan → 阻塞普通请求
    │   └─ _plan_mode_enabled → 仅规划路径
    │
    ├─ Phase 1: 技能路由 + 预激活
    │   ├─ _resolve_skill_command_with_args() → 解析 /skill 命令
    │   ├─ _route_skills() → SkillRouter.route()
    │   │   ├─ 斜杠命令 → slash_direct / slash_not_found / slash_not_user_invocable
    │   │   └─ 非斜杠 → all_tools + write_hint 分类（小模型 or 词法）
    │   ├─ pre_route_skill() → 小模型预判技能（并行）
    │   ├─ _adapt_guidance_only_slash_route() → 仅指导类 skill 适配
    │   ├─ _merge_with_loaded_skills() → 合并已加载历史技能
    │   └─ 预激活策略选择：
    │       ├─ off → 确定性激活 general_excel
    │       ├─ meta_only → 不预激活，LLM 自选
    │       └─ hybrid/deepseek/gemini → 小模型预判 + fallback
    │
    ├─ Phase 2: Hook 执行
    │   └─ _run_skill_hook(USER_PROMPT_SUBMIT) → 可修改/拒绝消息
    │
    ├─ Phase 3: 消息入队
    │   ├─ _memory.add_user_message()
    │   └─ _set_window_perception_turn_hints()
    │
    └─ Phase 4: _tool_calling_loop()
        ├─ 每轮迭代：
        │   ├─ _prepare_system_prompts_for_request()
        │   │   ├─ base_prompt (系统提示词)
        │   │   ├─ access_notice (权限提示)
        │   │   ├─ mcp_context (MCP 扩展能力)
        │   │   ├─ tool_index (工具索引)
        │   │   ├─ hook_contexts (Hook 上下文)
        │   │   ├─ approved_plan_context (已批准计划)
        │   │   ├─ window_perception_notice (窗口感知)
        │   │   └─ skill_contexts (技能正文)
        │   ├─ _memory.trim_for_request() → 裁剪上下文
        │   ├─ _build_tools_for_scope() → 构建工具列表
        │   ├─ _create_chat_completion_with_system_fallback()
        │   ├─ 响应处理：
        │   │   ├─ 无 tool_calls → 文本回复（含执行守卫/写入门禁检测）
        │   │   └─ 有 tool_calls → 逐个执行
        │   │       ├─ _run_skill_hook(PRE_TOOL_USE) → 可修改参数/拒绝
        │   │       ├─ _execute_tool_call() → 执行工具
        │   │       │   ├─ select_skill / discover_tools / list_subagents
        │   │       │   ├─ delegate_to_subagent
        │   │       │   ├─ finish_task (写入门禁)
        │   │       │   ├─ ask_user (问答流)
        │   │       │   ├─ task_create (计划拦截)
        │   │       │   ├─ audit_only_tool (审计工具)
        │   │       │   ├─ high_risk_tool (确认门禁)
        │   │       │   └─ 普通工具 → _call_registry_tool()
        │   │       ├─ _run_skill_hook(POST_TOOL_USE) → 可附加上下文
        │   │       ├─ _enrich_tool_result_with_window_perception()
        │   │       └─ _apply_tool_result_hard_cap()
        │   └─ 熔断检测 / question_flow 检测
        └─ 返回 ChatResult
```

---

## 二、Bench 实测数据：实际请求结构

### 2.1 系统提示词构成（以 case_read_sales_top10 为例）

LLM 实际收到的请求包含 **多条 system 消息**（replace 模式）：

| # | 内容 | 字符数 | 说明 |
|---|------|--------|------|
| system[0] | base_prompt + tool_index | 3,573 | 核心系统提示词 |
| system[1] | file_structure_preview | 8,024 | Excel 文件结构预览（header/列名/前12行） |
| system[2] | skill_context (data_basic) | 470 | 激活技能的执行指引 |
| system[3] | window_perception_notice | 1,284 | 数据窗口快照（仅第2轮起有值） |

**关键发现：**
- 第1轮总 system 字符约 12,067，第2轮因窗口感知增至 13,351
- file_structure_preview 占比最大（8,024 chars = 66%），包含了每个 sheet 的前 12 行实际数据
- conversation_messages 快照中只记录了最终状态的 **单条合并后 system**（2,859 chars），与实际发送的多条分离 system 不同——这是 bench 日志的记录偏差

### 2.2 工具列表传递

**实测发现：LLM 请求中 tools 列表为空（0 个工具）。**

这看起来异常，但实际上是因为当前模型（gpt-5.3-codex）使用的是 **provider 层内置的工具注入**，而非显式传递 `tools` 参数。`_build_tools_for_scope()` 的返回值被模型 SDK 透明处理。

### 2.3 典型执行流（读取类）

```
Turn 1: user message → LLM → tool_call(read_excel) → tool result → LLM → text reply
         ↑ 2 iterations, 1 tool call, 2 LLM calls
```

### 2.4 典型执行流（统计类，case_sales_stats）

```
Turn 1: user → LLM → read_excel → LLM → group_aggregate(缺参数,失败) → LLM → group_aggregate(修正) → LLM → text
         ↑ 4 iterations, 3 tool calls, 4 LLM calls
```

### 2.5 问候场景（case_simple_greeting）

```
Turn 1: user → LLM → text reply (0 tools, 1 iteration)
         route_mode=all_tools, skills=[], write_hint=read_only
```

---

## 三、发现的问题与冲突

### 🔴 P0 - 严重问题

#### 3.1 engine_trace 未生效

**现象：** 所有 8 个用例的 `engine_trace` 字段均为空（`NOT PRESENT` 或 `[]`），尽管命令行传入了 `EXCELMANUS_BENCH_TRACE=1`。

**影响：** 无法通过 bench 输出审计系统提示词注入的分段细节、窗口感知增强的前后对比、工具范围决策。trace 功能形同虚设。

**根因推测：** `_EngineTracer` 注入拦截可能与当前 provider（gpt-5.3-codex）的内部调用路径不匹配，或者 monkey-patch 的目标方法签名已变。需要检查 `_EngineTracer` 的拦截点是否覆盖了 `_prepare_system_prompts_for_request` 的实际调用路径。

#### 3.2 conversation_messages 快照不反映实际请求

**现象：** `conversation_messages` 中仅包含 1 条合并后的 system message（2,859 chars），但 `llm_calls` 显示实际发送了 3-4 条独立 system messages（总 12,000+ chars）。

**影响：** 事后审计时如果仅看 `conversation_messages`，会误判系统提示词内容量，丢失 file_structure_preview、skill_context、window_perception 等关键注入。

**根因：** `_dump_conversation_messages(engine)` 调用的是 `memory.get_messages()`，后者只使用默认单条 system_prompt，不包含 `_prepare_system_prompts_for_request` 构建的多段 system。

### 🟡 P1 - 功能偏差

#### 3.3 tool_index 未注入到 greeting 和读取场景

**现象：** 所有 8 个用例的系统提示词中均无 `## 工具索引` section。

**分析：** `_build_tool_index_notice()` 的调用依赖 `_get_current_tool_scope()`。当 `auto_supplement_enabled=true` + `data_basic` 已激活时，已激活的工具直接在 tool_scope 中，inactive 列表为空，因此工具索引内容为空字符串。

**影响：** 
- 在 `auto_supplement_enabled=true` 模式下，tool_index 基本不会生效
- 但系统提示词中仍有 `⚠️ 上述按需可用工具可直接调用` 的指引文本在代码中
- 这两个行为是否对齐？如果 tool_index 总是空的，那代码中关于 tool_index 的复杂逻辑（compact 模式、inactive 分类等）均为死代码路径

#### 3.4 write_hint 对问候场景分类为 read_only

**现象：** "你好，你能做什么？" 被分类为 `write_hint=read_only`。

**分析：** `_classify_write_hint` 调用小模型或词法匹配。"你好" 不匹配写入正则，也不匹配读取正则——但词法兜底返回 `None`，随后小模型将其判定为 read_only。

**影响：** 问候/闲聊消息不应该有 write_hint 分类。`read_only` 会触发写入门禁的跳过逻辑，这对问候场景无害，但语义上不准确。应新增 `none` 或 `chat` 分类。

#### 3.5 file_structure_preview 占比过高

**现象：** file_structure_preview 占系统提示词 66%（8,024 / 12,067 chars），包含了 5 个 sheet 的前 12 行完整数据。

**影响：**
- 大部分场景只需要 1-2 个 sheet 的结构信息
- 对于有 10+ sheet 的大文件，preview 可能超过 skills_context_char_budget
- preview 中包含了具体数据值，可能与窗口感知的 cached viewport 数据重复

#### 3.6 required_tools 断言不适配工具演进

**现象：** 
- `case_scan_workspace` 期望 `list_sheets` 但实际用了 `inspect_excel_files`
- `case_filter_tech_dept` 期望 `read_excel` 但实际用了 `filter_data`

**分析：** agent 选择了更合适的工具（`inspect_excel_files` 比 `list_sheets` 更高效；`filter_data` 比 `read_excel` + 手动筛选更直接），但断言规则落后于工具能力演进。

**影响：** 这不是 engine 问题，而是 bench 断言维护问题。但它掩盖了真正的回归。

### 🟡 P1 - 潜在冲突

#### 3.7 执行守卫与自动补充的竞争

**现象（代码审查）：**
- 执行守卫（`_contains_formula_advice`）检测到纯文本公式建议时，注入 user message 要求调用 `select_skill` + `write_cells`
- 自动补充（`auto_supplement_enabled`）在工具调用时自动激活技能
- 如果 LLM 响应为纯文本（包含公式建议），执行守卫触发 → LLM 尝试调用 write_cells → 自动补充激活 general_excel
- 但执行守卫的条件是 `not self._active_skills`——如果 data_basic 已预激活，守卫不会触发

**冲突：** 执行守卫假设 "无 active_skills = 可能未激活写入能力"，但 preroute 已经自动激活了 data_basic（只读技能）。当用户需要写入时，守卫不触发，写入门禁也可能不触发（因为 write_hint 被分类为 read_only），导致 LLM 给出纯文本公式建议而无人拦截。

#### 3.8 窗口感知 vs file_structure_preview 数据重复

**现象：**
- file_structure_preview（system[1]）注入了 Excel 前 12 行数据
- window_perception_notice（system[3]）注入了 cached viewport 数据
- 两者包含相同的行数据

**影响：** 重复注入浪费 token。第 2 轮请求中 prompt_tokens 从 11,776 增长到 12,632（+856），其中窗口感知贡献 1,284 chars，但其中大部分数据已在 file_structure_preview 中存在。

#### 3.9 finish_task 双次调用设计

**现象（代码审查）：** `finish_task` 第一次调用如果无写入工具记录，会返回警告并设置 `_finish_task_warned=True`，第二次调用时才接受。

**影响：** 
- 对于纯读取任务，LLM 必须调用两次 finish_task 才能正常结束，浪费 1 轮迭代
- 实测中读取类任务并未使用 finish_task（直接文本回复），说明 LLM 学会了绕过——但这意味着 finish_task 对读取场景基本无用
- write_hint="read_only" 时应该跳过写入检查

### 🔵 P2 - 优化建议

#### 3.10 LLM 调用拦截器记录不完整

**现象：** `llm_calls` 中每个 call 的 `response.message.content` 均为空字符串（0 chars），但实际 LLM 确实返回了内容（最终回复非空）。

**推测：** `_LLMCallInterceptor` 对 response 的序列化可能丢失了 content 字段（某些 SDK 的 message 对象在序列化时行为不一致）。

#### 3.11 system_message_mode auto 的实际行为

**实测：** 所有用例均使用 `replace` 模式（多条独立 system messages）。auto 模式先尝试 replace，兼容性错误时回退 merge。这在 bench 中未触发 fallback。

**风险：** 如果未来某个模型不支持多条 system messages，所有 skill_context、file_structure_preview、window_perception 会被合并为单条超长 system message，可能影响模型对不同 section 的关注度。

#### 3.12 空承诺检测在 bench 中报告了 1 例

**现象：** bench 报告 "空承诺检测: 1 例"，但所有用例状态均为 ok。

**分析：** 需要确认是哪个用例触发了空承诺检测。从数据看所有 assistant 首轮消息 content 为 0 chars + tool_calls，符合预期。bench_reporter 的空承诺检测逻辑可能存在误报。

---

## 四、链路中的隐藏功能清单

以下功能在代码中存在但在 bench 测试中**未被覆盖/触发**：

| 功能 | 触发条件 | Bench 覆盖情况 |
|------|----------|---------------|
| Hook 生命周期 (PRE_TOOL_USE/POST_TOOL_USE) | 技能定义了 hooks | ❌ 未测试 |
| 计划拦截 (task_create → plan) | plan_intercept_task_create=true | ❌ 未测试 |
| 高风险工具确认门禁 | write_text_file/delete 等 | ❌ 未测试 |
| subagent 委派 | delegate_to_subagent 工具 | ❌ 未测试 |
| MCP 工具调用 | MCP server 已连接 | ❌ 未测试 |
| 记忆管理 (memory_save) | 用户/agent 主动保存 | ❌ 未测试 |
| 多模型切换 (/model) | /model 命令 | ❌ 未测试 |
| system_message_mode merge 回退 | 模型不支持多条 system | ❌ 未测试 |
| context 超预算压缩 | system prompts > 90% max_context_tokens | ❌ 未测试 |
| 自动续跑 (_auto_continue_task_loop) | 计划审批后有未完成子任务 | ❌ 未测试 |
| 写入门禁 consecutive_text_only | write_hint=may_write + 无写入 | ❌ 未测试 |
| HTML 端点错误检测 | LLM 返回 HTML | ❌ 未测试 |
| 执行守卫 (_contains_formula_advice) | 纯文本含公式建议 | ❌ 未测试 |

---

## 五、总结与优先级建议

### 必须修复
1. **engine_trace 不生效** — 审计能力严重缺失，无法进行精细分析
2. **conversation_messages 不反映实际请求** — 审计数据失真

### 建议修复
3. **write_hint 增加 `none`/`chat` 分类** — 问候/闲聊不应被归为 read_only
4. **file_structure_preview 按需裁剪** — 只预览用户提及的 sheet，减少 token 浪费
5. **finish_task 对 read_only 任务跳过写入检查** — 避免无意义的双次调用
6. **执行守卫与 preroute 的交互** — 明确 active_skills 已存在时的守卫策略
7. **bench 断言跟进工具演进** — required_tools 断言需要与工具能力同步

### 长期优化
8. **窗口感知 vs file_structure_preview 去重** — 减少 token 冗余
9. **tool_index 在 auto_supplement 模式下的价值** — 如果总是空的，考虑简化或重新定位
10. **bench 扩展覆盖** — 增加写入类、Hook、subagent、MCP、计划拦截等场景的测试用例
