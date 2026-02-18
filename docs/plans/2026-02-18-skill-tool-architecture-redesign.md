# Skill-Tool 架构重设计：双层解耦

> **状态**: 已确认设计，待实施  
> **日期**: 2026-02-18  
> **取代**: `2026-02-18-skill-tool-logic-deconfusion.md`（战术修补方案，被本文的结构性重设计取代）

---

## 1. 问题陈述

当前 ExcelManus 的 Skill 系统将两个正交关切耦合在一起：

1. **知识注入**：告诉 LLM 如何完成特定领域任务的指引
2. **工具授权控制**：通过 `allowed_tools` 决定 LLM 可以调用哪些工具

这导致了多层冗余机制（PreRouter 小模型预判、auto_supplement 越权扩充、5 分支 tool_scope 计算），
总计 ~2000 行复杂路由/合并代码，以及三条独立的技能激活路径。

## 2. 行业调研

### 2.1 Agent Skills 开放标准（agentskills.io）

由 Anthropic 发起，Cursor、Gemini CLI、Codex 等采用的开放标准：

- **Skill = 知识包**：SKILL.md 只含 `name`、`description`、markdown body
- **Skill 不控制工具**：标准中的 `allowed-tools` 字段（实验性）是权限预批准语义（`Bash(git:*)`），不是工具归属
- **Progressive Disclosure**：初始只加载 name+description (~100 tokens)，激活后加载完整 body (<5000 tokens)
- **脚本不是工具**：Skill 附带的 `scripts/` 通过 agent 原生工具（Bash/run_shell）间接执行

### 2.2 Claude Code

- 原生工具 ~10 个（Read, Write, Bash, Search, Skill 等），始终全部可见
- `Skill` 元工具：LLM 调用后注入 SKILL.md body 到对话历史
- Skill 不控制工具可见性

### 2.3 Cursor

- 原生工具 + MCP 工具，始终全部可见
- Agent 自动判断或 `/skill-name` 手动激活
- SKILL.md frontmatter 仅 `name`、`description`、`disable-model-invocation`
- 兼容 `.claude/skills/`、`.codex/skills/`、`.cursor/skills/` 三种路径

### 2.4 Gemini CLI

- 原生工具（read_file, write_file, run_shell_command, activate_skill 等），始终可见
- `activate_skill` 元工具：注入 SKILL.md body + 目录结构
- 用户确认后激活，session 级别持续生效

### 2.5 共性总结

| 共性 | 说明 |
|------|------|
| Skill = 纯知识注入 | 不控制工具可见性或授权 |
| Tool 全部可见 | 因为原生工具少（~10 个），无需裁剪 |
| 单一元工具激活 | 一个 `Skill` / `activate_skill` 调用 |
| Progressive Disclosure | 延迟加载 prompt 内容，不裁剪工具 |

### 2.6 ExcelManus 的特殊约束

| 约束 | 通用 Agent | ExcelManus |
|------|-----------|------------|
| 原生工具数量 | ~10 个通用原语 | **40+ 领域特化工具** |
| 工具 schema token 开销 | 小 | **大（~6000 tokens 若全量完整展示）** |
| 安全分层需求 | 基本 | **强（Tier A 确认 / Tier B 审计）** |

因此我们不能简单照搬"全量完整展示"，但也不能让 Skill 承担工具呈现控制。
解决方案：**引入独立的 ToolProfile 层**。

## 3. 新架构：三层正交

```
┌──────────────────────────────────────────────────────────┐
│  Layer 1: Tool Presentation（工具呈现层）                  │
│  ToolProfile 静态配置 + expand_tools 元工具                │
│  决定：LLM 看到的 schema 详细度（完整 vs 摘要）           │
│                                                           │
│  Layer 2: Skill（知识注入层）                              │
│  对齐 Agent Skills 开放标准                                │
│  决定：LLM 获取的领域专业指引                              │
│                                                           │
│  Layer 3: Tool Policy（安全层）                            │
│  读写分层 + 审批门禁 + 审计                                │
│  决定：工具调用是否允许执行                                │
└──────────────────────────────────────────────────────────┘

三层完全正交：修改任何一层不影响其他两层。
```

### 3.1 Layer 1: ToolProfile（工具呈现层）

