# Skillpack protocol

Applies to: 1.8.1 source tree · Updated: 2026-09-21

[Documentation](README.md) · [中文](skillpack_protocol.md) · [Configuration](configuration_en.md)

Skillpacks provide reusable task methods and reference material. This guide covers authoring, discovery, overrides, and invocation. The runtime remains responsible for permissions.

## 1. Write a skill

Each skill has its own directory with a `SKILL.md` entry point. For example, create `sales_summary/SKILL.md` in the user skill directory:

```markdown
---
name: sales_summary
description: Summarize sales by region and save the summary to a new workbook.
file-patterns:
  - "*.xlsx"
user-invocable: true
---
Confirm the meaning of the sales and region columns before summarizing.
Explain how duplicate records and missing regions are handled.
Report the output file, calculation scope, and any unresolved questions.
```

`name` and `description` support discovery; the Markdown body provides the method. `resources` can list reference files inside the skill directory. Common optional fields:

| Field | Purpose |
| --- | --- |
| `file-patterns` | File-pattern metadata; does not grant file access or trigger execution by itself |
| `resources` | Reference files to load |
| `version` | Skill version identifier |
| `user-invocable` | Whether users can invoke the skill explicitly |
| `disable-model-invocation` | Whether model-initiated invocation is disabled |
| `argument-hint` | Argument hint |
| `required-mcp-servers` / `required-mcp-tools` | MCP dependencies |
| `hooks` | Hook handlers, described in section 7 |

Hyphenated fields above also accept underscore aliases. Describe applicability, key steps, and delivery requirements in the body. Query current tool details for parameters instead of duplicating a complete schema that can drift.

## 2. Loading and overrides

Overall priority is `system < user < project`. A same-name skill from a later, higher-priority source replaces the earlier skill; their bodies are not merged. Built-in skills provide defaults that user or project skills can override.

Settings supports skill management and import. Inspect the running instance's skill list and loading warnings to determine what is available. Loading a skill does not expand file access, tool permissions, or approval authority.

## 3. Directory discovery

Default scan order is listed below. Settings can disable general discovery, external tool directories, or ancestor scanning.

1. Built-in directory: `excelmanus/skillpacks/system`.
2. User directories: the configured user root, defaulting to `~/.excelmanus/skillpacks`, plus `~/.claude/skills` and `~/.openclaw/skills` when external discovery is enabled.
3. Ancestor directories: only when cwd is inside the configured workspace, scan `.agents/skills` from the workspace root toward cwd. Later, nearer directories can override earlier ones.
4. Explicit project directories: the configured project root, normally `<workspace_root>/.excelmanus/skillpacks`, plus enabled `.agents/skills`, `.claude/skills`, and `.openclaw/skills` directories.
5. Additional roots from `EXCELMANUS_SKILLS_DISCOVERY_EXTRA_DIRS`, loaded as project sources.

`.openclaw/skills` is supported at both user and project levels. A plain `workspace/skills` directory is not a default root; add it explicitly if needed. Disabling general discovery leaves only the configured system, user, and project roots.

## 4. Invocation and tool visibility

- `/<skill_name> args...` invokes a skill explicitly (`slash_direct` internally). An `@` reference can also include a skill in task context.
- Ordinary messages enter the model loop. The model can load instructions and resources through `skill`; loading does not execute a fixed business script.
- `read` and `plan` exclude pure-write tools. Tools with read-only actions may remain discoverable, but write actions are restricted at execution.
- In `write`, direct tools and `run_code` coexist. Common tools load upfront; other capabilities are disclosed on demand after `introspect_capability`. The `em.*` SDK binds the complete authorized catalog.
- Invoking a skill does not switch execution modes or bypass path checks, content versions, approvals, or write restrictions.
- Activation returns instructions and a resource index. Read supporting content on demand with `introspect_capability(query_type="knowledge_read", query="resource:skill-name/path")`; references are no longer all inlined at activation. Use `knowledge_workflow` for schema-checked task recipes and version dependencies.

## 5. Built-in system Skillpacks

| Skill | Main purpose |
| --- | --- |
| `spreadsheet_workflow` | ExcelManus V2 workflow for observing, analyzing, editing, recalculating, validating, previewing, and delivering spreadsheets |
| `data_basic` | Reading, analysis, filtering, and transformation |
| `chart_basic` | Workbook charts and image export |
| `format_basic` | Styles, conditional formatting, and layout |
| `file_ops` | File management |
| `sheet_ops` | Worksheet and cross-sheet operations |
| `excel_code_runner` | Custom calculations and tool composition |
| `run_code_templates` | Batch writing, analysis, and formatting templates |
| `word_basic` | Word reading, editing, and generation |
| `word_code_runner` | Complex Word processing |
| `agent_self_management` | Inspect capabilities and adjust session settings; disabled by default |

Individual built-in skills can be gated by runtime switches: `agent_self_management` is enabled by default and loads only while Settings → System → Capabilities → Agent self-management is enabled; when disabled it is hidden from the skill list, name lookups, and incremental loads.

## 6. Maintenance and validation

Update the implementation, both language guides, and related tests when the protocol changes. Adding or removing a built-in skill requires updating `excelmanus/skillpacks/system/`, both project README lists, and `tests/test_skillpack_docs_contract.py`.

```bash
uv run pytest tests/test_skillpack_docs_contract.py
```

Runtime skill files and templates affect program behavior. When changing a sample tool call, verify the current parameters, output structure, and write permissions. Clear prose alone does not establish a correct contract.

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
