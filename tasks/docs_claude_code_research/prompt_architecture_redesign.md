# ExcelManus 提示词架构大改方案

> **日期**：2026-02-13
> **前置研究**：prompt_engineering_patterns.md、todolist_tool_design.md
> **定位**：如果要从架构层面重新设计 ExcelManus 的提示词系统，应该怎么做

---

## 一、当前架构诊断

### 1.1 提示词组装流程（现状）

```
engine._build_system_prompts(skill_contexts)
│
├── base_prompt                  ← memory.py: _DEFAULT_SYSTEM_PROMPT（~500 token，单一字符串）
├── + access_notice              ← engine._build_access_notice()（权限提示，动态）
├── + skill_contexts[]           ← Skillpack.render_context()（技能指引，动态）
│
└── 输出：[system_msg_1, system_msg_2, ...]  或  [merged_single_msg]
         （取决于 system_message_mode: multi / merge）
```

### 1.2 各层现状

| 层 | 实现方式 | 问题 |
|----|---------|------|
| **核心人格** | 单一 Python 字符串 | 不可独立维护、不可测试、修改需改 .py 文件 |
| **技能注入** | Skillpack `.md` 文件 → render_context | ✅ 已模块化，有预算控制 |
| **权限注入** | engine._build_access_notice() | ✅ 动态，但硬编码在引擎中 |
| **子代理人格** | SubagentConfig.system_prompt 字符串 | 硬编码，无法继承主代理规则 |
| **用户自定义** | ❌ 不存在 | 无 CLAUDE.md / AGENTS.md 等价物 |
| **任务状态注入** | ❌ 不存在 | task_store 有数据但不注入到 prompt |
| **会话上下文感知** | ❌ 不存在 | 不知道当前打开了什么文件 |

### 1.3 与成熟方案的差距

| 维度 | Claude Code | Codex | ExcelManus |
|------|-------------|-------|------------|
| **提示词存储** | 25+ 独立 .md 文件 | 单文件 + AGENTS.md | 单 Python 字符串 |
| **变量替换** | `${TASK_TOOL_NAME}` 等 | 无 | 无 |
| **条件注入** | `${CONDITIONAL_XXX}` | 无 | access_notice（仅 1 处） |
| **用户自定义** | CLAUDE.md（层级覆盖） | AGENTS.md（目录层级） | ❌ |
| **子代理继承** | 共享核心 + 角色特化 | N/A | 完全独立，不继承 |
| **Token 预算** | 各模块独立计数 | 无 | 仅 skill_contexts 有预算 |

---

## 二、大改架构设计

### 2.1 核心理念

借鉴 Claude Code 的**模块化 + 可组合**，Codex 的**用户自定义层**，但适配 ExcelManus 的 Excel 领域特性。

**设计原则**：
1. **提示词即配置**——从 Python 代码中剥离到独立 `.md` 文件
2. **可组合**——每个模块独立，按需组装
3. **可继承**——子代理自动继承主代理的安全/输出规则
4. **用户可覆盖**——项目级 `.md` 文件可定制行为
5. **有预算**——总 token 预算内优先级驱动截断

### 2.2 五层架构

```
┌─────────────────────────────────────────────────┐
│  Layer 5: 末尾锚定（Recency Anchor）             │  ← 最后注入，利用 recency bias
├─────────────────────────────────────────────────┤
│  Layer 4: 动态上下文注入                          │  ← 任务状态、权限、会话感知
├─────────────────────────────────────────────────┤
│  Layer 3: 用户自定义（EXCELMANUS.md）             │  ← 项目级行为覆盖
├─────────────────────────────────────────────────┤
│  Layer 2: 技能上下文（Skillpack contexts）        │  ← 已有机制，增强
├─────────────────────────────────────────────────┤
│  Layer 1: 核心人格（Modular prompt modules）      │  ← 从 .py 迁移到 .md
└─────────────────────────────────────────────────┘
```

### 2.3 Layer 1：模块化核心提示词

#### 目录结构

```
excelmanus/prompts/
├── 00-identity.md           # 身份定位（~50 token）
├── 10-workflow.md           # 工作循环（~100 token）
├── 20-tool-strategy.md      # 工具策略（~120 token）
├── 30-task-management.md    # 任务管理（~100 token）
├── 40-safety.md             # 安全策略（~80 token）
├── 50-output.md             # 输出要求（~80 token）
├── 60-capabilities.md       # 能力范围（~50 token，可动态生成）
└── 90-anchor.md             # 末尾锚定（~30 token）
```