#### 概念

全量工具始终注册且可调用，但 LLM 看到的 schema 详细度分两级：

- **core 工具**：始终展示完整 OpenAI tool schema（parameters、required 等）
- **extended 工具**：默认仅展示 `name` + 一句 `description`，parameters 为空对象

#### core 工具集

覆盖"读取 + 结构发现 + 元操作"，约 15 个：

```python
CORE_TOOLS: frozenset[str] = frozenset({
    # 数据读取
    "read_excel", "analyze_data", "filter_data",
    "group_aggregate", "inspect_excel_files",
    # 结构发现
    "list_sheets", "list_directory", "get_file_info",
    "find_files", "read_text_file", "read_cell_styles",
    # 元工具
    "activate_skill", "expand_tools",
    "focus_window", "task_create", "task_update",
})
```

#### 工具类别

extended 工具按类别组织（复用现有 `TOOL_CATEGORIES`）：

| 类别 | 包含工具 | 工具数 |
|------|---------|--------|
| `data_write` | write_excel, write_cells, transform_data, insert_rows, insert_columns | 5 |
| `format` | format_cells, adjust_column_width/height, merge/unmerge_cells, 条件格式等 | 15 |
| `chart` | create_chart, create_excel_chart | 2 |
| `sheet` | create/copy/rename/delete_sheet, copy_range_between_sheets | 5 |
| `code` | write_text_file, run_code, run_shell | 3 |
| `file_ops` | copy_file, rename_file, delete_file | 3 |

#### expand_tools 元工具

```json
{
  "name": "expand_tools",
  "description": "按类别展开工具的完整参数说明。当你需要调用某个类别的工具但只看到了名称和简短描述时，先调用此工具获取完整的参数定义。\n\n可用类别：\n- data_write: 数据写入\n- format: 格式化与样式\n- chart: 图表生成\n- sheet: 工作表管理\n- code: 代码执行\n- file_ops: 文件操作",
  "parameters": {
    "type": "object",
    "properties": {
      "category": {
        "type": "string",
        "enum": ["data_write", "format", "chart", "sheet", "code", "file_ops"]
      }
    },
    "required": ["category"]
  }
}
```

调用后，engine 在下一轮对话中将对应类别的工具 schema 从摘要升级为完整版。
升级是 session 级别的（一次 expand 后持续生效）。

#### Schema 生成逻辑

```python
def _build_tool_schemas(self) -> list[dict]:
    """根据当前 expanded categories 生成分层 tool schemas。"""
    schemas = []
    for tool in self._registry.get_all_tools():
        profile = TOOL_PROFILES.get(tool.name)
        if profile is None:
            continue
        if profile["tier"] == "core" or profile["category"] in self._expanded_categories:
            schemas.append(tool.to_openai_schema())  # 完整 schema
        else:
            schemas.append(tool.to_summary_schema())  # 仅 name + description
    return schemas
```

#### Token 节省估算

- 完整 schema 每工具 ≈ 150-300 tokens
- 摘要 schema 每工具 ≈ 20-30 tokens
- ~30 个 extended 工具：完整 ≈ 6000 tokens，摘要 ≈ 750 tokens
- 首轮节省 ≈ 5000 tokens，同时 LLM 仍知道所有工具的存在

### 3.2 Layer 2: Skill（知识注入层）

#### 完全对齐 Agent Skills 开放标准

SKILL.md frontmatter 仅保留标准字段：

```yaml
---
name: format-basic
description: 格式化与样式操作指引。当用户需要调整字体、颜色、边框、合并单元格、条件格式时使用。
---
```

可选标准字段：`license`、`compatibility`、`metadata`。

ExcelManus 扩展字段（通过 `metadata` 承载，不污染标准）：

```yaml
metadata:
  excelmanus-version: "2.0.0"
  disable-model-invocation: false  # 对应标准的同名字段
```

#### SKILL.md body

纯 markdown 指引，不包含任何工具控制语义：

