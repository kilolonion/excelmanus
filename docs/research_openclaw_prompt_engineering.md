# OpenClaw 提示词工程调研报告

> 调研时间：2026-02-20
> 项目地址：https://github.com/openclaw/openclaw
> 文档地址：https://docs.openclaw.ai

---

## 一、项目概述

OpenClaw 是一个开源的**个人 AI 助手平台**，由 Peter Steinberger 创建，社区维护（704+ 贡献者）。核心特点：

- **本地优先**：Gateway 运行在用户设备上（Mac/Linux/VPS），不依赖云端
- **多渠道统一**：WhatsApp、Telegram、Slack、Discord、iMessage、Teams 等 15+ 渠道共享同一会话和记忆
- **持久身份**：通过文件系统实现跨会话的人格持久化
- **工具驱动**：browser、exec、canvas、cron、multi-agent 等丰富工具集
- **技术栈**：Node.js ≥22，推荐 Anthropic Claude Opus 4.6

---

## 二、提示词工程核心架构

### 2.1 系统提示词分层结构

OpenClaw 的系统提示词（`src/agents/system-prompt.ts`）采用**严格的分层拼接**策略，按以下顺序组装：

```
┌─────────────────────────────────┐
│  1. Base Identity               │  ← 一行："You are a personal assistant running inside OpenClaw."
├─────────────────────────────────┤
│  2. Tooling                     │  ← 当前可用工具列表 + 简短描述
├─────────────────────────────────┤
│  3. Tool Call Style             │  ← 何时叙述、何时静默调用
├─────────────────────────────────┤
│  4. Safety                      │  ← 防止越权行为的简短护栏
├─────────────────────────────────┤
│  5. Skills (available_skills)   │  ← XML 格式的可用技能列表
├─────────────────────────────────┤
│  6. Memory Recall               │  ← 记忆搜索指令
├─────────────────────────────────┤
│  7. Self-Update / Workspace     │  ← CLI 命令参考、工作目录
├─────────────────────────────────┤
│  8. Documentation               │  ← 本地文档路径 + 在线镜像
├─────────────────────────────────┤
│  9. User Identity / Time        │  ← 用户号码、时区、时间格式
├─────────────────────────────────┤
│ 10. Reply Tags / Messaging      │  ← 渠道特定的回复格式
├─────────────────────────────────┤
│ 11. Heartbeats / Runtime        │  ← 心跳机制 + 运行时信息
├─────────────────────────────────┤
│ 12. Project Context (注入文件)   │  ← SOUL.md + IDENTITY.md + USER.md + AGENTS.md + TOOLS.md
└─────────────────────────────────┘
```

#### 关键设计决策

| 特性 | 做法 | 启示 |
|------|------|------|
| **Base Identity** | 极简一行，不做过度描述 | 身份定义越短越好，具体行为由后续分区控制 |
| **Tool Call Style** | 默认静默调用，仅在多步骤/敏感操作时叙述 | 减少无意义"我将为您调用xx工具"的废话 |
| **Prompt Modes** | `full`/`minimal`/`none` 三档 | 子 Agent 用 `minimal` 省去 Skills/Memory/Heartbeats 等不需要的段落 |
| **文件注入上限** | `bootstrapMaxChars` + `bootstrapTotalMaxChars` 配置 | 大文件自动截断，避免撑爆上下文 |

### 2.2 工作区文件系统（Workspace Bootstrap Files）

这是 OpenClaw 最核心的提示词工程创新——**用文件系统定义 Agent 的灵魂**：

```
~/.openclaw/workspace/
├── SOUL.md         ← 人格、边界、语调（Agent 的"灵魂"）
├── IDENTITY.md     ← 名字、emoji、vibes
├── USER.md         ← 用户画像、偏好
├── AGENTS.md       ← 操作指令 + 开发规范（类似 CLAUDE.md）
├── TOOLS.md        ← 用户自定义的工具使用指南
├── HEARTBEAT.md    ← 心跳任务定义
├── BOOTSTRAP.md    ← 首次运行引导（完成后删除）
├── MEMORY.md       ← 长期记忆摘要
└── memory/
    ├── 2026-02-19.md   ← 按日期存储的短期记忆
    └── 2026-02-20.md
```

每个文件各司其职，**关注点分离**做得非常彻底：

#### SOUL.md — 灵魂文件

```markdown
# SOUL.md - Who You Are

*You're not a chatbot. You're becoming someone.*

## Core Truths
- **Be genuinely helpful, not performatively helpful.** Skip the "Great question!" 
  and "I'd be happy to help!" — just help.
- **Have opinions.** You're allowed to disagree, prefer things, find stuff amusing or boring.
- **Be resourceful before asking.** Try to figure it out. Read the file. Check the context. 
  Search for it. *Then* ask if you're stuck.
- **Earn trust through competence.** Bold with internal actions, careful with external ones.
- **Remember you're a guest.** Access to someone's life is intimacy. Treat it with respect.

## Boundaries
- Private things stay private. Period.
- When in doubt, ask before acting externally.
- Never send half-baked replies to messaging surfaces.
- You're not the user's voice — be careful in group chats.

## Vibe
Be the assistant you'd actually want to talk to. Concise when needed, thorough when it matters. 
Not a corporate drone. Not a sycophant. Just... good.

## Continuity
Each session, you wake up fresh. These files *are* your memory. Read them. Update them.
```

