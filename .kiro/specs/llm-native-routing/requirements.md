# 需求文档：LLM-Native 路由与子代理架构重构

## 简介

ExcelManus 当前使用算法打分（trigger/description 词汇匹配）做路由决策，无法理解用户意图。本次重构将路由决策权从算法交还给 LLM，对齐 Claude Code、Cursor、Google ADK 等行业方案，采用"LLM 决策 + 工具化子代理"的范式。

核心变更：
- 删除 router.py 中的算法打分逻辑（prefilter、trigger scoring、description scoring、file pattern scoring）
- 新增 `select_skill` 元工具，让 LLM 自主选择技能包
- 新增 `explore_data` 子代理工具，让 LLM 自主决定是否启动只读数据探查
- 新增 `list_skills` 元工具（已有工具，保留）
- 实现三态工具范围动态管理（未激活 / 已激活技能 / 子代理执行中）

## 术语表

- **AgentEngine**：核心代理引擎，驱动 LLM 与工具之间的 Tool Calling 循环（engine.py）
- **SkillRouter**：技能路由器，负责将用户消息分派到合适的 Skillpack（router.py）
- **SkillpackLoader**：技能包加载器，扫描并解析 SKILL.md 文件（loader.py）
- **Skillpack**：技能包定义，包含 name、description、allowed_tools、triggers、instructions 等字段
- **SkillMatchResult**：路由结果数据模型，包含 skills_used、tool_scope、route_mode、system_contexts 等
- **ForkPlan**：子代理执行计划数据模型（将被删除）
- **select_skill**：元工具，LLM 通过调用此工具选择激活一个技能包
- **explore_data**：子代理工具，LLM 通过调用此工具启动只读数据探查子代理
- **list_skills**：工具，列出所有可用技能详情
- **Tool_Scope**：工具范围，定义当前 LLM 可调用的工具集合
- **斜杠直连**：用户通过 `/skill_name args` 语法直接分派到指定技能，不经过 LLM 路由
- **Skill_Catalog**：技能目录摘要文本，嵌入到 select_skill 工具描述中供 LLM 参考

## 需求

### 需求 1：斜杠直连路由保留

**用户故事：** 作为用户，我希望通过 `/skill_name args` 语法直接调用指定技能，以便绕过 LLM 路由实现零延迟分派。

#### 验收标准

1. WHEN 用户输入以 `/` 开头且匹配已注册技能名称时，THE SkillRouter SHALL 直接加载对应 Skillpack 并执行参数化分派，不经过 LLM 路由
2. WHEN 用户输入以 `/` 开头但未匹配任何已注册技能名称时，THE SkillRouter SHALL 返回 slash_not_found 路由模式
3. THE SkillRouter SHALL 保留现有的斜杠命令名称归一化逻辑（小写化、移除连字符和下划线）

### 需求 2：select_skill 元工具

**用户故事：** 作为 LLM，我希望通过 `select_skill` 元工具自主选择激活技能包，以便根据用户意图做出准确的路由决策。

#### 验收标准

1. THE AgentEngine SHALL 在每轮对话开始时将 `select_skill` 元工具包含在 LLM 可用工具集中
2. WHEN AgentEngine 构建 `select_skill` 工具定义时，THE AgentEngine SHALL 动态生成 Skill_Catalog（包含所有可用技能的 name 和 description），并嵌入到工具描述中
3. WHEN LLM 调用 `select_skill` 并传入有效的 skill_name 时，THE AgentEngine SHALL 加载对应 Skillpack 的上下文（通过 render_context），并将其作为工具调用结果返回给 LLM
4. WHEN LLM 调用 `select_skill` 并传入无效的 skill_name 时，THE AgentEngine SHALL 返回错误提示"未找到技能: {skill_name}"
5. WHEN `select_skill` 成功激活技能后，THE AgentEngine SHALL 将后续工具范围限定为该技能的 allowed_tools 加上 `select_skill`（允许切换技能）
6. THE `select_skill` 工具描述 SHALL 明确指示 LLM：如果用户只是闲聊、询问能力或打招呼，不要调用此工具，直接回复即可
7. WHEN `select_skill` 被调用时，THE AgentEngine SHALL 将该技能名称记录到会话级已加载技能集合中

### 需求 3：explore_data 子代理工具

**用户故事：** 作为 LLM，我希望通过 `explore_data` 工具自主决定是否启动只读数据探查子代理，以便在操作前了解数据结构和质量。

#### 验收标准

1. THE AgentEngine SHALL 在每轮对话开始时将 `explore_data` 工具包含在 LLM 可用工具集中
2. WHEN LLM 调用 `explore_data` 时，THE AgentEngine SHALL 在独立上下文中启动子代理，仅提供只读工具集（read_excel、analyze_data、filter_data、list_sheets、get_file_info、find_files、list_directory、read_text_file、read_cell_styles）
3. WHEN 子代理执行完成后，THE AgentEngine SHALL 将子代理输出的摘要作为工具调用结果返回给主 LLM
4. WHEN 子代理连续工具调用失败次数达到配置的熔断阈值时，THE AgentEngine SHALL 提前终止子代理并返回错误摘要
5. WHEN 子代理达到最大迭代次数时，THE AgentEngine SHALL 终止子代理并返回有限摘要
6. THE `explore_data` 工具描述 SHALL 明确列出适用场景（大体量文件、未知结构文件、复杂数据质量问题）和不适用场景（用户已明确告知结构、简单读取、仅询问能力）
7. THE AgentEngine SHALL 在子代理执行期间发出 SUBAGENT_START、SUBAGENT_SUMMARY、SUBAGENT_END 事件

### 需求 4：工具范围动态管理