```markdown
# 格式化操作指引

## 操作前
- 先用 read_cell_styles 了解现有样式，再决定修改方案
- 写入操作前先备份（copy_file）

## 格式化最佳实践
- format_cells 应用字体/填充/边框/对齐/数字格式
- adjust_column_width / adjust_row_height 调整尺寸
- merge_cells / unmerge_cells 合并/取消合并

## 条件格式
- 阈值图标：apply_threshold_icon_format
- 渐变色阶：add_color_scale
- 数据条：add_data_bar
- 通用规则：add_conditional_rule
```

#### activate_skill 元工具

```json
{
  "name": "activate_skill",
  "description": "激活技能获取专业操作指引。技能提供特定领域的最佳实践和步骤指导。\n\n可用技能：\n- data-basic: 数据读取、分析、筛选与转换\n- format-basic: 格式化与样式操作\n- chart-basic: 图表生成\n- file-ops: 文件管理\n- sheet-ops: 工作表管理与跨表操作\n- excel-code-runner: 通过 Python 脚本处理大体量 Excel",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {"type": "string", "description": "技能名称"}
    },
    "required": ["name"]
  }
}
```

返回值 = SKILL.md body 文本 + base path，注入对话历史。

#### Skill 目录结构

```
excelmanus/skillpacks/system/
├── data-basic/
│   └── SKILL.md
├── format-basic/
│   ├── SKILL.md
│   └── references/
│       └── advanced-format-guide.md
├── chart-basic/
│   └── SKILL.md
├── file-ops/
│   └── SKILL.md
├── sheet-ops/
│   └── SKILL.md
└── excel-code-runner/
    ├── SKILL.md
    └── references/
        └── openpyxl-patterns.md
```

不再有 `general_excel` 兜底 Skill（全量工具始终可见，不需要兜底）。

#### 三层 Skill 加载优先级

保持现有 project > user > system 覆盖机制。

### 3.3 Layer 3: Tool Policy（安全层）

完全不变，独立于 Skill 和 ToolProfile：

- **Tier A** (MUTATING_CONFIRM_TOOLS): 需 `/accept` 门禁确认
- **Tier B** (MUTATING_AUDIT_ONLY_TOOLS): 仅审计
- **READ_ONLY_SAFE_TOOLS**: 直接放行
- `write_hint` 分类保留（由 router 或词法分析提供）

### 3.4 两个元工具的交互模式

| 场景 | LLM 行为 |
|------|---------|
| "把A列格式化为百分比" | `expand_tools("format")` → `format_cells(...)` |
| "不确定怎么格式化最好" | `activate_skill("format-basic")` → 获得指引 → `expand_tools("format")` → 按指引操作 |
| "分析销售数据月度趋势" | 直接调用 `read_excel` + `analyze_data`（core 工具） |
| "做一个专业仪表盘" | `activate_skill("format-basic")` + `expand_tools("format")` + `expand_tools("chart")` |
| "你好" | 不需要任何元工具 |

两个元工具完全独立：
- 可以只 expand 不 activate（知道怎么做，只需要参数）
- 可以只 activate 不 expand（需要指引，但工具参数已通过其他方式获知）
- 可以都用（需要指引 + 参数）

## 4. 删除/保留清单

### 删除（~1800 行）

| 文件 | 组件 | 行数估 |
|------|------|--------|
| `skillpacks/pre_router.py` | 整个文件 | 493 |
| `engine.py` | `_get_current_tool_scope()` 及其 5 个分支 | 150 |
| `engine.py` | `_activate_preroute_candidates()` | 40 |
| `engine.py` | `_apply_preroute_fallback()` | 20 |
| `engine.py` | `_try_auto_supplement_tool()` | 60 |
| `engine.py` | `_build_tool_to_skill_index()` | 40 |
| `engine.py` | `_expand_tool_scope_patterns()` | 50 |
| `engine.py` | `_merge_with_loaded_skills()` | 30 |
| `engine.py` | `_resolve_preroute_target_layered()` | 30 |
| `engine.py` | Phase 1 预激活逻辑块 | 100 |
| `engine.py` | `select_skill` / `discover_tools` / `list_skills` 元工具注册 | 80 |
| `skillpacks/models.py` | `allowed_tools`, `triggers`, `priority`, `command_dispatch`, `command_tool` 字段 | 10 |
| `skillpacks/router.py` | `_build_result()` 中 tool_scope 计算 | 30 |
| `config.py` | `skill_preroute_*`, `auto_supplement_*`, `auto_activate_default_skill` | 40 |
| `tools/policy.py` | `DISCOVERY_TOOLS`, `FALLBACK_DISCOVERY_TOOLS` 概念 | 20 |
| 各 `SKILL.md` | `allowed_tools`, `triggers`, `priority` 字段 | - |
| 测试文件 | 对应的 pre_route / auto_supplement / tool_scope 测试 | ~600 |