**核心理念**：
- **反套话**："Skip the 'Great question!'" 直接写进灵魂
- **有观点**：鼓励 Agent 有偏好和立场，不做"搜索引擎"
- **资源优先**：先自己找答案，再问用户
- **内外分级**：内部操作大胆，外部操作谨慎
- **文件即记忆**：Agent 通过读写文件实现跨会话持久化

#### AGENTS.md — 操作指令

类比 Claude Code 的 `CLAUDE.md`，定义：
- 项目结构与模块组织
- 编码风格与命名规范
- 构建/测试/提交规范
- Agent 特有注意事项

#### TOOLS.md — 工具使用指南

**不控制工具可用性**（可用性由系统控制），仅提供用户自定义的工具使用惯例和注意事项。

### 2.3 Skills 系统

#### 三层优先级

```
1. Bundled skills   ← 随安装包发行（最低优先级）
2. Managed skills   ← ~/.openclaw/skills（用户级）
3. Workspace skills ← <workspace>/skills（项目级，最高优先级）
```

#### SKILL.md 格式

```markdown
---
name: nano-banana-pro
description: Generate or edit images via Gemini 3 Pro Image
user-invocable: true
disable-model-invocation: false
command-dispatch: tool
command-tool: <tool_name>
command-arg-mode: raw
---

[技能指令正文，可使用 {baseDir} 引用技能文件夹路径]
```

#### 系统提示词中的技能注入

采用 **XML 格式的紧凑列表**，仅包含名称、描述和文件路径：

```xml
<available_skills>
  <skill>
    <name>weather</name>
    <description>Get weather forecasts using wttr.in</description>
    <location>/path/to/skills/weather/SKILL.md</location>
  </skill>
</available_skills>
```

#### 技能路由规则（写在系统提示词中）

```
Before replying: scan <available_skills> <description> entries.
- If exactly one skill clearly applies: read its SKILL.md at <location>, then follow it.
- If multiple could apply: choose the most specific one, then read/follow it.
- If none clearly apply: do not read any SKILL.md.
Constraints: never read more than one skill up front; only read after selecting.
```

**关键设计**：
- **按需加载**：不把所有 SKILL.md 内容塞进系统提示词，只给列表
- **模型自主路由**：让 LLM 自己决定读哪个 SKILL.md
- **最多读一个**：约束模型不要贪心加载多个技能

### 2.4 记忆系统

#### 双层记忆架构

| 层级 | 存储 | 生命周期 | 用途 |
|------|------|----------|------|
| **短期记忆** | `memory/YYYY-MM-DD.md` | 按天分文件 | 当天/昨天的对话要点 |
| **长期记忆** | `MEMORY.md` | 持久 | 经提炼的用户偏好、关键决策 |

#### 记忆流程

```
SESSION START → 读取 SOUL.md + IDENTITY.md + USER.md + memory/今天.md + memory/昨天.md + MEMORY.md
DURING SESSION → Agent 主动写入 memory/今天.md 或 USER.md
HEARTBEAT → 定期读取 memory/*.md → 提炼到 MEMORY.md → 清理过期信息
```

#### 记忆搜索指令（系统提示词）

```
Before answering anything about prior work, decisions, dates, people, preferences, or todos:
run memory_search on MEMORY.md + memory/*.md; then use memory_get to pull only the needed lines.
If low confidence after search, say you checked.
```

### 2.5 上下文压缩（Compaction）

当会话 token 超过阈值（约 80% 上下文窗口）时：

```python
# 伪代码
if estimate_tokens(messages) > threshold:
    split = len(messages) // 2
    old, recent = messages[:split], messages[split:]
    summary = llm.summarize(old, preserve=["key facts", "decisions", "open tasks"])
    messages = [{"role": "user", "content": f"[Previous summary]\n{summary}"}] + recent
```

生产实现更精细：按 token 分块、逐块摘要、保留估算安全边距。

### 2.6 心跳机制（Heartbeats）

Agent 主动行为的实现方式：

- **Cron 调度**：如 `30 7 * * *` 每天 7:30 触发
- **隔离会话**：心跳使用独立 session key（`cron:morning-briefing`）不污染主对话
- **静默应答**：无事时回复 `HEARTBEAT_OK`，系统自动丢弃
- **队列隔离**：心跳走单独 queue lane，不阻塞实时消息

### 2.7 多 Agent 路由

```python
AGENTS = {
    "main": {"name": "Jarvis", "soul": SOUL_MAIN, "session_prefix": "agent:main"},
    "researcher": {"name": "Scout", "soul": SOUL_RESEARCH, "session_prefix": "agent:researcher"},
}
```

