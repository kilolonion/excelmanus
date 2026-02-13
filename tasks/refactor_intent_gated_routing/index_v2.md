# LLM-Native 路由与子代理架构方案（v2）

> **任务类型**：架构重构  
> **优先级**：高  
> **预计工期**：3-5 天  
> **状态**：方案设计中  
> **前置版本**：v1 (IGR) 已废弃 — 仍基于算法打分，修补不治本

---

## 一、问题与反思

### 1.1 直接触发问题

用户输入 `"你现在有python工具了吗"`（元问题），因 `excel_code_runner` 的 trigger 包含 `"python"` 且 priority=9，
走 `confident_direct` 快速路径 → 命中 `context: fork` → 无条件启动子代理。

### 1.2 根本架构缺陷

这不是一个 bug，而是 **架构范式错误**。当前系统用 **算法打分（trigger/description 词汇匹配）** 做路由决策——
这在本质上无法理解意图。所有基于当前架构的修补（提高阈值、加负面词、加守门）都是在错误地基上打补丁。

**行业共识**：Cursor、Claude Code、Google ADK **全部使用 LLM 做路由决策**，没有任何产品使用关键词打分路由。

---

## 二、行业调研（6 个产品）

### 2.1 Claude Code Skills

- **路由方式**：纯 LLM 路由。所有 skill 的 `description + when_to_use` 打包进 `Skill` meta-tool 描述。LLM 自行推理选择。
- **无算法匹配**：没有 trigger、scoring、prefilter。
- **执行生命周期**：`Skill` tool 被 LLM 调用 → 验证 → 权限检查 → 加载 SKILL.md → 两消息注入（metadata 可见 + prompt 不可见）→ LLM 带新上下文继续执行。
- **子代理**：`context: fork` 技能内容注入到独立子代理上下文中执行。
- **关键洞察**：*"No algorithmic matching. No lexical matching. No searches. This is pure LLM reasoning."*

### 2.2 Cursor Agent

- **架构组件**：Router → LLM → Tools → Context Retrieval → Orchestrator → Sandbox。
- **3 个内置子代理**：`explore`（代码库搜索）、`bash`（Shell 命令）、`browser`（浏览器 MCP）。
- **自定义子代理**：`.cursor/agents/*.md`，包含 name/description/model/readonly 等字段。
- **触发方式**：
  - **自动委派**：LLM 根据 task complexity + subagent descriptions + context 自动决定。
  - **显式调用**：`/name` 斜杠语法或自然语言提及。
  - **并行执行**：LLM 在一条消息中发多个 Task tool call → 子代理并发。
- **关键洞察**：*"The description field determines when Agent delegates to your subagent."* 子代理是 LLM 可用的 **工具**，不是规则触发的。

### 2.3 Windsurf / Cascade

- **Context Engine**：追踪编辑、终端、剪贴板、浏览器上下文，自动搜集相关信息。
- **Flows**：agentic 动作链，理解开发者意图后自主行动。
- **跨会话**：对话摘要 + checkpoint，恢复时按需检索而非全量加载。
- **关键洞察**：上下文搜集是自动化的基础设施，不需要用户触发。

### 2.4 Augment Code

- **Context Engine**：语义索引整个代码库，理解文件间关系。不是 grep，是 **搜索引擎**。
- **智能检索**："add logging to payment requests" → 自动映射 React app → Node API → payment service → DB → webhook 全链路。
- **上下文压缩**：只检索相关内容，自动排序和压缩，不超载上下文窗口。
- **关键洞察**：*"Most AI agents rely on grep to build context. They don't know what they don't know."*

### 2.5 OpenAI Agents SDK

- **Input Guardrails**：Agent 执行前的守门函数（blocking/parallel 两种模式）。
- **Handoff**：Triage Agent 分析意图 → handoff 给专业 Agent。
- **关键洞察**：昂贵操作前加轻量守门；Guardrail 可以是另一个 Agent。

### 2.6 Google ADK

- **Coordinator/Dispatcher 模式**：中央 Agent 分析意图，基于 sub-agent 的 description 做 LLM 驱动委派。
- **AutoFlow 机制**：自动路由。
- **关键洞察**：*"A central, intelligent agent acts as a dispatcher. It analyzes the user's intent and routes."*

### 2.7 总结：行业共性模式

