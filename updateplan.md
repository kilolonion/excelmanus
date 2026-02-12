# 深度借鉴 Claude Code + 额外优化建议

基于对 Claude Code 最新官方文档（Skills、Hooks、Subagents、Memory）的逐条研究，以下是当前方案**尚未覆盖**的可借鉴机制和独立优化建议。

---

## 一、Claude Code 机制深度对比

先看当前方案已借鉴 vs 尚未借鉴的全景：

| Claude Code 机制 | 当前方案状态 | 建议 |
|-----------------|------------|------|
| 三层 Skill 目录（system/user/project） | ✅ 已覆盖 | - |
| 渐进加载（描述路由 → 正文注入） | ✅ 已覆盖 | - |
| `allowed_tools` 权限边界 | ✅ 已覆盖 | - |
| `disable_model_invocation` | ⚠️ 仅声明字段，未描述路由逻辑 | v3.0 补充 |
| `user_invocable` | ❌ 未提及 | v3.0 新增 |
| `$ARGUMENTS` 参数化模板 | ❌ 未提及 | v3.0 新增 |
| 动态上下文注入 `!command` | ❌ 未提及 | v3.1 |
| Hooks 生命周期（13 事件） | ⚠️ 方案提到 v3.1 再做 | v3.1 精简版 |
| Subagent / `context: fork` | ❌ 未提及 | v3.2+ |
| 持久记忆（MEMORY.md） | ❌ 未提及 | v3.1 |
| Auto-compaction（智能压缩） | ❌ 当前是简单截断 | v3.2+ |
| 技能正文 Token 预算 | ❌ 只有 max_selected 数量限制 | v3.0 补充 |
| Skill 分享机制（plugins） | ❌ 未提及 | v3.2+ |

---

## 二、v3.0 应纳入的补充（4 项）

### A. 双向调用控制——完善路由分流逻辑

Claude Code 的 Skill 有两个关键控制维度：

> - **`disable-model-invocation: true`**：只能用户手动 `/invoke`。用于有副作用的流程，如 `/deploy`、`/export-all`——你不希望模型自己决定执行这些。
> - **`user-invocable: false`**：只能模型自动触发。用于背景知识类技能（如 `excel-formula-reference`），用户手动调用没有意义。

当前方案的 `SKILL.md` 虽然声明了 `disable_model_invocation` 字段，但 **SkillRouter 的路由逻辑中没有描述如何据此分流**。需要补充：

```
路由分流规则：
├── 用户通过 /command 手动调用
│   → 跳过 Router 打分，直接加载该 Skillpack
│   → 忽略 user_invocable 限制（用户显式意图优先）
│
└── 模型自动路由
    → 排除 disable_model_invocation=true 的技能
    → 排除 user_invocable=false 的技能
    → 正常预筛 + LLM 确认
```

**SKILL.md Frontmatter 需新增**：
```yaml
user_invocable: bool   # 默认 true，设 false 则只能模型自动触发
```

**实际场景**：
- `general_excel`：`user_invocable: false`（兜底背景知识，用户不需要手动调）
- `export_batch`：`disable_model_invocation: true`（批量导出有副作用，必须用户主动触发）

### B. `$ARGUMENTS` 参数化模板

Claude Code 的 Skill 支持 `$ARGUMENTS`、`$ARGUMENTS[0]`（或简写 `$0`），用户调用时传参直接嵌入 prompt。

当前 CLI 的斜杠命令是**硬编码**的（`/help`, `/history`, `/clear`）。如果 Skillpack 也能注册为斜杠命令并支持参数化，交互效率大幅提升：

```
/analyze 销售数据.xlsx
  → 加载 analyze Skillpack，SKILL.md 正文中的 $ARGUMENTS 替换为 "销售数据.xlsx"

/chart 销售数据.xlsx bar 月份 销售额
  → $0="销售数据.xlsx" $1="bar" $2="月份" $3="销售额"
```

**SKILL.md 示例**：
```yaml
---
name: quick-chart
description: 快速生成图表
disable_model_invocation: true
allowed_tools: [read_excel, create_chart]
argument_hint: "<file> <chart_type> <x_col> <y_col>"
---
从 $0 读取数据，生成 $1 类型图表，X轴为 $2，Y轴为 $3。
输出文件保存为 chart_output.png。
```

**实现成本极低**（正则替换），但直接消除了多轮对话的需要。

**SKILL.md Frontmatter 需新增**：
```yaml
argument_hint: str   # 参数提示，如 "<file> [chart_type]"
```

### C. 技能正文 Token 预算

当前方案有 `skills_max_selected`（最多 3 个技能），但**没有对注入的技能正文总长度做预算控制**。

