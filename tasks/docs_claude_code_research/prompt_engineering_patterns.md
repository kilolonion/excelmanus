# 主流 Agent 提示词工程设计模式研究

> **调研日期**：2026-02-13
> **调研对象**：Claude Code v2.1.41、OpenAI Codex (GPT-5.2)、Cursor Agent、Windsurf Cascade
> **目标**：提取可借鉴的提示词工程模式，对标 ExcelManus 当前系统提示词，找出改进方向

---

## 一、四大 Agent 系统提示词架构对比

### 1.1 结构规模

| Agent | 系统提示词总量 | 章节数 | 工具描述平均长度 |
|-------|-------------|--------|----------------|
| **Claude Code** | ~15,000 token（25+ 模块化 .md 拼接） | 25+ | 200-600 token/工具 |
| **Codex** | ~3,000 token（单一长文本） | 10 | 50-150 token/工具 |
| **Cursor** | ~2,500 token（XML 标签分区） | 7 | 100-300 token/工具 |
| **Windsurf** | ~2,000 token（Markdown 分区） | 6 | 80-200 token/工具 |
| **ExcelManus 现状** | ~350 token | 6 | 15-30 token/工具 |

**关键差距**：ExcelManus 的系统提示词仅 ~350 token，约为主流 Agent 的 **10%-15%**。工具描述平均仅 ~20 token，为主流的 **7%-15%**。

### 1.2 章节主题覆盖

| 主题 | Claude Code | Codex | Cursor | Windsurf | ExcelManus |
|------|:-----------:|:-----:|:------:|:--------:|:----------:|
| 身份定位 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 工作循环/流程 | ✅ Doing tasks | ✅ Autonomy | ❌ | ❌ | ✅ |
| 工具策略 | ✅ Tool usage policy | ✅ General | ✅ tool_calling | ❌ | ✅ |
| 任务管理 | ✅ Task management | ✅ Plan tool | ❌ | ❌ | ✅ (刚加) |
| 安全/风险控制 | ✅ Executing with care | ✅ Editing constraints | ❌ | ❌ | ✅ |
| 探索策略 | ✅ Delegate exploration | ✅ Exploration | ✅ search_and_reading | ❌ | ❌ |
| 代码实现质量 | ✅ Doing tasks | ✅ Code Implementation | ✅ making_code_changes | ❌ | ❌ |
| 语气/风格 | ✅ Tone and style | ✅ Presenting work | ✅ communication | ❌ | ✅ (输出要求) |
| 并行工具调用 | ✅ 专节 | ✅ 专节 | ❌ | ❌ | ❌ |
| 专业客观性 | ✅ Professional objectivity | ❌ | ❌ | ❌ | ❌ |
| 可逆性评估 | ✅ Reversibility/blast radius | ✅ Dirty worktree | ❌ | ❌ | ❌ |

---

## 二、关键提示词工程模式

### 模式1：分段协议式结构（Sectioned Protocol）

**所有主流 Agent 都使用**。用 `#` / `##` 或 XML 标签分隔主题，每个章节聚焦一个关注域。

| Agent | 分隔方式 | 示例 |
|-------|---------|------|
| Claude Code | `# Markdown H1` + 模块化文件 | `# Task Management`, `# Tone and style` |
| Codex | `# Markdown H1` 内联 | `# Autonomy and Persistence`, `# Code Implementation` |
| Cursor | `\<xml_tag>` | `\<communication>`, `\<tool_calling>`, `\<making_code_changes>` |
| Windsurf | `# Markdown H1` | 类似 Claude Code |

**ExcelManus 现状**：已采用 `## H2` 分节，结构正确但章节偏少。

**借鉴建议**：✅ 已满足，后续可考虑新增"探索策略"和"并行工具调用"章节。

---

### 模式2：偏向行动（Bias to Action）

**Codex 和 Cursor 的核心哲学**，也是 Claude Code 的隐含原则。

