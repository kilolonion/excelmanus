# Skillpack Protocol Specification (SSOT)

> Last updated: 2026-02-21  
> Scope: `excelmanus/skillpacks`, README, tests, and task documentation

## 1. Goals
- Unify the loading protocol, routing semantics, and documentation for Skillpacks.
- Prevent cognitive drift caused by "implementation changed but documentation not synced."

## 2. Three-Layer Loading and Override
- Override priority: `system < user < project`.
- A Skillpack with the same name from a higher-priority source overrides the one from a lower-priority source.
- system serves only as the built-in default set; project can override system/user.

## 3. Directory Discovery Rules
Discovery order (lowest to highest priority):
1. system: `excelmanus/skillpacks/system`
2. user: `~/.excelmanus/skillpacks`, `~/.claude/skills`, `~/.openclaw/skills`
3. project:
   - Ancestor chain: `cwd -> workspace_root`, scanning `.agents/skills` at each level
   - Explicit directories: `.excelmanus/skillpacks`, `.agents/skills`, `.claude/skills`, `.openclaw/skills`

Strict protocol notes:
- External tool directories (`.openclaw/skills`) are only supported at the project level.
- `workspace/skills` is no longer used as an external tool project-level directory.

## 4. Routing Semantics
- Slash commands: `/<skill_name> args...` directly invokes the skill (`slash_direct`).
- Non-slash messages: enter `fallback`, where all tools are always visible (core with full schema, extended with summary schema).
- The LLM injects domain knowledge via `activate_skill` and expands extended tools in a specified category via `expand_tools` to obtain full parameters.

## 5. Built-in system Skillpacks (Authoritative List)
- `data_basic`
- `chart_basic`
- `format_basic`
- `file_ops`
- `sheet_ops`
- `excel_code_runner`
- `run_code_templates`

## 6. Change Governance Requirements
- Any protocol change must simultaneously update: implementation, README, tests, and this document.
- When adding or removing a built-in system Skillpack, the following must be synced:
  - `excelmanus/skillpacks/system/*`
  - Built-in list in README
  - Contract test `tests/test_skillpack_docs_contract.py`

## 7. Hook Protocol
- Hook event keys support three naming conventions: `PascalCase`, `lowerCamelCase`, `snake_case`.
  - Examples: `PreToolUse` / `preToolUse` / `pre_tool_use`
- `matcher` uses glob syntax to match tool names (`fnmatch`).
- Multi-handler merged decision priority: `DENY > ASK > ALLOW > CONTINUE`.
- `ASK` only takes effect on `PreToolUse` events; for other events it automatically downgrades to `CONTINUE`.
- `ALLOW` in `PreToolUse` semantics means "skip the confirmation gate," but does not bypass ToolPolicy audit constraints.

### 7.1 command handler
- When `EXCELMANUS_HOOKS_COMMAND_ENABLED=false`, command hooks are unconditionally skipped.
- When the switch is true, the following conditions must still be met:
  - `fullAccess` is enabled, or
  - The command matches `EXCELMANUS_HOOKS_COMMAND_ALLOWLIST`.
- The allowlist only permits single-segment commands; multi-segment chained commands containing `;`, `&&`, `||`, `|`, etc. are not allowed.

### 7.2 prompt handler
- Supports reading `hookSpecificOutput` (`permissionDecision`, `permissionDecisionReason`, `updatedInput`, `additionalContext`).
- Compatible with top-level fields: `decision`, `reason`, `updated_input`, `additional_context`.

### 7.3 agent handler
- Minimum action fields:
  - `agent_name`
  - `task`
  - `on_failure` (`continue` / `deny`)
  - `inject_summary_as_context` (bool)
- Supports `hookSpecificOutput.agentAction` input format.