**用户故事：** 作为系统架构师，我希望工具范围根据当前状态动态调整，以便在不同阶段为 LLM 提供合适的工具集。

#### 验收标准

1. WHILE 未激活任何技能时，THE AgentEngine SHALL 向 LLM 提供全量常规工具加上 select_skill、explore_data、list_skills 元工具
2. WHILE 已激活技能时，THE AgentEngine SHALL 将工具范围限定为该技能的 allowed_tools 加上 select_skill（允许切换技能）
3. WHILE 子代理执行中时，THE AgentEngine SHALL 将子代理的工具范围限定为只读工具集
4. WHEN LLM 在已激活技能状态下再次调用 `select_skill` 时，THE AgentEngine SHALL 切换到新技能的工具范围
5. THE AgentEngine SHALL 保留现有的会话级技能累积逻辑（_merge_with_loaded_skills），确保历史已加载技能的工具在后续轮次中仍然可用

### 需求 5：删除算法打分路由逻辑

**用户故事：** 作为开发者，我希望删除所有基于算法打分的路由逻辑，以便简化代码并消除意图误判问题。

#### 验收标准

1. THE SkillRouter SHALL 删除 _prefilter_candidates 方法及其所有调用
2. THE SkillRouter SHALL 删除 _score_triggers 方法
3. THE SkillRouter SHALL 删除 _score_description 方法及 _tokenize 辅助方法
4. THE SkillRouter SHALL 删除 _score_file_patterns 方法
5. THE SkillRouter SHALL 删除 confident_direct 和 llm_confirm 路由路径
6. THE SkillRouter SHALL 删除 _build_fork_plan 方法和 _decorate_result 方法
7. THE AgentEngine SHALL 删除 _run_fork_subagent_if_needed 方法
8. THE 数据模型 SHALL 删除 ForkPlan 类
9. THE SkillMatchResult SHALL 删除 fork_plan 字段
10. THE SkillRouter 的 route 方法 SHALL 简化为仅保留斜杠直连路由和 Skill_Catalog 生成逻辑

### 需求 6：SkillRouter 简化重构

**用户故事：** 作为开发者，我希望 SkillRouter 简化为仅负责斜杠直连和技能目录生成，以便职责清晰、代码精简。

#### 验收标准

1. THE SkillRouter SHALL 保留斜杠直连路由功能（_find_skill_by_name、_build_parameterized_result）
2. THE SkillRouter SHALL 提供 build_skill_catalog 方法，生成所有可用技能的摘要目录文本（name + description）
3. THE SkillRouter SHALL 保留 _collect_candidate_file_paths 和 _extract_excel_paths 方法（供 explore_data 使用）
4. THE SkillRouter SHALL 删除 confirm_with_llm 回调参数及相关的 LLM 确认逻辑
5. WHEN 非斜杠命令消息到达时，THE SkillRouter SHALL 返回包含全量工具范围的 fallback 结果，不再执行算法打分

### 需求 7：子代理执行机制重构

**用户故事：** 作为开发者，我希望将子代理从规则自动触发改为 LLM 工具调用触发，以便 LLM 拥有完全的决策权。

#### 验收标准

1. THE AgentEngine SHALL 将 _execute_fork_plan_loop 方法重命名为 _execute_subagent_loop，保留核心循环逻辑
2. WHEN _execute_subagent_loop 执行时，THE AgentEngine SHALL 使用独立的 ConversationMemory 和只读工具集
3. THE AgentEngine SHALL 删除 _attach_fork_summary 方法（摘要改为通过工具调用结果返回）
4. THE AgentEngine SHALL 删除 _build_fork_system_prompt 方法，新增 _build_explorer_system_prompt 方法用于构建探查子代理的系统提示
5. THE _build_explorer_system_prompt SHALL 包含任务描述、文件路径、只读约束和输出格式要求

### 需求 8：元工具定义生成

**用户故事：** 作为开发者，我希望 AgentEngine 能动态生成元工具（select_skill、explore_data）的 OpenAI 工具定义，以便 LLM 在 Tool Calling 循环中使用。

#### 验收标准

1. THE AgentEngine SHALL 提供 _build_meta_tools 方法，返回 select_skill 和 explore_data 的 OpenAI 工具定义列表
2. WHEN 构建 select_skill 工具定义时，THE _build_meta_tools SHALL 动态填充 skill_name 参数的 enum 值为所有可用技能名称
3. WHEN 构建 select_skill 工具定义时，THE _build_meta_tools SHALL 将 Skill_Catalog 嵌入到工具描述中
4. THE select_skill 工具定义 SHALL 包含 skill_name（必填）和 reason（选填）两个参数
5. THE explore_data 工具定义 SHALL 包含 task（必填）和 file_paths（选填）两个参数
6. WHEN 技能包列表发生变化时（如加载新技能），THE _build_meta_tools SHALL 反映最新的技能目录

### 需求 9：向后兼容性

**用户故事：** 作为用户，我希望重构后的系统保持现有功能的向后兼容，以便平滑过渡。

#### 验收标准

1. THE 重构 SHALL 保留 SKILL.md 格式和 SkillpackLoader 的三层加载机制不变
2. THE 重构 SHALL 保留所有现有工具实现（read_excel、write_excel 等）不变
3. THE 重构 SHALL 保留 /fullAccess 权限控制机制不变
4. THE 重构 SHALL 保留会话管理和 ConversationMemory 机制不变
5. THE 重构 SHALL 保留事件系统（EventType、EventCallback）不变
6. THE 重构 SHALL 保留控制命令（/fullAccess、/subagent）不变
7. WHEN 用户使用斜杠命令调用技能时，THE 系统 SHALL 产生与重构前一致的行为