**Codex 原文**（重复 2 次！）：
```
Bias to action: default to implementing with reasonable assumptions;
do not end your turn with clarifications unless truly blocked.
```

**Cursor 原文**：
```
Bias towards not asking the user for help if you can find the answer yourself.
```

**Claude Code 原文**：
```
When the user's intent is clear, go ahead and complete the task
instead of providing unnecessary preambles.
```

**ExcelManus 现状**：
```
用户意图明确时默认执行，不要仅给出建议。
```

**差距分析**：ExcelManus 只有一句话，且未覆盖"不确定时用合理假设行动"的场景。Codex 额外强调了：
- "deliver working code, not just a plan"（交付可运行成果，不只是计划）
- "Every rollout should conclude with a concrete edit or an explicit blocker"（每轮必须有具体产出或明确阻塞点）
- "Persist until the task is fully handled end-to-end"（端到端坚持完成）

**借鉴建议**：在"工作循环"或"工具策略"中加入更强的 bias-to-action 语句。

---

### 模式3：先探索再修改（Explore Before Edit）

**所有 Agent 都强调这一点**，但方式不同。

**Cursor 原文**：
```
Unless you are appending some small easy to apply edit to a file,
or creating a new file, you MUST read the contents or section of
what you're editing before editing it.
```

**Codex 原文**：
```
Think first. Before any tool call, decide ALL files/resources you will need.
Batch everything. If you need multiple files, read them together.
```

**Claude Code 原文**：
```
For broader codebase exploration and deep research, use the Task tool
with subagent_type=explore. Use this only when a simple, directed
search proves to be insufficient.
```

**ExcelManus 现状**：
```
1. 探索：用最少的只读工具获取必要上下文（文件结构、sheet 列表、样本数据）。
```

**差距分析**：ExcelManus 工作循环中有"探索"步骤，但缺少"写入前必须先读取"的硬约束。

**借鉴建议**：在"工具策略"中强化 `写入前先读取目标区域` 为硬约束（已有，可强化措辞）。

---

### 模式4：可逆性与爆炸半径评估（Reversibility & Blast Radius）

**Claude Code 最突出的创新**，Codex 也有类似理念。

**Claude Code 原文**：
```
Carefully consider the reversibility and blast radius of actions.
Generally you can freely take local, reversible actions like editing
files or running tests. But for actions that are hard to reverse,
affect shared systems, or could otherwise be risky or destructive,
check with the user before proceeding.
```

**核心分类**：
- ✅ **可自由执行**：编辑文件、运行测试（本地可逆）
- ⚠️ **需确认**：删除文件/分支、覆盖数据、对外发送消息
- 🔴 **高危**：force-push、reset --hard、修改 CI/CD

**ExcelManus 现状**：
```
只读和本地可逆操作可直接执行。
高风险操作（删除、覆盖、批量改写）需先请求确认。
```

**差距分析**：ExcelManus 已有基本框架，但缺少"遇到障碍时不要用破坏性操作走捷径"的补充（Claude Code 专门强调了这点）。

**借鉴建议**：在"安全策略"中补充一条关于"不用破坏性操作绕过问题"的规则。

---

### 模式5：专业客观性（Professional Objectivity）

**Claude Code 独有**，其他 Agent 没有显式表达。

**Claude Code 原文**：
```
Prioritize technical accuracy and truthfulness over validating the
user's beliefs. Focus on facts and problem-solving. It is best for
the user if Claude honestly applies the same rigorous standards to
all ideas and disagrees when necessary, even if it may not be what
the user wants to hear.
```

**以及**：
```
Avoid using over-the-top validation or excessive praise when
responding to users such as "You're absolutely right" or similar.
```

**ExcelManus 现状**：无此章节。

**借鉴建议**：对 Excel 操作场景而言，这一原则体现为"发现数据异常时如实报告，而非忽略"。可在"输出要求"中补充。

---

### 模式6：工具偏好层级（Tool Preference Hierarchy）

**Codex 和 Claude Code 都明确定义了工具使用优先级**。