Claude Code 有 `SLASH_COMMAND_TOOL_CHAR_BUDGET` 来限制技能加载的字符预算。如果 3 个技能都接近 500 行上限，注入 ~4500+ tokens 的 system context 会严重挤压实际对话空间。

**建议新增配置**：
```python
skills_context_char_budget: int = 12000  # 默认约 4000 tokens
```

**预算超出时的降级策略**：
1. 按 `priority` 字段排序，优先保留高优先级技能的完整正文
2. 低优先级技能只注入前 N 行（截断 + 附加 `[正文已截断，完整内容见 SKILL.md]`）
3. 仍然超出时，降级为只注入 `name + description`（不注入正文）

### D. Router 对 `disable_model_invocation` 的处理

当前方案的运行时数据流第 2-3 步需要修正：

```
2. SkillRouter.prefilter：
   - 【新增】如果是 /command 手动调用 → 直接定位到目标 Skillpack，跳到步骤 4
   - 基于 triggers、file_patterns、skill_hints 打分
   - 【新增】排除 disable_model_invocation=true 的候选
   - 取 Top-K 候选

3. SkillRouter.confirm_with_llm：
   - （同原方案，无变化）
```

---

## 三、v3.1 建议纳入（4 项）

### E. 动态上下文注入 `!tool(args)`

Claude Code 的 `!` 前缀允许在技能加载时执行命令，输出直接嵌入 prompt。对 ExcelManus 价值极大——**可以零轮次获取环境信息**：

```markdown
---
name: data-explore
description: 探索并分析 Excel 数据
allowed_tools: [read_excel, analyze_data, filter_data]
---
## 当前工作目录
!list_directory(".")

## 目标文件概况
!read_excel("$0", max_rows=5)

## 你的任务
基于以上数据概况，对 $0 进行深入分析...
```

加载技能时，`!list_directory(".")` 和 `!read_excel(...)` 会**先执行**，结果替换占位符。LLM 拿到的 prompt 已经包含了环境上下文，**减少 1-2 轮工具调用**。

### F. 精简版 Hooks（3 个高价值事件点）

Claude Code 有 13 个 hook 事件，完整移植太重。建议 v3.1 先实现 **3 个最高 ROI 的 hook**：

