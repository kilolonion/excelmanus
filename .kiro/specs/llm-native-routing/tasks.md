# 实施计划：LLM-Native 路由与子代理架构重构

## 概述

将 ExcelManus 的路由架构从算法打分重构为 LLM-Native 路由。分为数据模型清理、SkillRouter 简化、AgentEngine 元工具集成、子代理重构、工具范围管理五个阶段，逐步推进。

## 任务

- [x] 1. 数据模型清理与 SkillRouter 简化
  - [x] 1.1 清理 models.py：删除 ForkPlan 类，从 SkillMatchResult 中移除 fork_plan 字段
    - 删除 `ForkPlan` dataclass
    - 从 `SkillMatchResult` 中移除 `fork_plan` 字段及默认值
    - 更新所有引用 `ForkPlan` 和 `fork_plan` 的导入和使用
    - _Requirements: 5.8, 5.9_

  - [x] 1.2 简化 SkillRouter.route 方法：删除算法打分逻辑，仅保留斜杠直连和 fallback
    - 删除 `_prefilter_candidates`、`_score_triggers`、`_score_description`、`_tokenize`、`_score_file_patterns` 方法
    - 删除 `_build_fork_plan`、`_decorate_result`、`_build_fork_context_hint`、`_build_large_file_fork_hint` 方法
    - 删除 `_llm_select`、`_filter_auto_routable_skillpacks` 方法
    - 删除 `confirm_with_llm` 参数及 `ConfirmWithLLM` 类型
    - 简化 `route()` 方法：斜杠命令 → 直连；非斜杠 → 返回 fallback 结果（全量工具 + 技能目录）
    - 将 `_build_skill_catalog` 提升为公开的 `build_skill_catalog` 实例方法，返回 `(catalog_text, skill_names)` 元组
    - _Requirements: 5.1-5.6, 5.10, 6.1-6.5_

  - [x] 1.3 编写属性测试：斜杠直连路由正确性和名称归一化
    - **Property 1: 斜杠直连路由正确性**
    - **Property 2: 斜杠命令名称归一化**
    - **Validates: Requirements 1.1, 1.2, 1.3**

- [x] 2. Checkpoint - 确保数据模型和路由简化测试通过
  - 确保所有测试通过，如有问题请向用户确认。

- [x] 3. AgentEngine 元工具集成
  - [x] 3.1 实现 `_build_meta_tools` 方法：生成 select_skill 和 explore_data 的 OpenAI 工具定义
    - 在 AgentEngine 中新增 `_build_meta_tools()` 方法
    - 调用 SkillRouter 的 `build_skill_catalog()` 获取技能目录和名称列表
    - 生成 `select_skill` 工具定义（含动态 skill_catalog 和 enum）
    - 生成 `explore_data` 工具定义（含适用/不适用场景描述）
    - 保留 `list_skills` 工具（已有，无需新增）
    - _Requirements: 2.1, 2.2, 2.6, 3.1, 3.6, 8.1-8.6_

  - [x] 3.2 编写属性测试：Skill_Catalog 完整性
    - **Property 3: Skill_Catalog 完整性**
    - **Validates: Requirements 2.2, 6.2, 8.2**

  - [x] 3.3 实现 `_handle_select_skill` 方法：处理技能选择工具调用
    - 新增 `_active_skill` 属性（`Skillpack | None`）
    - 加载技能上下文（`render_context()`），作为工具结果返回
    - 更新 `_active_skill` 和 `_loaded_skill_names`
    - 无效技能名返回错误提示
    - _Requirements: 2.3, 2.4, 2.5, 2.7_

  - [x] 3.4 编写属性测试：select_skill 调用正确性
    - **Property 4: select_skill 有效调用返回技能上下文**
    - **Property 5: select_skill 无效调用返回错误**
    - **Property 7: select_skill 记录到已加载集合**
    - **Validates: Requirements 2.3, 2.4, 2.7**

  - [x] 3.5 实现工具范围动态管理：`_get_current_tool_scope` 方法
    - 未激活状态：全量常规工具 + 元工具
    - 已激活技能状态：skill.allowed_tools + select_skill
    - 修改 `_tool_calling_loop` 中的工具获取逻辑，使用 `_get_current_tool_scope()`
    - _Requirements: 4.1, 4.2, 4.4_

  - [x] 3.6 编写属性测试：工具范围状态转换
    - **Property 6: 工具范围状态转换正确性**
    - **Validates: Requirements 2.5, 4.1, 4.2, 4.4, 6.5**