| 模式 | 采用产品 | 核心理念 |
|------|---------|---------|
| **LLM-as-Router** | Claude Code, Cursor, Google ADK | 路由决策由 LLM 做，不是算法 |
| **Meta-Tool** | Claude Code (`Skill`), Cursor (`Task`) | 技能/子代理包装为工具，LLM 自主调用 |
| **Description-Driven** | 所有产品 | description 是路由的唯一锚点 |
| **子代理 = 工具** | Cursor, Claude Code | 子代理是 LLM 可选的工具，非规则触发 |
| **Slash = 直连** | Cursor, Claude Code, ExcelManus | 斜杠命令绕过 LLM 直接分派 |
| **Guardrail 守门** | OpenAI SDK | 昂贵操作前加轻量检查 |

---

## 三、全新架构设计：LLM-Native Routing

### 3.1 范式转换

```
旧范式：  算法打分 → 选 skill → 限制工具 → LLM 在笼子里执行
新范式：  LLM 看到全局 → LLM 选 skill/subagent → 上下文注入 → LLM 自主执行
```

**核心改变**：把路由决策权从算法交还给 LLM。

### 3.2 整体架构图

```
┌──────────────────────────────────────────────────────────────────┐
│                        用户消息输入                               │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Layer 0: 斜杠直连   │  /skill_name args → 直接分派
                    └──────────┬──────────┘  （不走 LLM，零延迟）
                               │ (非斜杠)
                    ┌──────────▼──────────┐
                    │  Layer 1: 主 LLM     │  模型收到：
                    │  统一决策层           │  ├─ 基础 system prompt
                    │                      │  ├─ 全量常规工具（read_excel 等）
                    │                      │  ├─ select_skill 元工具（含技能目录）
                    │                      │  └─ explore_data 子代理工具
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
     LLM 不调工具      LLM 调 select_skill   LLM 调 explore_data
     （直接回答）      （选择技能包）          （启动子代理）
              │                │                │
              ▼                ▼                ▼
        ┌──────────┐   ┌──────────────┐  ┌──────────────┐
        │ 直接回复  │   │ 技能上下文    │  │ 子代理执行    │
        │ (元问题/  │   │ 注入 + 工具   │  │ (只读探查)   │
        │  闲聊)    │   │ 重新限定      │  │              │
        └──────────┘   └──────┬───────┘  └──────┬───────┘
                              │                  │
                              ▼                  │ 摘要回传
                    ┌─────────────────┐          │
                    │  主 LLM 继续执行 │◄─────────┘
                    │  (带技能上下文)   │
                    └─────────────────┘
```

### 3.3 Layer 0：斜杠直连（保留不变）

与当前完全一致：`/skill_name args` → 直接加载 SKILL.md → 参数化执行。

### 3.4 Layer 1：主 LLM 统一决策层（核心重构）

#### 3.4.1 `select_skill` 元工具

借鉴 Claude Code 的 `Skill` meta-tool 模式。把所有技能的摘要打包进一个工具的描述中：

```python
SELECT_SKILL_TOOL = {
    "type": "function",
    "function": {
        "name": "select_skill",
        "description": (
            "激活一个技能包来处理当前任务。调用后会获得该技能的详细指引和专用工具。\n"
            "仅在需要执行具体 Excel 操作时调用。如果用户只是闲聊、询问能力或打招呼，"
            "不要调用此工具，直接回复即可。\n\n"
            "可用技能：\n"
            "{skill_catalog}"  # 动态生成
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "要激活的技能名称",
                    "enum": [...]  # 动态填充
                },
                "reason": {
                    "type": "string",
                    "description": "选择该技能的原因（一句话）"
                }
            },
            "required": ["skill_name"]
        }
    }
}
```

**skill_catalog 动态生成示例**：

```
- data_basic: 数据读取、分析、筛选与转换。适用于查看/分析/修改 Excel 数据。
- excel_code_runner: 通过 Python 脚本处理 Excel。适用于大文件、复杂计算、批处理。需要先探查数据结构。
- chart_basic: 创建图表。适用于可视化需求。
- format_basic: 单元格格式化。适用于调整样式、字体、颜色、边框等。
- file_ops: 文件读写与目录操作。适用于文件管理。
- sheet_ops: 工作表管理。适用于新建/删除/重命名 sheet。
```

