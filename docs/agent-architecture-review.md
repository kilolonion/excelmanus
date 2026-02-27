# ExcelManus Agent 架构审查报告

> 基于行业权威范式对比分析，识别不足并提出改进建议

---

## 一、调研来源

| 来源 | 核心观点 |
|------|----------|
| **Anthropic** — Building Effective Agents | 工作流 vs 自主 Agent 的选择框架；Augmented LLM 三要素（retrieval, tools, memory）；工具设计 Poka-yoke 原则 |
| **OpenAI** — A Practical Guide to Building Agents | Agent = model + tools + instructions + guardrails；单 Agent 优先原则；结构化输出验证；防护栏分层（输入/输出/工具级） |
| **Microsoft Azure** — AI Agent Design Patterns | 10 种设计模式（routing, parallel, orchestrator-worker, evaluator-optimizer 等）；可观测性与追踪的系统化方案 |
| **LlamaIndex** — Optimal Design Patterns for Agents | 事件驱动 Workflow 架构；平衡自主性与可控性；结构化错误处理与人类监督 |
| **12-Factor Agents** (Dex Horthy) | 12 条工程原则：拥有提示词、拥有上下文窗口、工具即结构化输出、状态统一、无状态 reducer、紧凑错误、小而专注 |
| **OpenTelemetry** — AI Agent Observability | Agent 可观测性三支柱（metrics/traces/logs）；GenAI 语义约定标准化 |

---

## 二、ExcelManus 架构现状概览

### 2.1 核心架构

```
用户消息 → chat() 路由层 → _route_skills() → _tool_calling_loop()
                                                    ↓
                                        LLM 调用 → 工具派发 → 结果收集
                                                    ↓
                                        迭代循环（最多 max_iterations 轮）
                                                    ↓
                                              返回最终回复
```

### 2.2 已有优势（行业对标）

| 维度 | ExcelManus 现状 | 对标来源 |
|------|-----------------|----------|
| **模块化提示词** | PromptComposer：YAML frontmatter + .md 文件，按条件匹配、按优先级裁剪 | ✅ 12-Factor #2: Own your prompts |
| **上下文窗口管理** | token 计数 + 阈值压缩 + 自动 compaction + summarize_and_trim | ✅ 12-Factor #3: Own your context window |
| **技能路由** | SkillRouter 基于用户意图匹配 skillpack，动态注入 system context | ✅ Anthropic: Routing pattern |
| **人类参与** | ApprovalManager 审批门禁 + ask_user 工具 + question_flow | ✅ 12-Factor #7: Contact humans with tools |
| **熔断机制** | 连续失败熔断 (circuit breaker) + 迭代上限截断 | ✅ OpenAI: Guardrails |
| **卡住检测** | SessionState 滑动窗口检测重复调用 + 只读循环 | ✅ 独创（OpenHands 灵感） |
| **元认知反思** | meta_cognition_notice 在退化条件下注入策略调整提示 | ✅ Metacognition is All You Need |
| **Hook 系统** | pre/post tool use hooks (ALLOW/DENY/ASK) | ✅ OpenAI: Tool-level guardrails |
| **备份/事务** | workspace transaction + CoW + FileRegistry 版本追踪 | ✅ 安全网设计 |
| **子代理** | SubagentOrchestrator 支持委派和并行委派 | ✅ Microsoft: Orchestrator-Worker |

---

## 三、不足分析与改进建议

### 3.1 🔴 可观测性缺失（Observability）

**现状**：仅有 Python logging（`get_logger`），无结构化追踪、无指标采集、无 trace 关联。

**行业标准**（OpenTelemetry GenAI SIG / Microsoft / OpenAI）：
- Agent 是非确定性系统，仅靠日志无法有效诊断「为什么 Agent 做了这个决定」
- 需要 **分布式追踪**（每次 chat → span，每次工具调用 → child span，LLM 调用 → child span）
- 需要 **结构化指标**（token 用量、延迟、工具成功率、熔断触发率、每会话迭代分布）

**改进建议**：

