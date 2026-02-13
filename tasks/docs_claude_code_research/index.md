# Claude Code 调研与 ExcelManus 借鉴开发方案

> **任务类型**：调研 + 方案设计
> **创建日期**：2025-07-14
> **状态**：✅ 完成

---

## 一、Claude Code 核心架构调研

### 1.1 整体架构：Agentic Loop

Claude Code 的核心是一个 **Agentic Loop（代理循环）**：

```
用户输入 → LLM 推理 → 工具调用 → 结果反馈 → LLM 继续推理 → ... → 最终回复
```

与 ExcelManus 当前的 Tool Calling 循环本质相同，但 Claude Code 在以下维度做了更深入的设计：

| 维度 | Claude Code | ExcelManus 现状 |
|------|-------------|-----------------|
| **工具体系** | 内置 9 种核心工具 + MCP 扩展 | 12 种 Excel 专用工具 + registry |
| **技能系统** | SKILL.md 文件 + 三层作用域 | Skillpack YAML frontmatter + 三层加载 |
| **子代理** | 真正独立执行的 Subagent | 仅 fork hint，未真正执行 |
| **Hook 生命周期** | 14 种事件 + 3 种 Hook 类型 | 6 种 EventType，无 Hook 拦截 |
| **权限系统** | 细粒度 allow/deny/ask 决策 | /fullAccess 会话级开关 |
| **持久记忆** | MEMORY.md + 三作用域 | MEMORY.md + 单作用域 |
| **会话管理** | 自动压缩 + 分支 + 恢复 | ConversationMemory 滑窗 |

### 1.2 Hook 生命周期（重点借鉴）

Claude Code 定义了 **14 种 Hook 事件**，覆盖完整的代理生命周期：

```
SessionStart → UserPromptSubmit → PreToolUse → PermissionRequest
→ PostToolUse / PostToolUseFailure → Notification
→ SubagentStart → SubagentStop → Stop
→ TeammateIdle → TaskCompleted → PreCompact → SessionEnd
```

**三种 Hook 处理器类型**：
- **command**：执行 shell 脚本，通过 stdin 接收 JSON 输入，stdout 输出 JSON 决策
- **prompt**：调用 LLM 进行判断（如多条件 Stop 检查）
- **agent**：启动子代理执行复杂逻辑

**PreToolUse 决策控制**（最核心的创新）：
```json
{
  "hookSpecificOutput": {
    "permissionDecision": "allow | deny | ask",
    "permissionDecisionReason": "原因说明",
    "updatedInput": { "field": "修改后的值" },
    "additionalContext": "注入给 LLM 的额外上下文"
  }
}
```

Hook 可以：
- **放行**（allow）：跳过权限确认
- **拦截**（deny）：阻止工具执行并告知 LLM 原因
- **升级**（ask）：交给用户决定
- **修改输入**（updatedInput）：在工具执行前篡改参数
- **注入上下文**（additionalContext）：给 LLM 补充信息

### 1.3 Subagent 系统（重点借鉴）

Claude Code 的子代理是**真正独立执行**的：

**内置子代理**：
- **Explore**：Haiku 小模型 + 只读工具，用于文件发现和代码搜索
- **Plan**：继承主模型 + 只读工具，用于规划
- **General-purpose**：继承主模型 + 全部工具，用于复杂操作

**自定义子代理配置**（Markdown frontmatter）：
```yaml
---
name: data-analyst
description: 分析 Excel 数据并生成报告
tools: Read, Bash, Write
disallowedTools: Edit
permissionMode: acceptEdits
memory: project
skills:
  - data-conventions
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/validate-query.sh"
---
分析数据时遵循以下步骤...
```

**关键特性**：
- **前台/后台执行**：前台阻塞主对话，后台并发执行
- **工具限制**：每个子代理可独立配置允许/禁止的工具
- **权限模式**：default / acceptEdits / dontAsk / plan / delegate
- **持久记忆**：子代理有独立的 MEMORY.md，跨会话积累知识
- **自动委派**：LLM 根据子代理 description 自动选择合适的子代理
- **会话恢复**：子代理对话独立存储，可恢复上下文

### 1.4 Skills 系统

Claude Code 的 Skills 与 ExcelManus 的 Skillpack 高度相似，但有几个关键差异：

| 特性 | Claude Code Skills | ExcelManus Skillpack |
|------|-------------------|---------------------|
| **文件格式** | SKILL.md (Markdown) | SKILL.md (Markdown) |
| **作用域** | user / project / plugin | system / user / project |
| **动态上下文** | `!command` 语法注入命令输出 | 无 |
| **子代理执行** | `context: fork` + `agent: Explore` | `context: fork`（未实现） |
| **Hook 绑定** | 每个 Skill 可定义自己的 Hooks | 无 |
| **参数替换** | `$ARGUMENTS` / `$ARGUMENTS[N]` / `$N` | `$ARGUMENTS` |
| **模型选择** | 每个 Skill 可指定独立模型 | 无 |