#### 文件格式

```markdown
---
name: tool-strategy
priority: 20
version: "1.0.0"
conditional: false
variables: []
---
## 工具策略
- 参数不足时先读取或询问，不猜测路径和字段名。
- 写入前先读取目标区域，优先使用可逆操作。
...
```

**YAML frontmatter**：
- `name`：模块标识符
- `priority`：数字越小越优先（预算不足时优先保留）
- `version`：版本控制
- `conditional`：是否需要条件判断才注入（如权限提示）
- `variables`：支持的变量替换列表

#### PromptBuilder 核心类

```python
class PromptBuilder:
    """模块化提示词构建器。"""

    def __init__(self, prompts_dir: str, token_budget: int = 2000):
        self._modules: list[PromptModule] = self._load_modules(prompts_dir)
        self._budget = token_budget

    def build(self, context: PromptContext) -> list[str]:
        """按优先级组装提示词，在 token 预算内。

        Args:
            context: 包含当前状态的上下文对象
                - active_skill: 当前激活技能
                - task_state: 任务清单状态
                - access_level: 权限级别
                - user_overrides: 用户自定义内容
        """
        modules = self._select_modules(context)
        modules = self._apply_budget(modules)
        return self._render(modules, context)

    def _select_modules(self, context: PromptContext) -> list[PromptModule]:
        """根据条件过滤模块。"""
        ...

    def _apply_budget(self, modules: list[PromptModule]) -> list[PromptModule]:
        """按优先级在 token 预算内截断。"""
        ...

    def _render(self, modules: list[PromptModule], context: PromptContext) -> list[str]:
        """变量替换并渲染最终文本。"""
        ...
```

#### 与现有代码的集成点

```python
# engine.py 改造
class AgentEngine:
    def __init__(self, ...):
        ...
        self._prompt_builder = PromptBuilder(
            prompts_dir="excelmanus/prompts",
            token_budget=config.system_prompt_token_budget,
        )

    def _build_system_prompts(self, skill_contexts, ...):
        context = PromptContext(
            active_skill=self._active_skill,
            task_state=self._task_store.current,
            access_level="full" if self._full_access_enabled else "restricted",
            skill_contexts=skill_contexts,
            user_overrides=self._user_prompt_overrides,
        )
        return self._prompt_builder.build(context)
```

### 2.4 Layer 2：增强技能上下文（已有，增强）

当前 Skillpack 的 `render_context()` 仅输出工具列表 + 执行指引。可增强：

```markdown
# SKILL.md 增强格式
---
name: data_basic
...
behavioral_hints:
  - "写入前先读取目标区域确认"
  - "批量写入优于逐行调用"
inherits_safety: true          # 自动继承主代理安全规则
---
```

`behavioral_hints` 会被注入到技能上下文中，替代在每个工具描述中重复写相同的规则。

### 2.5 Layer 3：用户自定义（EXCELMANUS.md）

借鉴 Codex 的 `AGENTS.md` 机制：

#### 加载层级

```
~/.excelmanus/EXCELMANUS.md          # 全局配置（用户偏好）
{workspace_root}/EXCELMANUS.md       # 项目级配置（数据领域规则）
{workspace_root}/.excelmanus/EXCELMANUS.md  # 项目隐藏配置
```

后加载的覆盖先加载的（project > user > global）。与 Skillpack 三层覆盖机制一致。

#### 文件格式

```markdown
# EXCELMANUS.md

## 项目规则
- 本项目所有金额字段保留 2 位小数
- 日期格式统一为 YYYY-MM-DD
- 写入前始终备份到 backups/ 目录

## 工具偏好
- 数据清洗优先使用 run_code（本项目数据量大）
- 图表默认保存为 PNG 格式，DPI=150
```

#### 注入方式

与 Codex 一致——作为独立 user-role 消息注入，标记来源：

```
# EXCELMANUS.md 项目规则（来源：{workspace_root}/EXCELMANUS.md）
<INSTRUCTIONS>
...用户自定义内容...
</INSTRUCTIONS>
```

### 2.6 Layer 4：动态上下文注入

#### 4a. 任务状态注入

当前 `task_store` 有数据但不注入到 prompt。大改后：