```
优先级: P0（基础设施）

1. 引入 OpenTelemetry SDK
   - chat() 创建 root span（含 session_id, user_id, model）
   - _tool_calling_loop 每轮迭代创建 child span
   - execute() 每次工具调用创建 child span（含 tool_name, duration, success）
   - LLM API 调用创建 child span（含 model, tokens, latency）

2. 结构化指标（Prometheus / OTLP）
   - agent_chat_duration_seconds (histogram)
   - agent_tool_calls_total (counter, labels: tool_name, success)
   - agent_llm_tokens_total (counter, labels: model, type=prompt/completion)
   - agent_iterations_per_chat (histogram)
   - agent_circuit_breaker_triggered_total (counter)

3. SSE 事件已有 CHAT_SUMMARY 统计（iterations/tokens/elapsed），
   但未持久化、未关联 trace、无法做历史分析。
   建议：将 CHAT_SUMMARY 数据写入 LLMCallStore 并关联 trace_id。
```

---

### 3.2 🔴 Engine 巨型类问题（God Object）

**现状**：`engine.py` 超过 **7028 行**，承担了路由、循环控制、工具派发、审批、会话管理、备份、记忆、提示词组装等几乎所有职责。虽然已拆分出 `ContextBuilder`、`SessionState`、`ToolDispatcher`、`CommandHandler` 等组件，但它们仍通过 `self._engine` 反向引用 AgentEngine 的大量内部方法，耦合度极高。

**行业标准**（12-Factor #8: Own your control flow / #12: Stateless reducer / LlamaIndex Workflows）：
- Agent 的控制流应该是**显式的、可审计的**
- 理想架构：Agent 是一个 **无状态 reducer** — `(state, event) → (new_state, effects)`
- LlamaIndex 提倡事件驱动 Workflow，每个步骤是独立的、可测试的

**改进建议**：

```
优先级: P1（架构重构，可分阶段）

1. 定义显式 AgentState 数据类
   将散落在 engine 各处的状态（_last_route_result, _current_write_hint,
   _active_skills, _pending_plan 等）收敛到一个不可变 AgentState dataclass。
   每次迭代产生新 state 而非原地修改。

2. 提取 AgentLoop 编排器
   将 _tool_calling_loop 从 engine.py 中提取为独立类：
   - 输入：AgentState + user_message
   - 输出：AgentState + ChatResult
   - 副作用通过 effect handler 注入（LLM 调用、工具执行、事件发射）
   这使控制流可单元测试，不依赖完整 engine 实例。

3. Protocol 解耦
   当前 ToolDispatcher 等组件通过 ToolExecutionContext protocol 与
   engine 交互，但 protocol 接口过于宽泛（几乎暴露了 engine 全部方法）。
   应收窄 protocol 到最小必要接口。
```

---

### 3.3 🔴 输出验证不足（Output Guardrails）

**现状**：
- 有 `output_guard.py`（执行守卫：检测 Agent 是否只给建议不执行）
- 有 `_run_finish_verifier_advisory`（任务完成验证器，仅建议性）
- **无结构化输出验证**（LLM 返回的工具参数无 schema 级强校验）
- **无输出内容安全过滤**（无 PII 检测、无幻觉检测）

**行业标准**（OpenAI Guide）：
> "Guardrails are a critical component of any agent deployment... Input guardrails validate what goes in, output guardrails validate what comes out."
- 输出防护栏应包括：**内容合规检查、结构化输出验证、置信度阈值、幻觉检测**
- 工具参数应通过 **JSON Schema 强校验** + **Pydantic 模型**验证

**改进建议**：

```
优先级: P0

1. 工具参数强校验
   当前 parse_arguments 仅做 JSON 解析，未做 schema 验证。
   建议：在 ToolRegistry 注册时附带 JSON Schema，
   execute() 时用 jsonschema.validate() 强校验参数。
   无效参数直接返回结构化错误，不执行工具。

2. 输出内容防护栏
   - 在最终 reply 返回前增加 output_guardrail 管道
   - 检查项：PII 泄漏、敏感数据暴露、格式合规
   - 可选：轻量 LLM 调用做幻觉/偏离检测（OpenAI 推荐模式）

3. 强化 finish_verifier
   当前 verifier 是 advisory（仅建议），Agent 可忽略。
   建议：对关键任务（write_hint=may_write）改为 mandatory，
   验证失败则触发重试而非直接返回。
```