**动态上下文注入**（新特性）：
```markdown
---
name: pr-summary
context: fork
agent: Explore
---
## PR 上下文
- PR diff: !`gh pr diff`
- 变更文件: !`gh pr diff --name-only`

总结这个 PR...
```

`!command` 语法在 Skill 加载时立即执行命令，将输出替换到 Skill 内容中。

### 1.5 权限系统

Claude Code 的权限是**多层级、细粒度**的：

```
managed (企业管理) > project (.claude/settings.json) > user (~/.claude/settings.json)
```

权限规则支持 glob 匹配：
```json
{
  "permissions": {
    "allow": ["Read", "Grep", "Glob", "Bash(npm test *)"],
    "deny": ["Bash(rm *)", "Write(/etc/*)", "Task(deploy-agent)"]
  }
}
```

---

## 二、ExcelManus 现状评估

### 2.1 已实现（✅）
- 三层 Skillpack 加载与覆盖优先级（project > user > system）
- 斜杠命令直连 + fallback 路由
- `disable_model_invocation` 与 `user_invocable` 过滤
- `$ARGUMENTS` 参数替换
- `skills_context_char_budget` 预算控制
- 工具结果截断（output_guard）
- `/fullAccess` 会话级代码权限
- API `external_safe_mode`
- Accept 门禁与变更审计（approval.py）
- 持久记忆 MEMORY.md（单作用域）
- 会话级 EventType 事件系统
- FileAccessGuard 路径安全校验

### 2.2 未实现（❌）
- 完整 Hook 生命周期与规则引擎
- PersistentMemory 到 AgentEngine 的读写接入（部分完成）
- 真正 Subagent 执行（当前仅 fork hint）
- 多作用域 settings 权限矩阵
- 动态上下文注入（`!command` 语法）
- 子代理独立持久记忆
- 子代理工具限制与权限模式
- Skill 级别的 Hook 绑定
- 每 Skill 独立模型选择

---

## 三、借鉴开发方案

### 优先级排序原则
1. **ROI 最高**：对 Excel Agent 场景提升最大的功能优先
2. **依赖关系**：被其他功能依赖的基础设施优先
3. **复杂度递增**：从简单到复杂，逐步构建

### Phase 1：Hook 生命周期引擎（基础设施，P0）

**目标**：建立可扩展的 Hook 系统，为后续所有功能提供拦截/增强能力。

**借鉴点**：Claude Code 的 14 种事件 + command/prompt/agent 三种处理器

**ExcelManus 适配设计**：

```python
# excelmanus/hooks/models.py
class HookEvent(Enum):
    """Hook 事件类型 — 适配 Excel Agent 场景"""
    SESSION_START = "SessionStart"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"          # 核心：工具执行前拦截
    POST_TOOL_USE = "PostToolUse"        # 核心：工具执行后处理
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    PRE_APPROVAL = "PreApproval"         # ExcelManus 特有：审批前
    POST_APPROVAL = "PostApproval"       # ExcelManus 特有：审批后
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    STOP = "Stop"
    SESSION_END = "SessionEnd"

class HookDecision(Enum):
    ALLOW = "allow"      # 放行，跳过权限确认
    DENY = "deny"        # 拦截，告知 LLM 原因
    ASK = "ask"          # 升级到用户确认
    CONTINUE = "continue"  # 默认：继续正常流程

@dataclass
class HookResult:
    decision: HookDecision = HookDecision.CONTINUE
    reason: str = ""
    updated_input: dict | None = None       # 修改工具输入
    additional_context: str = ""            # 注入 LLM 上下文
    block_reason: str = ""                  # Stop 事件的阻止原因

class HookHandler(Protocol):
    """Hook 处理器协议"""
    async def execute(self, event: HookEvent, payload: dict) -> HookResult: ...

# 三种处理器实现
class CommandHookHandler:    # 执行 shell 脚本
class PromptHookHandler:     # 调用 LLM 判断
class CallbackHookHandler:   # Python 回调（ExcelManus 特有，比 shell 更高效）
```

**实现要点**：
- 在 `AgentEngine._execute_single_tool()` 前后插入 `PreToolUse` / `PostToolUse` Hook
- Hook 配置支持在 Skillpack frontmatter 中定义（与 Claude Code 一致）
- 保留现有 `EventCallback` 作为观察者，Hook 作为拦截者
- `PreApproval` / `PostApproval` 是 ExcelManus 特有的，与 `approval.py` 集成

**工作量估算**：3-5 天

---

### Phase 2：真正的 Subagent 执行（核心能力，P0）

**目标**：将当前的 fork hint 升级为真正独立执行的子代理。