- [x] 4. Checkpoint - 确保元工具集成测试通过
  - 确保所有测试通过，如有问题请向用户确认。

- [x] 5. 子代理重构
  - [x] 5.1 重构子代理执行循环：将 `_execute_fork_plan_loop` 重构为 `_execute_subagent_loop`
    - 重命名方法，简化参数签名为 `(system_prompt, tool_scope, max_iterations)`
    - 删除对 `ForkPlan` 和 `SkillMatchResult` 的依赖
    - 保留核心循环逻辑（LLM 调用 → 工具执行 → 熔断检测）
    - _Requirements: 7.1, 7.2_

  - [x] 5.2 实现 `_handle_explore_data` 和 `_build_explorer_system_prompt`
    - 新增 `_build_explorer_system_prompt(task, file_paths)` 方法
    - 新增 `_handle_explore_data(task, file_paths)` 方法：构建系统提示 → 调用 `_execute_subagent_loop` → 返回摘要
    - 发出 SUBAGENT_START、SUBAGENT_SUMMARY、SUBAGENT_END 事件
    - _Requirements: 3.2-3.7, 7.4, 7.5_

  - [x] 5.3 编写属性测试：子代理只读工具集约束
    - **Property 8: 子代理只读工具集约束**
    - **Validates: Requirements 3.2, 4.3**

- [x] 6. 集成与清理
  - [x] 6.1 修改 `_execute_tool_call` 以拦截元工具调用
    - 在 `_execute_tool_call` 中检测 `select_skill` 和 `explore_data`
    - 元工具不走 ToolRegistry，直接调用对应 handler
    - 元工具调用后更新工具范围（重新构建 tools 列表）
    - _Requirements: 2.1, 3.1_

  - [x] 6.2 清理 AgentEngine 中的废弃代码
    - 删除 `_run_fork_subagent_if_needed` 方法
    - 删除 `_execute_fork_plan_loop` 方法（已被 `_execute_subagent_loop` 替代）
    - 删除 `_build_fork_system_prompt` 方法
    - 删除 `_attach_fork_summary` 方法
    - 删除 `_confirm_with_llm` 方法
    - 从 `chat()` 方法中移除 fork 子代理调用逻辑
    - 更新 `_route_skills` 移除 `confirm_with_llm` 参数
    - _Requirements: 5.7, 7.3, 7.4_

  - [x] 6.3 更新 `chat()` 编排流程
    - 移除 fork 子代理相关逻辑（`_run_fork_subagent_if_needed`、`_attach_fork_summary`）
    - 路由结果简化：不再包含 fork_plan
    - 工具列表构建改为使用 `_get_current_tool_scope()` + `_build_meta_tools()`
    - _Requirements: 4.1, 4.2, 9.1-9.7_

  - [x] 6.4 编写单元测试：元工具定义结构、子代理熔断、事件发射、向后兼容
    - 验证 select_skill 和 explore_data 的 JSON schema 结构
    - 验证子代理熔断和迭代上限行为
    - 验证事件发射正确性
    - 验证斜杠命令和控制命令向后兼容
    - _Requirements: 3.4, 3.5, 3.7, 8.4, 8.5, 9.1-9.7_

- [x] 7. 最终 Checkpoint - 确保所有测试通过
  - 运行 `pytest` 确保所有测试通过，如有问题请向用户确认。

## 备注

- 标记 `*` 的任务为可选测试任务，可跳过以加速 MVP
- 每个任务引用了具体的需求编号以确保可追溯性
- Checkpoint 任务确保增量验证
- 属性测试验证通用正确性属性，单元测试验证具体示例和边界情况
