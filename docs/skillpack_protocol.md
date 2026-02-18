# Skillpack 协议规范（SSOT）

> 最后更新：2026-02-19 (v5)  
> 适用范围：`excelmanus/skillpacks`、README、测试与任务文档

## 1. 目标
- 统一 Skillpack 的加载协议、路由语义与文档口径。
- 避免“实现已变更、文档未同步”造成的认知漂移。

## 2. 三层加载与覆盖
- 覆盖优先级：`system < user < project`。
- 同名 Skillpack 以高优先级来源覆盖低优先级来源。
- system 仅作为内置默认集；project 可覆盖 system/user。

## 3. 目录发现规则
发现顺序（低优先级到高优先级）：
1. system：`excelmanus/skillpacks/system`
2. user：`~/.excelmanus/skillpacks`、`~/.claude/skills`、`~/.openclaw/skills`
3. project：
   - 祖先链：`cwd -> workspace_root` 逐层 `.agents/skills`
   - 显式目录：`.excelmanus/skillpacks`、`.agents/skills`、`.claude/skills`、`.openclaw/skills`

严格协议说明：
- 项目级 OpenClaw 目录仅支持 `.openclaw/skills`。
- `workspace/skills` 不再作为 OpenClaw 项目级目录。

## 4. 路由语义
- 斜杠命令：`/<skill_name> args...` 直连技能（`slash_direct`）。
- 非斜杠消息：进入 `fallback`，所有工具始终可见（core 完整 schema，extended 摘要 schema）。
- LLM 通过 `activate_skill` 注入领域知识，通过 `expand_tools` 展开指定类别的扩展工具获取完整参数。

## 5. 内置 system Skillpacks（权威清单）
- `data_basic`
- `chart_basic`
- `format_basic`
- `file_ops`
- `sheet_ops`
- `excel_code_runner`

## 6. 变更治理要求
- 协议变更必须同时更新：实现、README、测试、本文档。
- 新增/删除内置 system Skillpack 时，必须同步：
  - `excelmanus/skillpacks/system/*`
  - README 内置清单
  - 契约测试 `tests/test_skillpack_docs_contract.py`

## 7. Hook 协议
- Hook 事件键支持三种写法：`PascalCase`、`lowerCamelCase`、`snake_case`。
  - 示例：`PreToolUse` / `preToolUse` / `pre_tool_use`
- `matcher` 使用 glob 语法匹配工具名（`fnmatch`）。
- 多 handler 合并决策优先级：`DENY > ASK > ALLOW > CONTINUE`。
- `ASK` 仅在 `PreToolUse` 事件生效，其它事件自动降级为 `CONTINUE`。
- `ALLOW` 在 `PreToolUse` 语义为“跳过确认门禁”，但不绕过 ToolPolicy 审计约束。

### 7.1 command handler
- `EXCELMANUS_HOOKS_COMMAND_ENABLED=false` 时，无条件跳过 command hook。
- 当开关为 true 时，仍需满足：
  - `fullAccess` 已开启，或
  - 命令命中 `EXCELMANUS_HOOKS_COMMAND_ALLOWLIST`。
- allowlist 仅允许单段命令；包含 `;`、`&&`、`||`、`|` 等多段链式命令不放行。

### 7.2 prompt handler
- 支持读取 `hookSpecificOutput`（`permissionDecision`、`permissionDecisionReason`、`updatedInput`、`additionalContext`）。
- 兼容顶层字段：`decision`、`reason`、`updated_input`、`additional_context`。

### 7.3 agent handler
- 最小动作字段：
  - `agent_name`
  - `task`
  - `on_failure`（`continue` / `deny`）
  - `inject_summary_as_context`（bool）
- 支持 `hookSpecificOutput.agentAction` 输入格式。
