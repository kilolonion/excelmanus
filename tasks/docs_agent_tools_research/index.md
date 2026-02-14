# Agent 工具系统对比研究

> **日期**：2026-02-14
> **范围**：Claude Code、Cursor、Windsurf/Cascade 与 ExcelManus 的工具系统对比

---

## 一、各平台工具清单概览

### 1.1 Claude Code（22+ 工具）

| 类别 | 工具 | 说明 |
|------|------|------|
| **文件操作** | ReadFile, Write, Edit | 读/写/精确替换三件套 |
| **搜索发现** | Glob, Grep | 文件名搜索 + 内容搜索 |
| **延迟加载** | ToolSearch | ⭐ 按需加载 deferred tools |
| **执行** | Bash | Shell 执行（含丰富安全指引） |
| **浏览器** | Computer | Chrome 自动化 |
| **规划** | EnterPlanMode, ExitPlanMode | ⭐ 显式规划/确认模式切换 |
| **任务管理** | TodoWrite, TaskCreate | 任务清单 + 子任务创建 |
| **子代理** | Task | ⭐ 启动专业子代理（explore/test-runner 等） |
| **技能** | Skill | 执行 slash command 技能 |
| **协作** | SendMessageTool, TeammateTool, TeamDelete | 多代理 Swarm 协作 |
| **交互** | AskUserQuestion | 向用户提问 |
| **等待** | Sleep | 等待/休眠（可被用户输入唤醒） |
| **Web** | WebFetch, WebSearch | 网页获取 + 搜索 |
| **IDE** | LSP, NotebookEdit | Language Server + Jupyter |

### 1.2 Windsurf/Cascade（24+ 工具）

| 类别 | 工具 | 说明 |
|------|------|------|
| **文件操作** | read_file, write_to_file, edit, multi_edit | ⭐ multi_edit 原子多处修改 |
| **搜索** | code_search, find_by_name, grep_search | ⭐ code_search 语义子代理 |
| **目录** | list_dir | 目录列表 |
| **执行** | run_command, command_status | 命令执行 + 异步状态查询 |
| **任务** | todo_list | 任务清单 |
| **记忆** | create_memory | ⭐ 持久记忆 CRUD |
| **交互** | ask_user_question | 预定义选项提问 |
| **Web** | read_url_content, view_content_chunk, search_web | 网页读取+搜索 |
| **部署** | deploy_web_app, read_deployment_config, check_deploy_status | 一键部署 |
| **预览** | browser_preview | Web 服务预览 |
| **历史** | trajectory_search | 对话历史搜索 |
| **Notebook** | read_notebook, edit_notebook | Jupyter 支持 |
| **MCP** | context7, excel, git, mongodb, sequential-thinking | MCP 服务器集成 |

### 1.3 Cursor Agent（~8 核心工具）

| 工具 | 说明 |
|------|------|
| read_file | 读取文件 |
| edit_file | 编辑文件 |
| list_dir | 目录列表 |
| search_files | 文件搜索 |
| codebase_search | ⭐ AI 语义代码搜索 |
| run_terminal_command | 终端命令 |
| browser_action | 浏览器操作 |
| file_search | grep 类搜索 |

### 1.4 ExcelManus（30+ 工具）

| 类别 | 工具 | 数量 |
|------|------|------|
| **数据操作** | read_excel, write_excel, analyze_data, filter_data, transform_data, scan_excel_files | 6 |
| **单元格** | write_cells, insert_rows, insert_columns | 3 |
| **格式化** | format_cells, adjust_column_width, adjust_row_height, merge_cells, unmerge_cells + 高级格式 | 5+ |
| **工作表** | create_sheet, copy_sheet, rename_sheet, delete_sheet, copy_range_between_sheets | 5 |
| **图表** | create_chart | 1 |
| **代码** | write_text_file, run_code | 2 |
| **Shell** | run_shell（白名单受限） | 1 |
| **文件** | copy_file, rename_file, delete_file | 3 |
| **任务** | task_create, task_update | 2 |
| **技能** | list_skills | 1 |
| **记忆** | memory_read_topic | 1 |
| **MCP** | 通过 MCPManager 动态注册 | N |

---

## 二、核心设计模式对比

### 2.1 工具加载策略