---

### 3.4 🟡 状态持久化与恢复（State Persistence）

**现状**：
- `SessionState` 是纯内存对象，进程重启后丢失
- `ConversationMemory` 依赖 `conversation_persistence.py` 序列化/反序列化
- 执行中断后无法从断点恢复（无 checkpoint/resume 机制）

**行业标准**（12-Factor #5/#6/#12）：
- **Factor 5**: 统一执行状态和业务状态 — Agent 的执行进度应持久化到数据库
- **Factor 6**: Launch/Pause/Resume — Agent 应支持暂停后精确恢复
- **Factor 12**: Stateless reducer — 所有状态存储在外部，Agent 进程本身无状态

**改进建议**：

```
优先级: P1

1. Turn-level Checkpoint
   当前已有 FileRegistry 的 turn_checkpoint（文件级快照），
   但缺少 agent state 级别的 checkpoint。
   建议：每轮迭代结束后将 AgentState 序列化到 DB。
   恢复时从最新 checkpoint 重建 state，而非重放全部历史。

2. Task-level Resume
   当前 _auto_continue_task_loop 只在内存中续跑。
   若进程崩溃，TaskList 进度丢失。
   建议：TaskStore 的状态变更实时同步到 DB。

3. 长时间运行的 Agent 任务
   对于多步骤计划执行，应支持：
   - 用户关闭浏览器后任务继续执行
   - 执行进度可查询
   - 异常时自动重试或暂停等待人工干预
```

---

### 3.5 🟡 错误处理策略粗放（Error Compaction）

**现状**：
- 工具失败时将原始错误字符串塞入 tool result 返回给 LLM
- 熔断机制（连续 N 次失败终止）存在但阈值固定
- 无错误分类（可重试 vs 不可重试 vs 需人工介入）

**行业标准**（12-Factor #9: Compact Errors / Anthropic / OpenAI）：
- 错误应**分类压缩**后注入上下文窗口，避免冗长堆栈污染有限的 context
- 区分 **可重试错误**（网络超时、rate limit）、**不可重试错误**（文件不存在、权限拒绝）、**需人工错误**（数据歧义）
- 自动重试应带指数退避

**改进建议**：

```
优先级: P1

1. 错误分类枚举
   class ToolErrorKind(Enum):
       RETRYABLE = "retryable"        # 网络/超时/rate_limit
       PERMANENT = "permanent"        # 文件不存在/参数无效
       NEEDS_HUMAN = "needs_human"    # 数据歧义/权限不足
       CONTEXT_OVERFLOW = "overflow"  # 结果过大

2. 自动重试（带退避）
   RETRYABLE 错误自动重试 2-3 次（指数退避），不消耗 Agent 迭代次数。
   当前所有失败都等价地消耗迭代预算，浪费在瞬态错误上。

3. 错误压缩
   向 LLM 返回的错误信息应结构化：
   {kind: "permanent", summary: "文件不存在", suggestion: "请检查路径"}
   而非原始 Python traceback。
```

---

### 3.6 🟡 工具设计缺少语义分层（Tool Design）

**现状**：
- 工具按功能分类（data_read, sheet, file, code, macro, vision）
- `TOOL_CATEGORIES` + `TOOL_SHORT_DESCRIPTIONS` 提供索引
- 无工具间依赖声明、无工具组合建议、无输入/输出类型约束

**行业标准**（Anthropic: Tool Design / OpenAI: Tool Documentation）：
- 工具应有 **清晰的边界**：每个工具做一件事
- 工具文档应包含：**何时使用、何时不使用、参数格式约束、示例调用、边界情况**
- 工具间应有 **组合提示**（"读取后用 X 工具处理"）
- Poka-yoke 原则：**防呆设计**，减少 LLM 误用工具的可能