#### 3.4.2 `explore_data` 子代理工具

借鉴 Cursor 的 `explore` 内置子代理。把 fork 变成 LLM 可选的工具：

```python
EXPLORE_DATA_TOOL = {
    "type": "function",
    "function": {
        "name": "explore_data",
        "description": (
            "启动只读数据探索子代理，用于在操作前了解数据结构和质量。\n"
            "子代理会分析 Excel 文件并返回高密度摘要（sheet 结构、列信息、数据质量等）。\n\n"
            "适用场景：\n"
            "- 大体量 Excel 文件（>8MB），需要先了解结构再处理\n"
            "- 未知结构的文件，需要探查后制定处理方案\n"
            "- 复杂数据质量问题，需要先诊断\n\n"
            "不适用场景：\n"
            "- 用户已明确告知文件结构和需求\n"
            "- 简单的数据读取或已知结构的操作\n"
            "- 用户只是询问能力，不需要实际操作"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "探查任务描述"
                },
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要探查的文件路径列表"
                }
            },
            "required": ["task"]
        }
    }
}
```

#### 3.4.3 LLM 统一决策流程

主 LLM 在第一轮收到的工具集：

```
常规工具（始终可用）:
  read_excel, write_excel, analyze_data, filter_data,
  transform_data, list_sheets, write_text_file, run_python_script,
  read_text_file, search_files, get_file_info, list_directory, ...

元工具:
  select_skill    → 激活技能包（获取详细指引 + 限定工具范围）
  explore_data    → 启动只读探查子代理
  list_skills     → 列出所有可用技能详情
```

LLM 自然行为：
- **"你有python工具吗"** → LLM 理解这是元问题 → 不调任何工具 → 直接回复
- **"帮我分析销售数据.xlsx"** → LLM 调 `select_skill("data_basic")` → 获得技能指引
- **"这个 50MB 的 Excel 要怎么处理"** → LLM 调 `explore_data(task="分析文件结构")` → 获得摘要后再决策
- **"用 python 批量处理这些文件"** → LLM 调 `select_skill("excel_code_runner")` → 进入代码工作流

#### 3.4.4 技能上下文注入机制

当 `select_skill` 被调用后：

```python
async def _handle_select_skill(self, skill_name: str, reason: str):
    """处理 select_skill 工具调用。"""
    skill = self._loader.get_skillpack(skill_name)
    if skill is None:
        return "未找到技能: " + skill_name

    # 1. 渲染技能上下文
    context = skill.render_context()

    # 2. 返回工具结果（包含技能指引）
    result = (
        f"已激活技能 [{skill_name}]。\n"
        f"以下是该技能的执行指引，请严格遵循：\n\n"
        f"{context}"
    )

    # 3. 动态限定后续工具范围（关键！）
    self._active_tool_scope = skill.allowed_tools

    return result
```

**与 Claude Code 的关键区别**：
- Claude Code 用两消息注入（metadata + hidden prompt）。
- 我们用 tool result 注入 — 更简单，且天然支持 OpenAI API 的 tool_call 消息流。

### 3.5 子代理执行机制（重构）

#### 3.5.1 `explore_data` 子代理

当 LLM 调用 `explore_data` 时：

```python
async def _handle_explore_data(self, task: str, file_paths: list[str]):
    """处理 explore_data 子代理调用。"""
    # 1. 构建子代理 system prompt
    system = self._build_explorer_system_prompt(task, file_paths)

    # 2. 在独立上下文中执行（只读工具）
    summary = await self._execute_subagent_loop(
        system_prompt=system,
        tool_scope=self._READ_ONLY_TOOLS,
        max_iterations=self._config.subagent_max_iterations,
    )

    # 3. 返回摘要给主 LLM
    return f"[数据探查摘要]\n{summary}"
```

**与当前 fork 的关键区别**：
- **当前**：算法规则自动触发 fork → LLM 无选择权
- **新**：LLM 主动调用 explore_data → LLM 有完全选择权
- **当前**：fork 发生在路由阶段（chat 之前）
- **新**：explore 发生在执行阶段（chat loop 中的 tool call）

#### 3.5.2 未来扩展：更多子代理

参照 Cursor 的模式，可以后续添加更多子代理工具：