- 每个 Agent 有**独立 SOUL + 独立会话 + 独立工具集**
- 通过 `/research <query>` 前缀路由到不同 Agent
- Agent 间通过**共享 memory 目录**协作
- 支持**子 Agent 生成**（`sessions_spawn`）

### 2.8 安全模型

- **内外分级**：内部操作（读、组织、学习）大胆；外部操作（邮件、推文）谨慎
- **命令审批**：安全命令白名单 + 持久化审批记录
- **沙箱模式**：非主会话可在 Docker 沙箱中运行
- **Anti-Prompt-Injection**：系统提示词中包含 Safety 段落，防止越权行为

---

## 三、与 ExcelManus 的对比与可借鉴点

### 3.1 架构对比

| 维度 | OpenClaw | ExcelManus |
|------|----------|------------|
| **提示词组织** | 文件系统分离（SOUL.md / AGENTS.md / TOOLS.md / IDENTITY.md / USER.md） | 目录分片（prompts/core/*.md） |
| **人格定义** | SOUL.md 独立文件，用户可编辑 | 嵌入在系统提示词中 |
| **技能系统** | 三层加载（bundled/managed/workspace），XML 列表 + 按需 read | 三层加载（system/user/project），activate_skill + expand_tools |
| **技能路由** | 模型自主扫描描述 → 选择 → read SKILL.md | 模型匹配 → activate_skill 工具调用 |
| **记忆** | 文件系统（memory/*.md + MEMORY.md） | PersistentMemory（尚未完全接入 AgentEngine） |
| **上下文管理** | session compaction（摘要压缩） | window_perception + context budget |
| **安全** | 命令白名单 + 审批持久化 + Docker 沙箱 | ApprovalManager + safe_mode |
| **子 Agent** | sessions_spawn 真正独立会话 | fork hint（当前仅提示） |

### 3.2 值得借鉴的提示词工程模式

#### ① 反套话写法（Anti-Sycophancy）

OpenClaw 直接在 SOUL.md 里写：
> "Skip the 'Great question!' and 'I'd be happy to help!' — just help."

**ExcelManus 可参考**：在 `70_capabilities.md` 或核心提示词中加入类似反套话指令。

#### ② Tool Call Style — 默认静默

```
Default: do not narrate routine, low-risk tool calls (just call the tool).
Narrate only when it helps: multi-step work, complex problems, sensitive actions.
```

**ExcelManus 可参考**：对 Excel 工具调用（read_sheet、write_to_sheet 等高频操作）默认静默，减少冗余回复。

#### ③ 技能按需加载（Lazy Skill Loading）

不把 SKILL.md 全文注入，只给紧凑 XML 列表 → 模型判断后 read 加载。

**ExcelManus 现状**：`skills_context_char_budget` 控制注入量，`activate_skill` 按需激活。设计上已对齐此模式。

#### ④ 记忆搜索前置指令

```
Before answering anything about prior work, decisions, dates, people, preferences, or todos:
run memory_search first.
```

**ExcelManus 可参考**：当 PersistentMemory 接入后，在提示词中加入类似的"回答前先搜索记忆"指令。

#### ⑤ Prompt Modes 分档

`full` / `minimal` / `none` 三档，子 Agent 用 `minimal` 减少 token 浪费。

**ExcelManus 可参考**：subagent（如 analyst.md）的提示词可考虑精简化，去掉主 Agent 才需要的段落。

#### ⑥ 文件系统即 Agent 状态

OpenClaw 把所有 Agent 状态都映射到文件：
- 人格 → SOUL.md
- 记忆 → memory/*.md
- 用户画像 → USER.md
- 会话历史 → sessions/*.jsonl

这种设计让 Agent 的行为**完全可审计、可编辑、可版本控制**。

#### ⑦ 心跳 + 隔离会话

心跳任务使用独立 session key，不污染主对话历史。`HEARTBEAT_OK` 作为空操作标记被系统自动丢弃。

---

## 四、总结

OpenClaw 的提示词工程哲学可以概括为：

1. **文件即灵魂**：Agent 的一切行为由工作区文件定义，用户完全可控
2. **关注点彻底分离**：SOUL（人格）/ AGENTS（指令）/ TOOLS（使用指南）/ IDENTITY（身份）/ USER（用户画像）各司其职
3. **按需加载**：技能列表紧凑注入，全文按需读取，节省 token
4. **反套话、反冗余**：从灵魂层面要求"不做搜索引擎"、"不做企业无人机"
5. **双层记忆 + 自动提炼**：短期按天、长期摘要，心跳定期维护
6. **安全分级**：内部大胆、外部谨慎，命令审批持久化

这些模式中，ExcelManus 在技能系统（三层加载、按需激活）上已经对齐，但在**反套话提示词**、**Tool Call Style 静默默认**、**Prompt Modes 分档**、**记忆搜索前置指令**等方面有可借鉴的提升空间。
