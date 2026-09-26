# Skillpack 协议

适用版本：1.8.1 源码 · 更新日期：2026-09-21

[文档导航](README.md) · [English](skillpack_protocol_en.md) · [配置参考](configuration.md)

Skillpack 为模型提供可复用的任务方法和参考资料。本文说明当前的编写、发现、覆盖与调用规则；权限仍由运行时控制。

## 1. 编写一个技能

每个技能放在独立目录中，入口为 `SKILL.md`。例如，在用户技能目录创建 `sales_summary/SKILL.md`：

```markdown
---
name: sales_summary
description: 按地区汇总销售数据，并将汇总表保存到新工作簿。
file-patterns:
  - "*.xlsx"
user-invocable: true
---
先确认销售额和地区列的含义，再按用户要求汇总。
存在重复记录或缺失地区时，说明处理方式。
交付时列出输出文件、统计口径和仍需确认的事项。
```

`name` 和 `description` 用于发现；Markdown 正文提供具体方法。`resources` 可列出技能目录内的参考文件。常用可选字段：

| 字段 | 用途 |
| --- | --- |
| `file-patterns` | 文件模式元数据；不会单独授予文件访问或自动执行权限 |
| `resources` | 要加载的参考资源路径 |
| `version` | 技能版本标识 |
| `user-invocable` | 是否允许用户显式调用 |
| `disable-model-invocation` | 是否禁止模型主动调用 |
| `argument-hint` | 参数提示 |
| `required-mcp-servers` / `required-mcp-tools` | MCP 依赖声明 |
| `hooks` | Hook 处理器配置，见第 7 节 |

上述连字符字段也支持对应的下划线写法。方法正文宜描述适用条件、关键步骤和交付要求；工具字段应通过当前工具详情查询，避免复制容易过期的完整参数表。

## 2. 加载与覆盖

总体优先级为 `system < user < project`。同名技能由后加载的高优先级来源覆盖，不合并两份正文。内置技能提供默认方法，用户和项目技能可覆盖它们。

设置页支持管理和导入技能；目录名称、加载警告和当前可见技能应以运行实例的技能列表为准。技能加载不会扩大当前会话的文件范围、工具权限或审批权限。

## 3. 目录发现规则

默认扫描顺序如下；可用配置项关闭通用发现、外部工具目录或祖先链扫描：

1. 内置目录：`excelmanus/skillpacks/system`。
2. 用户目录：配置的用户技能目录（默认 `~/.excelmanus/skillpacks`），以及启用外部目录发现后的 `~/.claude/skills`、`~/.openclaw/skills`。
3. 项目祖先链：仅当当前目录位于配置的工作区内时，扫描从工作区根到当前目录的 `.agents/skills`；后加载的近层目录可覆盖前者。
4. 项目显式目录：配置的项目技能目录（默认 `<workspace_root>/.excelmanus/skillpacks`），以及开关允许的 `.agents/skills`、`.claude/skills`、`.openclaw/skills`。
5. `EXCELMANUS_SKILLS_DISCOVERY_EXTRA_DIRS` 指定的额外目录，作为项目来源加载。

`.openclaw/skills` 同时支持用户级和项目级发现。普通 `workspace/skills` 不是默认扫描入口；如需使用，显式加入额外目录。关闭通用发现时，只加载配置的 system、user、project 三个目录。

## 4. 调用与工具可见性

- `/<skill_name> args...` 显式调用技能（内部路由名为 `slash_direct`）；`@` 引用也可把技能作为当前任务上下文。
- 普通消息进入模型循环，模型可通过 `skill` 按需加载方法与参考资料。加载本身不执行一套固定业务脚本。
- `read` / `plan` 不提供纯写工具；含只读 action 的工具仍可能被发现，写入 action 在执行时受限。
- `write` 中直接工具与 `run_code` 共存。常用工具直接提供，其他能力经 `introspect_capability` 查询后按需加载；代码内的 `em.*` 绑定完整授权目录。
- 技能调用不切换执行模式，不绕过路径、内容版本、审批或写入约束。
- 激活仅返回技能正文和资源索引，资源正文通过 `introspect_capability(query_type="knowledge_read", query="resource:技能名/路径")` 按需读取；不再默认把全部参考文件追加到上下文。组合任务可用 `knowledge_workflow` 获取当前 schema 校验过的路线与版本依赖。

## 5. 内置 system Skillpacks

| 技能 | 主要用途 |
| --- | --- |
| `spreadsheet_workflow` | ExcelManus V2 表格任务的观察、分析、变更、重算、校验、预览与交付闭环 |
| `data_basic` | 读取、分析、筛选与转换 |
| `chart_basic` | 工作簿图表与图片导出 |
| `format_basic` | 样式、条件格式与排版 |
| `file_ops` | 文件管理 |
| `sheet_ops` | 工作表及跨表操作 |
| `excel_code_runner` | 自定义计算和跨工具编排 |
| `run_code_templates` | 批量读写、分析及格式模板 |
| `word_basic` | Word 读取、编辑与生成 |
| `word_code_runner` | 复杂 Word 处理 |
| `agent_self_management` | 查询自身能力并调整当前会话配置；默认关闭 |

个别内置技能受运行时开关门控：`agent_self_management` 默认启用，仅在「设置 → 系统 → 能力 → Agent 自我管理」开关开启时可加载，关闭时对技能列表、按名获取与增量加载均不可见。

## 6. 维护与验证

协议变化应同步实现、中英文说明和相关测试。新增或删除内置技能时，同步 `excelmanus/skillpacks/system/`、两份项目 README 的技能清单及 `tests/test_skillpack_docs_contract.py`。

```bash
uv run pytest tests/test_skillpack_docs_contract.py
```

运行时使用的 `SKILL.md` 及参考模板属于程序行为的一部分。修改示例调用时，应核对当前工具参数、返回结构和写入权限，不能仅把文字通顺视为契约正确。

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
