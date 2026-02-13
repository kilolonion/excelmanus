# Agent 内联澄清与上下文委派模式研究

> **日期**：2026-02-13
> **触发场景**：true_example.md — Agent 花 7 轮盲猜文件，浪费 25K token
> **研究目标**：如何借鉴成熟 Agent 的 ask_user 和 subagent 委派模式

---

## 一、问题诊断

### 1.1 true_example.md 回放

```
用户：读取"销售明细"工作表的前 10 行数据
  ↓ 轮次 1: select_skill → file_ops
  ↓ 轮次 2: search_files → 找到 6 个 .xlsx（关键信息已到手！）
  ↓ 轮次 3: read_excel("销售数据示例.xlsx") → 默认 sheet，没有"销售明细"
  ↓ 轮次 4: read_excel(..., sheet_name="销售明细") → 失败
  ↓ 轮次 5: read_excel("demo_sales_data.xlsx") → 也没有
  ↓ 轮次 6: read_excel("示例数据.xlsx") → 也没有
  ↓ 轮次 7: 猜测"销售数据示例.xlsx"就是用户想要的，强行输出结果
```

**问题**：轮次 2 后已掌握 6 个候选文件，但 Agent 没有：
1. **使用 `ask_user` 工具**让用户从候选中选择 → 而是逐个盲猜
2. **委派 `explorer` 子代理**在后台批量列出所有文件的 sheet 名 → 而是在主对话中逐个试错

**代价**：7 轮 × 6 次工具调用 × 25K token × 30 秒。**理想路径仅需 2-3 轮**。

### 1.2 根因分析

| 根因 | 详情 |
|------|------|
| **ask_user 描述过于抽象** | "当任务存在关键不确定性时" — LLM 不知道"6 个候选文件选哪个"是否算"关键不确定性" |
| **系统提示词缺少歧义消解策略** | 工作循环只说"探索→计划→执行"，没有"歧义→澄清"分支 |
| **delegate_to_subagent 描述缺少探查场景** | 仅说"复杂多步任务"，LLM 不会想到用它做轻量探查 |
| **"行动偏好"过强** | 小改后加了"信息不足时用合理假设行动"，在歧义场景反而鼓励了盲猜 |

---

## 二、成熟 Agent 的模式调研

### 2.1 Claude Code: AskUserQuestion

**工具描述（v2.0.77）**：
> Use this tool when you need to ask the user questions during execution. This allows you to:
> 1. Gather user preferences or requirements
> 2. **Clarify ambiguous instructions**
> 3. Get decisions on implementation choices as you work
> 4. **Offer choices to the user about what direction to take**

**关键设计**：
- **结构化选项**：2-4 个选项 + 自动追加 "Other"
- **推荐标记**：第一个选项 + "(Recommended)" 后缀
- **Plan mode 禁用**：计划模式下用于澄清需求，不用于"我的计划好了吗？"
- **非阻塞**（Cursor 2.4）：Cursor 的同类工具可以**不阻塞执行**，Agent 继续做能做的部分，用户回答后合并

**系统提示词中的引导**（Claude Code prompt engineering patterns）：
```
# Asking questions
- **Use the AskUserQuestion tool to clarify with the user**
- Ask about specific implementation choices
- **Clarify any assumptions that could affect the implementation**
- Only proceed with ExitPlanMode after resolving ambiguities
```

### 2.2 Claude Code: Subagent 探查委派

**内置子代理**：
| 名称 | 模型 | 工具 | 用途 |
|------|------|------|------|
| **Explore** | Haiku（快速） | Glob, Grep, Read, Bash | 文件发现、代码搜索、代码库探索 |
| Plan | 继承主模型 | 只读 | 计划模式下的代码库调研 |
| General-purpose | 继承主模型 | 全部 | 复杂研究、多步操作 |

**Explore 子代理的工具描述**：
> "Fast agent specialized for exploring codebases. **When you are searching for a keyword or file and are not confident that you will find the right match in the first few tries, use this agent to perform the search for you.**"

**关键设计**：
- **明确触发条件**："when not confident in first few tries" — 直接对应我们的场景
- **后台执行**：可以用 `Ctrl+B` 放到后台，主对话继续
- **指定彻底程度**："quick" / "medium" / "very thorough"
- **结果摘要回传**：子代理完成后返回结构化摘要，不污染主对话上下文

### 2.3 Cursor 2.4: 非阻塞澄清

**核心创新**：
> "Agents can now ask clarifying questions **without blocking** and continue working while you respond."

- Agent 发出澄清问题后，**继续执行能独立完成的部分**
- 用户回答后，Agent **无缝整合**答案到后续步骤
- 提供"Add more optional details"输入框，用户可以补充问题之外的信息

### 2.4 Spring AI: AskUserQuestionTool

**设计理念**：
> "Traditional AI interactions follow a common pattern: you provide a prompt, the AI makes assumptions, and produces a response. Each assumption creates rework. What if your AI agent could **ask clarifying questions before acting**?"

**工作流**：
```
1. AI generates questions → 构造 2-4 个选项
2. User provides answers → UI 展示选项，收集回答
3. Ask additional questions → 如需要可追问
4. AI continues with context → 基于回答提供定制方案
```