**改进建议**：

```
优先级: P2

1. 工具 Schema 增强
   在工具注册时增加：
   - examples: 示例调用（few-shot for LLM）
   - common_errors: 常见错误及解决方案
   - related_tools: 相关工具推荐
   - preconditions: 调用前置条件

2. 动态工具裁剪
   当前所有工具始终暴露给 LLM（分层 schema 仅做摘要/完整切换）。
   建议：根据任务类型（route_result.task_tags）动态过滤不相关工具，
   减少 LLM 决策空间，降低误用概率。
   （OpenAI 建议：10-20 个工具以内效果最佳）

3. 参数防呆
   - file_path 参数自动补全为绝对路径
   - sheet_name 参数对可选值做 fuzzy match
   - range 参数自动校验格式（如 "A1:B10"）
   这些预处理已部分存在（backup 路径重定向），但未系统化。
```

---

### 3.7 🟡 多 Agent 协作架构初级

**现状**：
- 有 `SubagentOrchestrator` 支持委派/并行委派
- 子代理使用独立 prompt（通过 PromptComposer 的 subagent/ 目录）
- 子代理与主代理共享工具注册表和沙盒环境

**行业标准**（Microsoft: Multi-Agent Patterns / Anthropic: Orchestrator-Worker）：
- **Orchestrator-Worker**：编排器分解任务 → 分发给专家 worker → 汇总结果
- **Evaluator-Optimizer**：一个 Agent 生成，另一个评估并反馈改进
- 关键要素：Agent 间 **通信协议**、**共享状态管理**、**错误隔离**

**改进建议**：

```
优先级: P2

1. 子代理结果结构化
   当前子代理返回纯文本结果。
   建议：定义 SubagentResult 数据类，包含：
   - status: success/failure/partial
   - artifacts: 产出物列表（修改的文件、生成的数据）
   - metrics: token 用量、工具调用次数
   - error_summary: 失败时的结构化错误

2. 错误隔离
   子代理失败不应导致主代理熔断。
   建议：子代理有独立的迭代预算和熔断计数器。

3. Evaluator-Optimizer 模式
   对复杂 Excel 任务（cross_sheet、large_data），
   引入评估器子代理：执行后自动验证结果正确性，
   发现问题时生成修正建议并触发重试。
```

---

### 3.8 🟡 上下文预取不足（Pre-fetch）

**现状**：
- `_build_file_structure_context` 在路由阶段预读文件结构（sheet 列表、行数）
- `_build_large_file_context` 对大文件预生成提示
- `WindowPerception` 管理焦点窗口状态

**行业标准**（12-Factor #13: Pre-fetch / Anthropic: Augmented LLM）：
- 在 Agent 开始推理前，**尽可能多地预加载**它可能需要的上下文
- 减少 Agent 需要的工具调用轮次 = 减少延迟 + 减少 token 消耗 + 减少出错机会

**改进建议**：

```
优先级: P2

1. 意图感知预取
   路由阶段已知 task_tags（如 cross_sheet、formatting），
   可根据标签预读相关数据：
   - cross_sheet → 预读所有 sheet 的前 5 行 + 列头
   - formatting → 预读目标区域当前格式
   - formula → 预读相关单元格的公式

2. 历史模式学习
   通过分析历史会话的工具调用序列，
   预测当前任务可能需要的数据并预加载。
   （中长期优化，需要数据积累）
```

---

### 3.9 🟢 成本优化机制（Cost Optimization）

**现状**：
- Prompt cache 优化（静态前缀 + 动态后缀分层）
- token 计数缓存（fingerprint → token_count LRU）
- 轮次级 notice 缓存
- Compaction + Summarization 减少 context 膨胀

**行业标准**（OpenAI / Anthropic）：
- prompt caching 是关键成本优化手段
- 应监控每会话/每任务的 token 消耗并设上限

**改进建议**：