```python
def _build_task_state_injection(self) -> str:
    """将当前任务清单状态注入 prompt，让 LLM 知道进度。"""
    task_list = self._task_store.current
    if task_list is None:
        return ""
    items = task_list.items
    lines = [f"## 当前任务清单：{task_list.title}"]
    for i, item in enumerate(items):
        icon = {"pending": "⬜", "in_progress": "🔄", "completed": "✅", "failed": "❌"}
        lines.append(f"{icon.get(item.status.value, '?')} #{i} {item.title} [{item.status.value}]")
    progress = task_list.progress_summary()
    lines.append(f"进度：{progress.get('completed', 0)}/{len(items)} 完成")
    return "\n".join(lines)
```

**效果**：LLM 在每一轮都能"看到"任务清单的实时状态，而不是只在创建时知道。这是 Claude Code Tasks API 的核心优势——状态可见性。

#### 4b. 权限状态注入（已有，不变）

`_build_access_notice()` 保持现有逻辑。

#### 4c. 会话上下文感知（新增，可选）

如果 ExcelManus 运行在 IDE 模式（未来），可注入：

```
## 会话上下文
- 最近操作的文件：员工信息表.xlsx（sheet: Sheet1, 100 行 × 8 列）
- 上一步操作：read_excel → 成功
- 累计工具调用：5 次（4 成功 / 1 失败）
```

### 2.7 Layer 5：末尾锚定

最后一个模块，利用 LLM 的 recency bias：

```markdown
# 90-anchor.md
---
name: anchor
priority: 90
---
重要：多步骤任务中始终使用 task_create 和 task_update 追踪进度。遇到不确定时偏向行动。
```

### 2.8 子代理提示词继承

当前子代理完全独立写 `system_prompt`，不继承主代理的安全/输出规则。大改后：

#### 继承模型

```
主代理提示词
├── 00-identity.md      → 子代理替换为自己的身份
├── 40-safety.md        → 子代理继承（inherits_safety: true）
├── 50-output.md        → 子代理继承（inherits_output: true）
└── 子代理专属.md       → 角色特化指引
```

#### 实现方式

```python
# SubagentExecutor._build_system_prompt 改造
def _build_system_prompt(self, config, parent_context):
    parts = []
    # 1. 子代理身份
    parts.append(f"你是子代理 `{config.name}`：{config.description}")
    # 2. 子代理专属指引
    parts.append(config.system_prompt)
    # 3. 继承主代理的安全/输出模块（如果 config 允许）
    if config.inherits_safety:
        parts.append(self._load_module("40-safety.md"))
    if config.inherits_output:
        parts.append(self._load_module("50-output.md"))
    # 4. 主会话上下文
    if parent_context:
        parts.append(f"## 主会话上下文\n{parent_context}")
    return "\n\n".join(parts)
```

**效果**：安全规则和输出规范只维护一份，主代理和子代理自动一致。

---

## 三、组装流程总览

```
engine._build_system_prompts()
│
│  ┌── Layer 1: PromptBuilder 加载 prompts/*.md ──┐
│  │   00-identity.md                               │
│  │   10-workflow.md                               │
│  │   20-tool-strategy.md                          │
│  │   30-task-management.md                        │
│  │   40-safety.md                                 │
│  │   50-output.md                                 │
│  │   60-capabilities.md（动态：当前可用工具列表）   │
│  └────────────────────────────────────────────────┘
│
│  ┌── Layer 2: Skillpack contexts ─────────────────┐
│  │   [Skillpack] data_basic                        │
│  │   [Skillpack] chart_basic                       │
│  │   （已有机制，含 behavioral_hints 增强）          │
│  └────────────────────────────────────────────────┘
│
│  ┌── Layer 3: User overrides ─────────────────────┐
│  │   EXCELMANUS.md（如果存在）                      │
│  └────────────────────────────────────────────────┘
│
│  ┌── Layer 4: Dynamic context ────────────────────┐
│  │   任务清单状态（如果有活跃任务）                   │
│  │   权限提示（如果受限）                            │
│  └────────────────────────────────────────────────┘
│
│  ┌── Layer 5: Recency anchor ─────────────────────┐
│  │   90-anchor.md                                  │
│  └────────────────────────────────────────────────┘
│
└── 输出：[system_msg_1, system_msg_2, ...]
    （在 token_budget 内，按 priority 截断）
```