### 2.5 Codex: 停止循环策略

Codex 系统提示词中的关键规则：
> "Avoid excessive looping or repetition; if you find yourself re-reading or re-editing the same files without clear progress, **stop and end the turn with a concise summary and any clarifying questions needed**."

---

## 三、ExcelManus 现状与差距

### 3.1 已有基础设施

| 组件 | 状态 | 差距 |
|------|------|------|
| `ask_user` 工具 | ✅ 完整实现 | 描述过于抽象，LLM 不知何时用 |
| `QuestionFlowManager` | ✅ FIFO 队列 + 解析 | 功能完备，无需改 |
| `delegate_to_subagent` | ✅ 完整实现 | 描述缺少"探查"场景引导 |
| `explorer` 子代理 | ✅ 只读工具集 | 功能匹配，描述需增强 |
| 系统提示词 | ❌ 缺少歧义处理策略 | 工作循环无"澄清"分支 |

### 3.2 差距总结

**核心问题不是缺少工具，而是缺少引导 LLM 使用工具的提示词**。

---

## 四、改进方案

### 4.1 改进 1：增强 `ask_user` 工具描述

**现状**：
```python
ask_user_description = (
    "当任务存在关键不确定性时，向用户发起结构化提问。"
    "可用于框架选择、范围确认、边界条件确认。"
    "触发后会暂停执行，等待用户回答后继续。"
)
```

**改进后**：
```python
ask_user_description = (
    "向用户发起结构化选择题以消除歧义。适用场景："
    "(1) 搜索到多个候选文件/sheet，需要用户确认目标；"
    "(2) 用户指令存在多种合理解读，需要确认意图；"
    "(3) 操作涉及不可逆选择（如覆盖文件），需要用户决策。"
    "规则：已知答案时不要问；选项应具体（如列出实际文件名），不要泛泛而问。"
    "触发后暂停执行，等待用户回答后继续。"
)
```

**改进要点**：
- 从抽象（"关键不确定性"）变为**具体场景枚举**（"多个候选文件"）
- 加入**反模式**（"已知答案时不要问"）
- 强调选项**具体化**（"列出实际文件名"）

### 4.2 改进 2：增强 `delegate_to_subagent` 描述

**现状**：
```python
delegate_description = (
    "把任务委派给 subagent 执行，适用于复杂多步任务、数据探查、批量改写。"
    "当任务需要专门角色（explorer/analyst/writer/coder）时优先调用。"
)
```

**改进后**：
```python
delegate_description = (
    "把任务委派给 subagent 执行。适用场景："
    "(1) 需要批量探查多个文件/sheet 的结构（委派 explorer）；"
    "(2) 需要执行复杂数据分析（委派 analyst）；"
    "(3) 需要批量写入或格式化（委派 writer）；"
    "(4) 需要编写和调试 Python 脚本（委派 coder）。"
    "当搜索结果不确定、需要逐个检查多个目标时，优先委派 explorer 而非自己逐个尝试。"
)
```

### 4.3 改进 3：系统提示词增加"歧义消解"策略

在工具策略章节加入：

```
- 发现多个候选目标（文件、sheet、列名）且无法确定时，用 ask_user 让用户选择，不要逐个猜测。
- 需要批量探查多个文件结构时，委派 explorer 子代理，避免在主对话中逐个试错。
```

### 4.4 改进 4：修正"行动偏好"措辞

当前：
> "信息不足时用合理假设行动，除非真正受阻才提问"

这在**歧义场景**下会鼓励盲猜。改为：

> "信息不足但只有一条合理路径时默认行动；存在多个等概率候选时用 ask_user 澄清。"

---

## 五、理想执行路径对比

### 改进后的 true_example 场景

```
用户：读取"销售明细"工作表的前 10 行数据

轮次 1: search_files("**/*.xlsx") → 找到 6 个文件
轮次 2: 意识到不确定哪个文件有"销售明细" sheet
        → 方案 A: ask_user 让用户选择
        → 方案 B: delegate_to_subagent(explorer, "列出所有 xlsx 文件的 sheet 名称")

方案 A（ask_user）:
  ask_user({
    header: "选择文件",
    text: "找到 6 个 Excel 文件，请选择要读取的文件：",
    options: [
      {label: "销售数据示例.xlsx", description: "44KB，含销售相关数据"},
      {label: "demo_sales_data.xlsx", description: "12KB，销售演示数据"},
      {label: "其他文件", description: "示例数据.xlsx 等其余 4 个文件"}
    ]
  })
  → 用户选择后，直接读取对应文件
  → 总计：2 轮 + 1 次用户交互，~5K token

方案 B（explorer 委派）:
  delegate_to_subagent(
    task="列出以下文件的所有 sheet 名称，找出含有'销售明细'的文件",
    agent_name="explorer"
  )
  → explorer 批量检查所有文件，返回摘要
  → 主 Agent 根据结果直接读取
  → 总计：2 轮主对话 + 后台 explorer，~8K token
```

**对比**：25K token / 7 轮 / 30s → **5-8K token / 2-3 轮 / 10-15s**