### 新增（~280 行）

| 文件 | 组件 | 行数估 |
|------|------|--------|
| `tools/profile.py` (新) | ToolProfile 定义 + 分层 schema 生成 + `to_summary_schema()` | 150 |
| `engine.py` | `_build_tool_schemas()` 替代 `_get_current_tool_scope()` | 60 |
| `engine.py` | `expand_tools` 元工具处理 | 40 |
| `engine.py` | `activate_skill` 元工具（简化自 `_handle_select_skill`） | 30 |

### 保留（不变）

| 组件 | 原因 |
|------|------|
| `tools/policy.py` 安全分层 | Layer 3 独立 |
| `subagent/tool_filter.py` (FilteredToolRegistry) | 子代理工具限制是独立关切 |
| `skillpacks/loader.py` | 简化但保留（只需加载 name/description/body） |
| `skillpacks/manager.py` | 简化但保留（CRUD 接口） |
| `write_hint` 分类 | 属于安全层 |
| `hooks/` 系统 | 独立于 Skill 重设计 |
| `mcp/` 集成 | MCP 工具直接平铺在 core/extended 中 |

## 5. 迁移策略

### Phase 1: 引入 ToolProfile + 新元工具（非破坏性）

1. 新增 `tools/profile.py`
2. 新增 `expand_tools` 元工具
3. 新增 `activate_skill` 元工具（与现有 `select_skill` 并存）
4. 新增 `to_summary_schema()` 方法
5. 所有现有逻辑不动，新旧并存

**验证**: 全量测试通过 + bench 回归

### Phase 2: 切换 Schema 生成路径

1. engine 默认使用 `_build_tool_schemas()` 替代 `_get_current_tool_scope()`
2. 配置开关 `EXCELMANUS_USE_TOOL_PROFILE=true` 控制切换
3. 新旧路径可通过配置切换

**验证**: bench 对比测试（新路径 vs 旧路径）

### Phase 3: SKILL.md 格式迁移

1. 所有 SKILL.md 移除 `allowed_tools`、`triggers`、`priority`
2. Skill name 格式从 `snake_case` 迁移为 `kebab-case`（对齐标准）
3. `general_excel` Skill 删除（不再需要兜底）
4. `Skillpack` model 简化

**验证**: 全量测试通过

### Phase 4: 删除旧代码

1. 删除 `pre_router.py`
2. 删除 engine 中所有 scope/preroute/supplement 代码
3. 删除 `select_skill` / `discover_tools` / `list_skills` 旧元工具
4. 删除对应配置项
5. 删除对应测试

**验证**: 全量测试通过 + bench 全面回归

## 6. 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| LLM 看到摘要工具后不知道调用 `expand_tools` | `expand_tools` 的 description 中明确说明；摘要工具的 description 末尾附加提示 |
| 首轮额外一次 `expand_tools` 调用增加延迟 | core 工具集覆盖最常见的读取场景，大部分简单任务不需要 expand |
| bench 回归分数下降 | Phase 2 使用配置开关 A/B 测试，数据驱动决策 |
| 自定义 Skill 兼容性 | 迁移期间 loader 同时支持新旧格式，旧格式 `allowed_tools` 静默忽略 |
| 子代理工具限制受影响 | 子代理独立于 ToolProfile，`FilteredToolRegistry` 不变 |

## 7. 成功指标

- [ ] Skill 层代码完全对齐 Agent Skills 开放标准（可用 `skills-ref validate` 验证）
- [ ] ToolProfile 层与 Skill 层零耦合（可独立关闭任一层）
- [ ] 删除 ~1500+ 行路由/合并代码
- [ ] bench 回归分数不低于当前基线
- [ ] 首轮 token 消耗降低 ~40%（core schema ~1500 tokens vs 当前全量 ~6000 tokens）