```
explore_data   → 数据结构探查（已实现）
run_code       → 代码执行子代理（隔离环境）
validate       → 结果验证子代理（只读校验）
```

每个子代理都是一个工具，LLM 自主选择何时使用。

### 3.6 工具范围动态管理

#### 3.6.1 三种状态

```
状态 1: 未激活技能（初始状态）
  → 全量常规工具 + select_skill + explore_data + list_skills

状态 2: 已激活技能（select_skill 被调用后）
  → 技能 allowed_tools + select_skill（允许切换）

状态 3: 子代理执行中（explore_data 被调用后）
  → 只读工具集（子代理独立上下文）
```

#### 3.6.2 技能切换

LLM 可以在对话中再次调用 `select_skill` 切换技能。
技能上下文累积逻辑与当前 `_merge_with_loaded_skills` 保持一致。

---

## 四、与当前架构对比

### 4.1 决策流对比

```
当前：
  用户消息 → [算法打分] → confident_direct/llm_confirm → [规则判断 fork] → LLM 执行
  问题：算法不理解意图，规则无条件 fork

新：
  用户消息 → LLM 看到全局 → LLM 自己决定要不要选技能/探查 → LLM 执行
  优势：意图理解是 LLM 的天然能力
```

### 4.2 对本次 bug 的解决

```
输入: "你现在有python工具了吗"

当前: trigger "python" → score 12 → confident_direct → 自动 fork → 浪费
新:   LLM 看到 select_skill 描述中写着"如果用户只是闲聊或询问能力，不要调用"
      → LLM 不调任何工具 → 直接回答 "是的，我有..."
```

**零额外成本，零规则，零守门**。LLM 天然理解这不是操作请求。

### 4.3 全面对比

| 维度 | 当前架构 | v1 (IGR) | **v2 (LLM-Native)** |
|------|---------|----------|---------------------|
| 路由决策者 | 算法 | 算法 + 规则守门 | **LLM** |
| 意图理解 | ❌ 无 | 规则分类（有限） | ✅ LLM 天然具备 |
| Fork 触发 | 规则自动 | 规则 + 守门 | **LLM 自主决定** |
| 与行业对齐 | ❌ 独创算法 | ❌ 仍是算法+补丁 | ✅ Claude Code/Cursor 模式 |
| 新增 LLM 调用 | 0 | 0~1（守门） | 0（元工具是主调用的一部分） |
| 代码复杂度 | 高（router 744行） | 更高（+intent+guardrail） | **降低**（删除 prefilter） |
| SKILL.md 改动 | 无 | 无 | 无（description 字段复用） |
| 扩展性 | 加 skill 需调 trigger | 加 skill 需调 trigger + 规则 | **加 skill 只需写 description** |

---

## 五、与行业方案最终对比

| 维度 | Claude Code | Cursor | OpenAI SDK | Google ADK | **ExcelManus v2** |
|------|------------|--------|-----------|-----------|-------------------|
| 路由机制 | `Skill` meta-tool | `Task` tool | Handoff | Coordinator | `select_skill` + `explore_data` |
| LLM 决策 | ✅ | ✅ | ✅ (triage) | ✅ | ✅ |
| 子代理触发 | context: fork | description-based | guardrail | coordinator | **LLM 调 explore_data 工具** |
| 斜杠直连 | ✅ | ✅ | — | — | ✅ |
| 上下文注入 | 两消息 | tool result | — | prompt | **tool result**（简洁） |
| 工具动态限定 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 技能切换 | ✅ | — | handoff | — | ✅（再次调 select_skill） |

---

## 六、需要删除/重构的代码

### 6.1 删除

| 文件/方法 | 原因 |
|----------|------|
| `router.py` → `_prefilter_candidates()` | 算法打分不再需要 |
| `router.py` → `_score_triggers()` | trigger 匹配不再需要 |
| `router.py` → `_score_description()` | description 打分不再需要 |
| `router.py` → `_score_file_patterns()` | 文件模式打分不再需要 |
| `router.py` → confident_direct / llm_confirm 路径 | LLM 自己选择技能 |
| `router.py` → `_build_fork_plan()` | fork 不再由路由构建 |
| `router.py` → `_decorate_result()` | 不再需要 fork 装饰 |
| `engine.py` → `_run_fork_subagent_if_needed()` | fork 改为 tool call |
| `models.py` → `ForkPlan` | 不再需要 |
| `models.py` → `SkillMatchResult.fork_plan` | 不再需要 |
| Skillpack → `triggers` 字段 | 不再用于路由（但保留兼容性） |
| Skillpack → `context: fork` | 改为工具描述驱动 |