**借鉴点**：Claude Code 的 Explore/Plan/General-purpose 三种内置子代理 + 自定义子代理

**ExcelManus 适配设计**：

```python
# excelmanus/subagent/models.py
@dataclass
class SubagentConfig:
    name: str
    description: str
    model: str | None = None          # None 表示继承主模型
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    permission_mode: str = "default"  # default / acceptEdits / readOnly
    max_iterations: int = 6
    skills: list[str] = field(default_factory=list)  # 预加载的 Skill
    memory_scope: str | None = None   # user / project / None
    hooks: dict = field(default_factory=dict)

# excelmanus/subagent/executor.py
class SubagentExecutor:
    """子代理执行器：创建独立的 AgentEngine 实例运行子任务。"""

    async def run(
        self,
        config: SubagentConfig,
        prompt: str,
        parent_context: dict | None = None,
    ) -> SubagentResult:
        """在独立上下文中执行子代理任务。"""
        ...
```

**内置子代理（适配 Excel 场景）**：

| 子代理 | 模型 | 工具 | 用途 |
|--------|------|------|------|
| **Explorer** | 小模型 | read_excel, list_sheets, get_file_info, search_files | 数据探索 |
| **Analyst** | 主模型 | read_excel, analyze_data, filter_data | 数据分析 |
| **Writer** | 主模型 | 全部工具 | 数据写入与格式化 |
| **Coder** | 主模型 | execute_code, read_excel | 代码执行 |

**实现要点**：
- 子代理创建独立的 `AgentEngine` 实例，拥有独立的 `ConversationMemory`
- 子代理的工具注册表是主注册表的**受限视图**（通过 allowed/disallowed 过滤）
- 子代理执行完毕后，将结果摘要返回主对话
- 支持前台（阻塞）和后台（异步）两种执行模式
- 子代理的 Hook 独立于主会话

**工作量估算**：5-8 天

---

### Phase 3：细粒度权限矩阵（安全增强，P1）

**目标**：替换当前的 `/fullAccess` 二元开关，实现 Claude Code 级别的细粒度权限控制。

**借鉴点**：Claude Code 的 allow/deny glob 规则 + 多层级配置

**ExcelManus 适配设计**：

```python
# excelmanus/permissions.py
@dataclass
class PermissionRule:
    pattern: str          # glob 模式，如 "write_excel(*.xlsx)"
    decision: str         # "allow" | "deny" | "ask"

@dataclass
class PermissionConfig:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)

class PermissionManager:
    """多层级权限管理器。"""

    def __init__(self):
        self._layers: list[tuple[str, PermissionConfig]] = []
        # 优先级：project > user > system（与 Skillpack 一致）

    def check(self, tool_name: str, arguments: dict) -> str:
        """返回 "allow" | "deny" | "ask"。"""
        ...
```

**配置文件位置**：
- **system**：`excelmanus/permissions.json`（内置默认规则）
- **user**：`~/.excelmanus/settings.json`
- **project**：`.excelmanus/settings.json`

**默认规则示例**：
```json
{
  "permissions": {
    "allow": [
      "read_excel", "list_sheets", "get_file_info",
      "analyze_data", "filter_data", "search_files"
    ],
    "deny": [
      "execute_code"
    ]
  }
}
```

**与 Hook 系统集成**：权限检查作为 `PreToolUse` Hook 的内置处理器。

**工作量估算**：3-4 天

---

### Phase 4：动态上下文注入（Skill 增强，P1）

**目标**：支持 Skill 中的 `!command` 语法，在加载时动态注入命令输出。

**借鉴点**：Claude Code 的 `!` 前缀命令执行

**ExcelManus 适配设计**：

```python
# excelmanus/skillpacks/dynamic_context.py
import re
import subprocess

_DYNAMIC_CMD_PATTERN = re.compile(r"!\`([^`]+)\`")

async def resolve_dynamic_context(
    skill_content: str,
    workspace_root: str,
    timeout: float = 10.0,
) -> str:
    """将 !`command` 占位符替换为命令执行结果。"""
    ...
```

**Excel 场景应用示例**：
```markdown
---
name: analyze-sales
description: 分析销售数据
context: fork
agent: Analyst
---
## 当前工作目录文件
!`ls *.xlsx`

## 任务
分析 $ARGUMENTS 中的销售数据...
```

**安全约束**：
- 命令执行受 `FileAccessGuard` 限制
- 超时控制（默认 10 秒）
- `external_safe_mode` 开启时禁用动态上下文

**工作量估算**：2-3 天

---

### Phase 5：子代理持久记忆（知识积累，P2）

**目标**：每个子代理拥有独立的 MEMORY.md，跨会话积累领域知识。

**借鉴点**：Claude Code 的 `memory: user | project | local` 三作用域