| Hook | 触发时机 | ExcelManus 用途 |
|------|---------|----------------|
| **PreToolUse** | 工具执行前 | 自动注入 [workspace_root](cci:1://file:///Users/jiangwenxuan/Desktop/excelagent/excelmanus/security.py:18:4-21:25)（统一解决 FileAccessGuard 分散问题）；参数校验增强 |
| **PostToolUse** | 工具执行后 | 操作审计日志；自动结果压缩（大 DataFrame 只保留摘要） |
| **UserPromptSubmit** | 用户输入后、路由前 | 自动识别文件引用（"分析销售数据" → 自动发现 `销售数据示例.xlsx`）；输入规范化 |

**实现方式**：用 Python callable（而非 Claude Code 的 bash 脚本），更贴合项目技术栈：

```python
@dataclass
class HookDef:
    event: Literal["pre_tool_use", "post_tool_use", "user_prompt_submit"]
    matcher: str | None = None  # 匹配工具名（仅 tool hook）
    handler: Callable[[dict], HookResult]
```

**PreToolUse 的核心价值**——`updatedInput`：hook 可以**修改工具参数再执行**。这直接解决当前代码中 [FileAccessGuard](cci:2://file:///Users/jiangwenxuan/Desktop/excelagent/excelmanus/security.py:11:0-76:23) 在 4 个模块中重复初始化的问题：

```python
# PreToolUse hook：统一注入 workspace_root
def inject_workspace(input: dict) -> HookResult:
    if "file_path" in input["arguments"]:
        # 自动将相对路径解析为安全的绝对路径
        input["arguments"]["file_path"] = resolve_safe_path(input["arguments"]["file_path"])
    return HookResult(decision="allow", updated_input=input)
```

### G. 跨会话持久记忆

Claude Code 的 auto-memory 机制（`MEMORY.md` + 主题文件）：
- **MEMORY.md** 前 200 行自动加载到每个会话的 system prompt
- 主题文件（`debugging.md`, `patterns.md`）按需读取
- 自动学习项目模式、用户偏好

**对 ExcelManus 的场景价值**：
- 记住项目中常用的 Excel 文件结构（列名、数据类型、行数量级）
- 记住用户偏好的图表样式、输出格式
- 记住常见错误的解决方案

**建议实现路径**：
```
~/.excelmanus/memory/
├── MEMORY.md            # 前 200 行自动加载
├── file_patterns.md     # 常见文件结构记录
└── user_prefs.md        # 用户偏好
```

Session 结束时，由 AgentEngine 自动提取"值得记住的信息"追加到 MEMORY.md。

### H. 工具结果压缩

当前工具返回完整 JSON（如 [read_excel](cci:1://file:///Users/jiangwenxuan/Desktop/excelagent/excelmanus/skills/data_skill.py:48:0-78:60) 返回 10 行预览数据 + 列类型 + 统计信息），对大文件可能产生 **2000+ tokens 的 tool result**。

建议在 `ToolDef` 上增加 `max_result_chars` 字段：

```python
@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]
    func: Callable[..., Any]
    sensitive_fields: set[str] = field(default_factory=set)
    max_result_chars: int = 3000  # 新增：结果最大字符数
```

超出时自动截断 + 附加 `"[结果已截断，原始长度: N 字符]"`。这与 Claude Code subagent 的"只返回摘要"思路一致，但更轻量。

---

## 四、v3.2+ 远期方向（3 项）

### I. Subagent / Fork Context

**场景**：分析 10 万行 Excel 时，中间过程（逐列统计、异常检测）产生的 token 会快速填满上下文。

**借鉴 Claude Code 的 `context: fork`**：
- 将"数据探索"阶段 fork 到子上下文
- 子代理有独立的工具权限（只读）和 token 窗口
- 子代理只向主会话返回摘要结论
- 主上下文保持精简

```yaml
---
name: deep-analysis
description: 大数据集深度分析
context: fork           # 在独立上下文中执行
allowed_tools: [read_excel, analyze_data, filter_data]
---
```

### J. 智能 Compaction 替代简单截断

当前 `@/Users/jiangwenxuan/Desktop/excelagent/excelmanus/memory.py:141-180` 的 [_truncate_if_needed()](cci:1://file:///Users/jiangwenxuan/Desktop/excelagent/excelmanus/memory.py:140:4-179:37) 是从头部逐条删除消息。Claude Code 的做法是用 LLM 生成对话摘要，替换旧消息。

**建议**：
- 触发条件同现在（token 接近阈值）
- 但不是直接删除，而是用轻量模型（如 qwen-turbo）将最早的 N 条消息压缩为 1 条摘要消息
- 保留关键的工具调用结果摘要，丢弃冗长中间输出
- 比简单截断保留更多语义信息

### K. 多模型路由

Claude Code 的 subagent 可以指定不同模型（Haiku 做探索、Sonnet 做规划）。

ExcelManus 可以借鉴：不同 Skillpack 可在 SKILL.md 中声明偏好模型：
```yaml
model: qwen-turbo        # 简单数据读取用轻量模型
model: qwen-max-latest   # 复杂分析用强模型
```
路由层根据命中的 Skillpack 动态切换模型，**优化成本/质量平衡**。

---

## 五、更新后的 SKILL.md Frontmatter 规范

综合以上建议，**完整的 v3.0 Frontmatter 规范**应为：

```yaml
---
# ── 必填 ──
name: str
description: str
allowed_tools: [str]
triggers: [str]

# ── 选填（v3.0） ──
file_patterns: [str]
resources: [str]
priority: int                       # 默认 0
version: str
disable_model_invocation: bool      # 默认 false
user_invocable: bool                # 【新增】默认 true
argument_hint: str                  # 【新增】参数提示

# ── 选填（v3.1 预留） ──
context: str                        # 【预留】"fork" | "inline"（默认 inline）
model: str                          # 【预留】偏好模型
hooks: [dict]                       # 【预留】技能级 hooks
memory: str                         # 【预留】"user" | "project" | "local"
---
```

---

## 六、分层路线图总览

```
v3.0 ─── 核心架构（当前方案） ──────────────────────
  │  双层 Tools + Skillpacks
  │  三层目录 + 优先级覆盖
  │  SkillRouter（预筛 + LLM 确认）
  │  ToolScope 权限边界
  │  【补充】双向调用控制
  │  【补充】$ARGUMENTS 参数化
  │  【补充】技能正文 Token 预算
  │
v3.1 ─── 增强交互 ─────────────────────────────────
  │  动态上下文注入 !command
  │  精简 Hooks（3 事件点）
  │  跨会话持久记忆（MEMORY.md）
  │  工具结果压缩
  │  工具调用结果缓存
  │
v3.2+ ── 高级能力 ─────────────────────────────────
     Subagent / Fork Context
     智能 Compaction（摘要替代截断）
     多模型路由
     Skill 分享 / Plugin 机制
```

需要我把这些补充内容整合进原方案文档，生成一份完整的更新版重构方案吗？