**Codex 原文**：
```
If a tool exists for an action, prefer to use the tool instead of
shell commands (e.g read_file over cat). Strictly avoid raw cmd/terminal
when a dedicated tool exists. Default to solver tools: git, rg,
read_file, list_dir, apply_patch, todo_write/update_plan.
```

**Claude Code 原文**：
```
Use specialized tools instead of bash commands when possible.
For file operations, use dedicated tools: Read for reading files
instead of cat/head/tail, Edit for editing instead of sed/awk.
```

**ExcelManus 现状**：无此概念。ExcelManus 的工具都是专用的（read_excel、write_excel），没有 shell fallback，所以这一模式的直接适用度较低。

**借鉴建议**：对于 `run_code` / `run_shell` 等通用工具，可在"工具策略"中加入偏好层级：`优先使用专用 Excel 工具，仅在专用工具无法完成时使用代码执行`。

---

### 模式7：并行工具调用（Parallel Tool Calling）

**Codex 最重视**，Claude Code 也有专节。

**Codex 原文**：
```
When multiple tool calls can be parallelized (e.g., todo updates with
other actions, file searches, reading files), make these tool calls
in parallel instead of sequential. Always maximize parallelism.
Never read files one-by-one unless logically unavoidable.
Workflow: (a) plan all needed reads → (b) issue one parallel batch →
(c) analyze results → (d) repeat if new, unpredictable reads arise.
```

**ExcelManus 现状**：
```
独立操作可并行，依赖步骤必须串行。
```

**差距分析**：ExcelManus 只有一句原则性描述，缺少具体的工作流指导。

**借鉴建议**：在"工具策略"中补充并行调用的具体指导（先规划所有需要的读取 → 批量执行 → 分析结果）。

---

### 模式8：不给时间估算（No Time Estimates）

**Claude Code 独有的有趣规则**。

```
Never give time estimates or predictions for how long tasks will take.
Avoid phrases like "this will take me a few minutes" or "this is a quick fix".
Focus on what needs to be done, not how long it might take.
```

**借鉴建议**：对 ExcelManus 有价值——避免 AI 说"这很快就能完成"然后实际耗时很长。可纳入"输出要求"。

---

### 模式9：Plan 工具的精细使用规则（Plan Hygiene）

**Codex 最详细**，Claude Code 也有对应的 Task management。

**Codex 原文**（6 条规则）：
```
1. Skip using the planning tool for straightforward tasks (roughly
   the easiest 25%).
2. Do not make single-step plans.
3. When you made a plan, update it after having performed one of
   the sub-tasks.
4. Unless asked for a plan, never end the interaction with only a plan.
   Plans guide your edits; the deliverable is working code.
5. Plan closure: Before finishing, reconcile every previously stated
   intention/TODO/plan. Mark each as Done, Blocked, or Cancelled.
   Do not end with in_progress/pending items.
6. Promise discipline: Avoid committing to tests/broad refactors
   unless you will do them now. Otherwise, label them explicitly as
   optional "Next steps".
```

**ExcelManus 现状**（刚加入的"任务管理"章节）：
```
- 复杂任务（3 步以上）开始前，使用 task_create 创建任务清单。
- 开始执行某步前标记 in_progress，完成后立即标记 completed。
- 同一时间只有一个子任务处于执行中。
- 如果不规划就执行，可能遗漏关键步骤——这是不可接受的。
```

**差距分析**：ExcelManus 覆盖了 Codex 规则中的 1-3，但缺少 4-6：
- **规则4**：不能以"仅给出计划"结束，必须交付实际结果
- **规则5**：结束前清理所有 TODO 状态（Plan closure）
- **规则6**：不轻易承诺后续步骤（Promise discipline）

**借鉴建议**：在"任务管理"章节补充 plan closure 和 promise discipline 规则。

---

### 模式10：每个工具都要求 explanation 参数（Tool Call Explanation）

**Cursor 独有的设计**——每个工具都有一个 `explanation` 必填参数。