| 平台 | 策略 | ExcelManus 现状 |
|------|------|-----------------|
| **Claude Code** | ⭐ Deferred Loading：工具分为"立即可用"和"延迟加载"，通过 ToolSearch 按需加载 | 全量注入：30+ 工具 schema 一次性发给 LLM |
| **Windsurf** | 全量注入 + MCP 动态扩展 | 类似，Skillpack 做 tool_scope 过滤 |
| **Cursor** | 少量核心工具（~8 个），精简 | 工具数量远多于 Cursor |

**借鉴**：ExcelManus 已有 Skillpack 的 `allowed_tools` 过滤，但可进一步引入 **deferred loading** 模式：
- 将不常用工具（如 `insert_rows`, `insert_columns`, 高级格式工具）设为延迟加载
- 保留核心工具（read_excel, write_cells, run_code 等）为立即可用
- 可减少 ~40% 的 schema token 消耗

### 2.2 工具描述质量

| 平台 | 描述风格 | 特色 |
|------|---------|------|
| **Claude Code** | 极其详细，含 When to Use / When NOT to Use / Examples | Bash 工具 1067 tokens，TodoWrite 2167 tokens |
| **Windsurf** | 中等详细，含使用规范 | 重要约束嵌入描述 |
| **Cursor** | 简洁 | 依赖模型能力 |
| **ExcelManus** | 简短功能描述 | 缺少使用场景指引 |

**借鉴**：ExcelManus 应增强关键工具的描述，特别是：
- `run_code`：明确 "仅在专用工具无法完成时使用"（当前已有，可进一步强化）
- `task_create`：已有良好的使用场景描述（可保持）
- `read_excel` vs `scan_excel_files`：需要明确选择指引
- `write_cells` vs `write_excel`：需要明确单元格级 vs DataFrame 级的场景区分

### 2.3 规划与任务管理

| 平台 | 机制 | 特色 |
|------|------|------|
| **Claude Code** | EnterPlanMode + ExitPlanMode + TodoWrite | ⭐ 显式模式切换，用户确认后才执行 |
| **Windsurf** | todo_list（单一工具） | 无显式规划模式 |
| **Cursor** | 无显式规划工具 | Agent 模式自带多步规划 |
| **ExcelManus** | task_create + task_update | 有任务清单，无规划确认流程 |

**借鉴**：可考虑引入 **轻量规划模式**：
- 复杂任务（涉及写入 + 多步操作）时，先输出方案让用户确认
- 不需要专门工具，可通过系统提示词 + task_create 组合实现
- 已有的 accept gate 已覆盖了写入确认，规划模式侧重"整体方案确认"

### 2.4 子代理/委托机制

| 平台 | 机制 | 特色 |
|------|------|------|
| **Claude Code** | Task 工具 + 多种 subagent_type（explore/task/plan） | ⭐ 独立上下文窗口，可后台运行，可恢复 |
| **Windsurf** | code_search（语义搜索子代理） | 单一用途子代理 |
| **Cursor** | 无显式子代理 | 模型内部推理 |
| **ExcelManus** | delegate_to_subagent（当前仅 fork hint） | 仅规划，未真正实现 |

**借鉴**：
- Claude Code 的 Task 工具设计最完整，支持"独立上下文 + 自定义工具集 + 后台运行"
- ExcelManus 可优先实现 **探索型子代理**（类似 Windsurf 的 code_search）
- 在 Excel 场景下，子代理可用于"数据探索"（扫描多 sheet/多文件后汇总结论）

### 2.5 安全与审批

| 平台 | 机制 | 特色 |
|------|------|------|
| **Claude Code** | blast radius 思维 + 用户确认 | 按可逆性/影响范围分级 |
| **Windsurf** | SafeToAutoRun 标记 + 用户确认 | 命令级安全判断 |
| **Cursor** | 用户确认 | 简单二元确认 |
| **ExcelManus** | ApprovalManager + HIGH_RISK_TOOLS + /fullAccess | ⭐ 工具级分类 + 审计追踪 + 回滚能力 |

**评价**：ExcelManus 的审批系统是四个平台中 **最完善的**，已有：
- 工具级风险分类（HIGH_RISK_TOOLS）
- 完整审计链（before/after 快照、diff、manifest）
- 回滚能力（undo）
- MCP 工具白名单

### 2.6 持久记忆