### 6.2 新增

| 文件/方法 | 说明 |
|----------|------|
| `engine.py` → `_build_meta_tools()` | 生成 select_skill + explore_data 工具定义 |
| `engine.py` → `_handle_select_skill()` | 处理技能选择 tool call |
| `engine.py` → `_handle_explore_data()` | 处理子代理 tool call |
| `engine.py` → `_active_tool_scope` | 动态工具范围状态 |
| `router.py` → 大幅简化 | 仅保留 slash 直连 + skill catalog 生成 |

### 6.3 保留不变

| 组件 | 原因 |
|------|------|
| SKILL.md 格式 | 完全兼容，description 字段复用 |
| SkillpackLoader | 三层加载机制不变 |
| 所有 tool 实现 | 工具本身不变 |
| `_execute_fork_plan_loop` | 重命名为 `_execute_subagent_loop`，逻辑保留 |
| 斜杠命令分派 | 不变 |
| /fullAccess 权限控制 | 不变 |
| 会话管理 | 不变 |

---

## 七、实施计划

| 阶段 | 内容 | 预计耗时 | 依赖 |
|------|------|---------|------|
| **P0** | 设计 select_skill + explore_data 工具定义 | 1h | — |
| **P1** | 简化 router.py（仅保留 slash 直连 + catalog 生成） | 3h | P0 |
| **P2** | engine.py 集成元工具 + 技能选择 handler | 4h | P1 |
| **P3** | 将 fork 重构为 explore_data tool call | 3h | P2 |
| **P4** | 动态工具范围管理（状态机） | 2h | P2 |
| **P5** | 端到端测试 + 迁移验证 | 3h | P3+P4 |
| **P6** | 清理废弃代码 + 更新文档 | 2h | P5 |

---

## 八、测试用例矩阵

| 输入 | 预期 LLM 行为 | select_skill | explore_data |
|------|-------------|:---:|:---:|
| "你有python工具吗" | 直接回答 | ❌ | ❌ |
| "你能做什么" | 直接回答 | ❌ | ❌ |
| "你好" | 直接回答 | ❌ | ❌ |
| "帮我分析销售数据.xlsx" | 选择 data_basic | ✅ | ❌ |
| "把A列格式化为百分比" | 选择 format_basic | ✅ | ❌ |
| "用python处理这个50MB的Excel" | 选择 excel_code_runner，可能先 explore | ✅ | 可能 ✅ |
| "这个文件结构是什么样的" | 先 explore 再回答 | ❌ | ✅ |
| "创建一个柱状图" | 选择 chart_basic | ✅ | ❌ |
| "/data_basic 分析数据" | Layer 0 直连 | N/A | N/A |
| "合并这三个sheet" | 选择 sheet_ops 或 data_basic | ✅ | ❌ |

---

## 九、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| LLM 选错技能 | 工具范围不对 | select_skill 可重复调用切换；description 持续优化 |
| LLM 不调 select_skill 就直接用工具 | 无技能指引 | 初始状态已有全量工具，不会报错；只是少了指引 |
| explore_data 被过度使用 | 延迟增加 | description 明确写"不适用场景"；模型会自己判断 |
| 技能目录 token 占用 | 输入成本增加 | ~500 tokens，远小于当前 skills_context_char_budget=12000 |
| 迁移期间兼容性 | 已有测试失败 | 分阶段迁移，P5 专门做迁移验证 |

---

## 十、总结

**本方案的核心转变**：从"算法决策 + 规则触发"转向"LLM 决策 + 工具化子代理"。

这不是对现有架构的修补，而是与 **Cursor、Claude Code、Google ADK** 对齐的范式升级：
1. **路由** = LLM 通过 `select_skill` 元工具自主选择
2. **子代理** = LLM 通过 `explore_data` 工具自主触发
3. **斜杠** = 唯一保留的确定性路径
4. **意图理解** = LLM 天然能力，无需额外规则/守门

一句话：**让 LLM 做 LLM 擅长的事**。