**ExcelManus 适配设计**：

```
~/.excelmanus/agent-memory/
├── explorer/
│   └── MEMORY.md      # Explorer 子代理的知识积累
├── analyst/
│   └── MEMORY.md      # Analyst 子代理的数据分析经验
└── coder/
    └── MEMORY.md      # Coder 子代理的代码模式库

.excelmanus/agent-memory/   # 项目级子代理记忆
└── analyst/
    └── MEMORY.md      # 该项目特有的分析经验
```

**实现要点**：
- 复用现有 `PersistentMemory` 类，为每个子代理创建独立实例
- 子代理启动时自动加载前 200 行 MEMORY.md
- 子代理结束时通过 `MemoryExtractor` 提取新知识
- 支持用户/项目两种作用域

**工作量估算**：2-3 天

---

### Phase 6：Skill 级 Hook 绑定 + 模型选择（P2）

**目标**：每个 Skill 可定义自己的 Hook 和独立模型。

**Skillpack frontmatter 扩展**：
```yaml
---
name: safe-data-export
description: 安全导出数据
model: gpt-4o-mini              # 独立模型选择
allowed-tools: read_excel, write_excel
hooks:
  PreToolUse:
    - matcher: "write_excel"
      hooks:
        - type: callback
          handler: "validate_export_path"
  PostToolUse:
    - matcher: "write_excel"
      hooks:
        - type: command
          command: "./scripts/log-export.sh"
---
```

**工作量估算**：3-4 天

---

## 四、实施路线图

```
Phase 1 (Hook 引擎)     ████████░░  3-5天   ← 基础设施，所有后续功能依赖
Phase 2 (Subagent)       ██████████░ 5-8天   ← 核心能力提升
Phase 3 (权限矩阵)      ██████░░░░  3-4天   ← 安全增强
Phase 4 (动态上下文)     ████░░░░░░  2-3天   ← Skill 增强
Phase 5 (子代理记忆)     ████░░░░░░  2-3天   ← 知识积累
Phase 6 (Skill Hook)     ██████░░░░  3-4天   ← 完善生态
                                    ─────────
                         总计约      18-27天
```

**依赖关系**：
```
Phase 1 (Hook) ─┬─→ Phase 2 (Subagent) ──→ Phase 5 (子代理记忆)
                ├─→ Phase 3 (权限矩阵)
                ├─→ Phase 4 (动态上下文)
                └─→ Phase 6 (Skill Hook)
```

---

## 五、关键设计决策

### 5.1 为什么 Hook 优先于 Subagent？

Claude Code 的 Subagent 系统**深度依赖** Hook：
- 子代理的工具限制通过 `PreToolUse` Hook 实现
- 子代理的权限模式通过 Hook 决策控制
- 子代理启停通过 `SubagentStart` / `SubagentStop` Hook 通知

没有 Hook 基础设施，Subagent 的安全性和可控性无法保证。

### 5.2 为什么用 CallbackHookHandler 而非纯 shell？

ExcelManus 是 Python 项目，Excel 操作涉及大量二进制数据：
- **性能**：Python 回调比 shell 进程快 10-100 倍
- **类型安全**：Python 回调可以直接操作 openpyxl 对象
- **可测试性**：Python 回调更容易单元测试

同时保留 `CommandHookHandler` 支持 shell 脚本，兼容 Claude Code 的生态。

### 5.3 为什么不照搬 Claude Code 的 Agent Teams？

Claude Code 的 Agent Teams（多代理协作）适用于大型代码库的并行开发场景。
ExcelManus 的核心场景是**单文件 Excel 操作**，并行度有限。
当前阶段聚焦于 **单主代理 + 多子代理** 的层级结构即可。

### 5.4 ExcelManus 特有的创新点

| 特性 | 说明 |
|------|------|
| **PreApproval Hook** | Excel 写操作前的审批拦截，Claude Code 没有 |
| **CallbackHookHandler** | Python 原生回调，比 shell 更高效 |
| **Excel 感知的权限规则** | `write_excel(*.xlsx)` 支持文件路径 glob |
| **数据探索子代理** | 专为 Excel 数据探索优化的 Explorer |

---

## 六、总结

Claude Code 的核心设计哲学是 **"可组合的代理基础设施"**：
- Hook 提供**拦截与增强**能力
- Subagent 提供**分治与隔离**能力
- Skills 提供**知识与流程**封装
- Permissions 提供**安全与控制**保障

ExcelManus 应借鉴这套架构，但针对 **Excel 操作场景** 做适配：
- 更强的数据安全（PreApproval Hook + 变更审计）
- 更高效的执行（Python 回调 > shell 脚本）
- 更专业的子代理（Explorer / Analyst / Writer / Coder）
- 更精准的权限（Excel 文件路径级别的 glob 控制）