```
优先级: P2

1. 成本预算机制
   当前仅有迭代次数上限，无 token 总量上限。
   建议：增加 max_tokens_per_session 配置，
   接近阈值时降级到更小模型或提示用户。

2. 模型级联（Model Cascade）
   简单任务用小模型（如 GPT-4o-mini），复杂任务用强模型。
   当前 _compute_reasoning_level_static 已有推理级别计算，
   但未映射到实际的模型切换。
```

---

### 3.10 🟢 安全性增强

**现状**：
- `FileAccessGuard`（文件访问守卫）
- `CodePolicy` 引擎（代码风险分级 GREEN/YELLOW/RED）
- Docker 沙盒隔离（可选）
- CoW（Copy-on-Write）文件保护

**行业标准**（OpenAI: Security / Anthropic: Guardrails）：
- 输入净化（prompt injection 防御）
- 工具权限最小化
- 沙盒化执行环境

**改进建议**：

```
优先级: P2

1. Prompt Injection 防御
   当前无显式的 prompt injection 检测。
   建议：在用户输入进入 system prompt 前，
   增加轻量级注入检测（关键词匹配 + 可选 LLM 分类）。

2. 工具权限矩阵
   当前权限控制基于 fullaccess 开关（全有或全无）。
   建议：引入细粒度权限矩阵：
   - 每个工具声明所需权限级别
   - 用户可按工具级别授权
```

---

## 四、优先级总览

| 优先级 | 改进项 | 预估工作量 | 预期收益 |
|--------|--------|------------|----------|
| **P0** | 可观测性（OpenTelemetry 集成） | 3-5 天 | 根本性提升调试/运维能力 |
| **P0** | 输出验证强化（Schema 校验 + 防护栏管道） | 2-3 天 | 减少工具误用和不安全输出 |
| **P1** | Engine 拆分（AgentState + AgentLoop 提取） | 5-8 天 | 可测试性、可维护性质的飞跃 |
| **P1** | 状态持久化与恢复 | 3-5 天 | 支持长时间任务、断点续跑 |
| **P1** | 错误分类与自动重试 | 2-3 天 | 减少迭代浪费、提升稳定性 |
| **P2** | 工具设计增强 | 3-4 天 | 减少 LLM 误用、提升首次成功率 |
| **P2** | 多 Agent 协作强化 | 3-5 天 | 支撑复杂任务分解 |
| **P2** | 上下文预取优化 | 2-3 天 | 减少迭代轮次和延迟 |
| **P2** | 成本优化（预算 + 模型级联） | 2-3 天 | 降低运营成本 |
| **P2** | 安全性增强 | 2-3 天 | 防御攻击面 |

---

## 五、架构演进路线建议

### 第一阶段：基础设施加固（2-3 周）
- ✅ OpenTelemetry 集成（traces + metrics）
- ✅ 输出防护栏管道
- ✅ 错误分类与自动重试

### 第二阶段：核心架构重构（3-4 周）
- ✅ AgentState 数据类抽取
- ✅ AgentLoop 编排器独立
- ✅ 状态持久化 + checkpoint/resume

### 第三阶段：智能增强（持续迭代）
- ✅ 工具 schema 增强 + 动态裁剪
- ✅ 意图感知预取
- ✅ 模型级联 + 成本预算
- ✅ Evaluator-Optimizer 子代理

---

## 六、总结

ExcelManus 已经具备了行业 Agent 架构的多数核心要素：模块化提示词、技能路由、人类参与、熔断机制、卡住检测、元认知反思、Hook 系统、事务备份。这在同类项目中处于**中上水平**。

最突出的短板集中在两个方面：

1. **可观测性完全空白** — 这是所有行业来源一致强调的 Agent 核心基础设施，直接影响调试效率、生产运维和持续改进能力。

2. **Engine God Object** — 7000+ 行的单一类使得测试、调试、扩展都极其困难，违背了 12-Factor Agents 的「拥有控制流」和「无状态 reducer」原则。

建议优先投入 P0（可观测性 + 输出验证），然后逐步推进 P1 架构重构。