---

## 四、实施路线图

### Phase 1：基础设施（~2 天）

| 步骤 | 内容 | 文件 |
|------|------|------|
| 1.1 | 创建 `excelmanus/prompts/` 目录，将 `_DEFAULT_SYSTEM_PROMPT` 拆分为 8 个 `.md` 文件 | 新建目录 + 8 个 .md |
| 1.2 | 实现 `PromptModule` 数据模型（解析 frontmatter + body） | 新建 prompt_builder.py |
| 1.3 | 实现 `PromptBuilder` 核心类（加载、选择、预算、渲染） | prompt_builder.py |
| 1.4 | 改造 `engine._build_system_prompts()` 使用 PromptBuilder | engine.py |
| 1.5 | 保持 `_DEFAULT_SYSTEM_PROMPT` 作为 fallback（零破坏迁移） | memory.py |

### Phase 2：动态注入（~1 天）

| 步骤 | 内容 | 文件 |
|------|------|------|
| 2.1 | 实现任务状态注入（task_state → prompt 段落） | engine.py 或 prompt_builder.py |
| 2.2 | 将 `_build_access_notice()` 迁移为条件模块 | prompts/conditional-access.md |

### Phase 3：用户自定义层（~1 天）

| 步骤 | 内容 | 文件 |
|------|------|------|
| 3.1 | 实现 EXCELMANUS.md 加载器（三层扫描） | 新建 user_prompt.py |
| 3.2 | 注入到 PromptBuilder 的 Layer 3 | prompt_builder.py |

### Phase 4：子代理继承（~1 天）

| 步骤 | 内容 | 文件 |
|------|------|------|
| 4.1 | SubagentConfig 增加 `inherits_safety`、`inherits_output` 字段 | models.py |
| 4.2 | SubagentExecutor._build_system_prompt 改造为继承模式 | executor.py |

### Phase 5：Skillpack 增强（~0.5 天）

| 步骤 | 内容 | 文件 |
|------|------|------|
| 5.1 | SKILL.md frontmatter 增加 `behavioral_hints` 字段 | models.py, frontmatter.py |
| 5.2 | render_context() 渲染 behavioral_hints | models.py |

---

## 五、风险与决策点

### 5.1 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 拆分后行为回归 | 中 | Phase 1 保留 fallback，A/B 对比测试 |
| Token 预算过紧 | 低 | 默认 2000 token，可配置，128K 窗口下影响极小 |
| EXCELMANUS.md 注入恶意指令 | 中 | 加入长度限制 + 内容过滤（与 Codex 一致） |
| 子代理继承导致提示过长 | 低 | 继承模块有独立 token 预算 |

### 5.2 关键决策点

**Q1：是否需要变量替换？**

Claude Code 用 `${TASK_TOOL_NAME}` 是因为工具名可能变化。ExcelManus 工具名稳定，初期可不实现，后续按需加。

**Q2：multi-system-message 还是 merge？**

保持现有 `system_message_mode: auto` 机制。模块化不改变最终注入方式，只改变组装过程。

**Q3：是否迁移子代理 system_prompt 到 .md 文件？**

建议不迁移。子代理提示词短且与代码紧耦合（工具列表、权限模式），保持在 Python 中更灵活。仅通过继承机制共享安全/输出规则。

**Q4：EXCELMANUS.md 的安全边界？**

参考 Codex：
- 最大 2000 字符
- 不允许覆盖安全策略（40-safety.md 不可被用户覆盖）
- 明确标记来源："以下规则来自项目配置"

---

## 六、与当前小改的关系

| 维度 | 小改（已完成） | 大改（本方案） |
|------|-------------|-------------|
| **改动范围** | 6 个文件，~40 行 | 新建 10+ 文件，改造 5+ 现有文件 |
| **工作量** | 0.5 天 | 5-6 天 |
| **破坏性** | 零（纯追加） | 中（需要迁移 + 测试） |
| **收益** | 行为引导提升 | 架构可维护性 + 可扩展性 + 用户自定义 |

**建议**：小改已经让系统提示词达到"够用"水平（~500 token，关键模式对齐）。大改应在以下场景启动：
1. 需要支持用户自定义行为（EXCELMANUS.md）
2. 提示词膨胀到 >1500 token，维护成本上升
3. 子代理数量增多，规则一致性成为问题