```json
{
  "explanation": {
    "description": "One sentence explanation as to why this tool is being used,
                    and how it contributes to the goal.",
    "type": "string"
  }
}
```

这迫使模型在每次调用前想清楚"为什么要用这个工具"。

**Claude Code 等效**：在系统提示词中写 `每次工具调用前用一句话说明目的`。
**ExcelManus 现状**：也在系统提示词中写了同样的话，但没有在工具 schema 中强制。

**借鉴建议**：可考虑在 ExcelManus 的高风险工具（write_excel、delete_file）中添加 `reason` 可选参数，但不建议全工具强制（避免增加 token 开销）。

---

## 三、ExcelManus 系统提示词改进方案

### 3.1 现状评估

当前 `_DEFAULT_SYSTEM_PROMPT` 约 350 token，6 个章节：
- ✅ 身份定位、工作循环、工具策略、任务管理、安全策略、输出要求

### 3.2 建议新增/增强的章节

| 优先级 | 章节 | 来源模式 | 预估 token 增量 | 理由 |
|--------|------|----------|----------------|------|
| **P0** | 增强"工具策略"：并行调用指导 | Codex 模式7 | +40 | 减少串行调用浪费 |
| **P0** | 增强"任务管理"：plan closure | Codex 模式9 | +30 | 避免遗留 pending 状态 |
| **P1** | 增强"工作循环"：bias to action | Codex 模式2 | +30 | 减少不必要的确认/解释 |
| **P1** | 增强"安全策略"：不走破坏性捷径 | Claude Code 模式4 | +20 | 防止覆盖用户数据 |
| **P2** | 新增"数据诚实"：发现异常如实报告 | Claude Code 模式5 | +20 | Excel 场景特有需求 |
| **P2** | 增强"输出要求"：不给时间估算 | Claude Code 模式8 | +10 | 避免虚假承诺 |

**总增量**：~150 token → 系统提示词从 ~350 提升到 ~500 token

### 3.3 工具描述改进方向

根据调研，工具描述应包含：

| 要素 | 主流做法 | ExcelManus 现状 | 建议 |
|------|---------|----------------|------|
| **功能说明** | 1-2 句 | ✅ 有 | 保持 |
| **使用场景** | 列举 3-5 种 | ❌ 仅 task_tools 刚加 | 高频工具补充 |
| **不使用场景** | 列举反面 | ❌ 仅 task_tools 刚加 | 关键工具补充 |
| **注意事项** | 参数约束、安全提示 | 部分有 | 补充 |
| **偏好替代** | 优先用 X 而非 Y | ❌ | 对 run_code vs 专用工具补充 |

**优先改进的工具**：
1. `write_excel` — 加入"写入前先读取确认"、"批量写入优于逐行"
2. `run_code` — 加入"仅在专用工具无法完成时使用"、"优先使用小步可验证脚本"
3. `create_chart` — 加入"先确认数据范围和字段含义"
4. `delete_file` — 加入"不可逆操作，需用户确认"

---

## 四、具体改进文本（可直接使用）

### 4.1 增强"工具策略"

当前：
```
## 工具策略
- 参数不足时先读取或询问，不猜测路径和字段名。
- 写入前先读取目标区域，优先使用可逆操作。
- 用户意图明确时默认执行，不要仅给出建议。
- 每次工具调用前用一句话说明目的。
```

建议增强为：
```
## 工具策略
- 参数不足时先读取或询问，不猜测路径和字段名。
- 写入前先读取目标区域，优先使用可逆操作。
- 用户意图明确时默认执行，不仅给出建议；信息不足时用合理假设行动，除非真正受阻才提问。
- 优先使用专用 Excel 工具，仅在专用工具无法完成时使用代码执行。
- 独立操作应并行调用：先规划需要的读取，批量执行，再根据结果决定下一步。
- 每次工具调用前用一句话说明目的。
```

### 4.2 增强"任务管理"