| 平台 | 机制 | 特色 |
|------|------|------|
| **Claude Code** | CLAUDE.md 文件 + agent memory instructions | 基于文件的持久记忆 |
| **Windsurf** | create_memory（CRUD） | ⭐ 结构化记忆数据库 |
| **Cursor** | .cursorrules 文件 | 纯配置文件 |
| **ExcelManus** | memory_read_topic（只读） | 仅支持读取主题文件 |

**借鉴**：需要增加记忆写入能力，已在规划中。

---

## 三、高价值借鉴清单（按优先级排序）

### P0 - 立即可行

| # | 借鉴项 | 来源 | ExcelManus 落地方案 | 预期收益 |
|---|--------|------|---------------------|----------|
| 1 | **工具描述增强** | Claude Code | 为 read_excel / write_cells / run_code 等核心工具增加 When to Use / When NOT to Use 段落 | 减少工具选择错误 |
| 2 | **工具选择指引** | Claude Code Bash | 在 run_shell 描述中明确"优先使用专用工具"指引；在 run_code 中指引"先尝试 read_excel/write_cells" | 减少不必要的代码执行 |
| 3 | **并行调用指引** | Claude Code | 系统提示中增加：独立操作并行调用，依赖操作顺序执行 | 减少 LLM 轮次 |

### P1 - 中期优化

| # | 借鉴项 | 来源 | ExcelManus 落地方案 | 预期收益 |
|---|--------|------|---------------------|----------|
| 4 | **延迟工具加载** | Claude Code ToolSearch | 将 30+ 工具分为"核心 10 个"+"延迟加载 20 个"，通过 Skillpack auto-route 实现按需注入 | 减少 ~40% schema token |
| 5 | **结构化用户交互** | Windsurf ask_user_question | 增加 ask_user 工具，提供预定义选项 | 减少歧义交互轮次 |
| 6 | **记忆写入能力** | Windsurf create_memory | 完善 PersistentMemory 的 CRUD 接入 | 跨会话学习 |
| 7 | **轻量规划确认** | Claude Code EnterPlanMode | 复杂任务自动触发方案输出 + 用户确认 | 减少方向性错误 |

### P2 - 长期演进

| # | 借鉴项 | 来源 | ExcelManus 落地方案 | 预期收益 |
|---|--------|------|---------------------|----------|
| 8 | **探索型子代理** | Claude Code Task + Windsurf code_search | 实现 Excel 数据探索子代理（多文件扫描+汇总） | 复杂任务效率 |
| 9 | **后台任务** | Claude Code Task run_in_background | 支持耗时任务后台执行 + 状态查询 | 用户体验 |
| 10 | **多代理协作** | Claude Code Swarm | 多代理并行处理不同 sheet/文件 | 大规模 Excel 处理 |

---

## 四、ExcelManus 工具系统优势（值得保持的设计）

1. **领域专用工具丰富** — 30+ Excel 专用工具，远超通用 Agent 的文件操作能力
2. **审批与审计完善** — ApprovalManager 是所有平台中最完整的（含 diff、快照、回滚）
3. **Skillpack 路由** — 三层加载 + auto route + tool_scope 过滤，已有工具分级基础
4. **安全沙盒** — FileAccessGuard + Shell 白名单 + Python 沙盒（资源限制）
5. **MCP 集成** — 动态发现和注册远程工具的能力

---

## 五、核心架构差异总结

```
Claude Code 设计哲学：
  "少量通用工具 + 丰富描述 + 显式模式切换 + 子代理委托"
  → 工具数量少但描述极详细，依赖 LLM 判断力

Windsurf 设计哲学：
  "全量工具 + 语义搜索 + 持久记忆 + 部署集成"
  → 工具数量多但直接可用，侧重开发者工作流

Cursor 设计哲学：
  "极简工具集 + 强模型能力 + Agent 自治"
  → 最少工具数量，依赖模型推理

ExcelManus 设计哲学：
  "领域专用工具 + Skillpack 路由 + 审批审计"
  → 最多领域工具，需要优化工具发现和选择
```

**ExcelManus 最需改进的方向**：不是增加更多工具，而是 **让 LLM 更精准地选择和使用已有工具**。

具体行动：
1. 工具描述质量 → 减少选择错误
2. 工具分级加载 → 减少 token 消耗
3. 使用场景指引 → 减少不必要的 fallback 和轮次浪费