当前：
```
## 任务管理
- 复杂任务（3 步以上）开始前，使用 task_create 创建任务清单。
- 开始执行某步前标记 in_progress，完成后立即标记 completed。
- 同一时间只有一个子任务处于执行中。
- 如果不规划就执行，可能遗漏关键步骤——这是不可接受的。
```

建议增强为：
```
## 任务管理
- 复杂任务（3 步以上）开始前，使用 task_create 创建任务清单。
- 开始执行某步前标记 in_progress，完成后立即标记 completed。
- 同一时间只有一个子任务处于执行中。
- 如果不规划就执行，可能遗漏关键步骤——这是不可接受的。
- 不要以"仅给出计划"结束，计划指导执行，交付物是实际结果。
- 结束前清理所有任务状态：标记为 completed、failed 或删除已取消项，不要留下 pending/in_progress。
```

### 4.3 增强"安全策略"

当前：
```
## 安全策略
- 只读和本地可逆操作可直接执行。
- 高风险操作（删除、覆盖、批量改写）需先请求确认。
- 遇到权限限制时，告知限制原因与解锁方式，不绕过。
```

建议增强为：
```
## 安全策略
- 只读和本地可逆操作可直接执行。
- 高风险操作（删除、覆盖、批量改写）需先请求确认。
- 遇到权限限制时，告知限制原因与解锁方式，不绕过。
- 遇到障碍时排查根本原因，不要用破坏性操作（如覆盖原文件）走捷径。
```

### 4.4 增强"输出要求"

当前：
```
## 输出要求
- 完成后输出结果摘要与关键证据（数字、路径、sheet 名）。
- 需要多步操作时逐步执行，每步完成后简要汇报。
- 保持简洁，避免冗长的背景解释。
```

建议增强为：
```
## 输出要求
- 完成后输出结果摘要与关键证据（数字、路径、sheet 名）。
- 需要多步操作时逐步执行，每步完成后简要汇报。
- 保持简洁，避免冗长的背景解释。
- 发现数据异常（空值、类型不匹配、异常值）时如实报告，不忽略。
- 不给出时间估算（"很快完成"、"大约需要几分钟"），聚焦于做什么。
```

---

## 五、改进影响评估

### Token 开销

| 版本 | Token 量 | 增量 |
|------|---------|------|
| 改进前（当前） | ~350 | — |
| 改进后（建议） | ~500 | +150 (~43%) |

~150 token 增量在 128K 上下文中完全可忽略，但能显著提升 AI 行为质量。

### 预期效果

| 改进 | 预期效果 |
|------|---------|
| 并行调用指导 | 减少 2-3 轮不必要的串行读取 |
| Bias to action | 减少"请问您是否需要..."等无效确认 |
| Plan closure | 消除任务清单遗留 pending 的问题 |
| 安全策略补充 | 防止 AI 在遇到问题时覆盖原始数据 |
| 数据诚实 | 让用户知道数据存在的真实问题 |

---

## 六、参考资料

1. [Claude Code System Prompts (Piebald-AI)](https://github.com/Piebald-AI/claude-code-system-prompts) — v2.1.41 完整提取
2. [Codex Prompting Guide (OpenAI)](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide) — GPT-5.2 官方指南
3. [Cursor IDE System Prompt (leaked)](https://github.com/jujumilk3/leaked-system-prompts/blob/main/cursor-ide-sonnet_20241224.md)
4. [Windsurf Cascade System Prompt (leaked)](https://github.com/jujumilk3/leaked-system-prompts/blob/main/codeium-windsurf-cascade-R1_20250201.md)
5. [Claude Code Tone and Style](https://github.com/Piebald-AI/claude-code-system-prompts/blob/main/system-prompts/system-prompt-tone-and-style.md)
6. [Claude Code Executing with Care](https://github.com/Piebald-AI/claude-code-system-prompts/blob/main/system-prompts/system-prompt-executing-actions-with-care.md)
7. [Claude Code Tool Usage Policy](https://github.com/Piebald-AI/claude-code-system-prompts/blob/main/system-prompts/system-prompt-tool-usage-policy.md)
