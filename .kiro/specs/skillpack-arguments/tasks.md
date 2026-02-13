# 实现计划：Skillpack 参数化模板

## 概述

基于设计文档，按增量方式实现参数化模板功能。从数据模型扩展开始，逐步构建参数引擎、路由集成、CLI 扩展，每步都有测试覆盖。

## 任务

- [ ] 1. 扩展数据模型与 Loader
  - [ ] 1.1 在 Skillpack 数据模型中新增 `argument_hint` 字段
    - 修改 `excelmanus/skillpacks/models.py`，为 `Skillpack` dataclass 添加 `argument_hint: str = ""` 字段
    - 修改 `SkillMatchResult` dataclass 添加 `parameterized: bool = False` 字段
    - _Requirements: 1.1, 1.2, 4.1_

  - [ ] 1.2 在 SkillpackLoader 中解析 `argument_hint` frontmatter 字段
    - 修改 `excelmanus/skillpacks/loader.py` 的 `_parse_skillpack_file` 方法
    - 使用 `_get_optional_str` 解析 `argument_hint`，默认值为空字符串
    - 将解析结果传入 `Skillpack` 构造函数
    - _Requirements: 1.1, 1.2, 1.3_

  - [ ]* 1.3 编写 argument_hint 解析的属性测试
    - **Property 1: argument_hint 解析一致性**
    - **Property 2: 非字符串 argument_hint 触发校验错误**
    - **Validates: Requirements 1.1, 1.3**

- [ ] 2. 实现参数引擎（ArgumentEngine）
  - [ ] 2.1 创建 `excelmanus/skillpacks/arguments.py` 模块
    - 实现 `parse_arguments(raw: str) -> list[str]` 函数：空格分隔、引号包裹、未闭合容错
    - 实现 `substitute(template: str, args: list[str]) -> str` 函数：占位符替换逻辑
    - 使用正则表达式匹配 `$ARGUMENTS[N]`、`$ARGUMENTS`、`$N` 占位符
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 5.1, 5.2, 5.3_

  - [ ]* 2.2 编写参数解析的属性测试
    - **Property 5: 参数解析 round-trip**
    - **Validates: Requirements 5.1, 5.2**

  - [ ]* 2.3 编写占位符替换的属性测试
    - **Property 3: 占位符替换正确性**
    - **Property 4: 无占位符模板恒等**
    - **Validates: Requirements 2.1, 2.2, 2.5**

  - [ ]* 2.4 编写参数引擎的单元测试
    - 测试边界情况：空输入、引号未闭合、越界索引、替换后纯空白
    - _Requirements: 2.3, 2.4, 5.3, 6.2_

- [ ] 3. 检查点 - 确保所有测试通过
  - 确保所有测试通过，如有问题请向用户确认。

- [ ] 4. 扩展 SkillRouter 支持斜杠命令路由
  - [ ] 4.1 修改 `SkillRouter.route` 方法支持 `slash_command` 和 `raw_args` 参数
    - 修改 `excelmanus/skillpacks/router.py`
    - 当 `slash_command` 非空时：按名称直连匹配 Skillpack，调用参数引擎解析和替换，构建参数化的 `SkillMatchResult`
    - 当 `slash_command` 为空时：保持现有路由逻辑不变
    - _Requirements: 4.1, 4.3_

  - [ ] 4.2 修改 `AgentEngine.chat` 和 `_route_skills` 方法透传斜杠命令参数
    - 修改 `excelmanus/engine.py`，`chat` 方法新增 `slash_command` 和 `raw_args` 参数
    - 透传给 `_route_skills`，再透传给 `SkillRouter.route`
    - _Requirements: 4.1, 4.2_

  - [ ]* 4.3 编写路由器斜杠命令的属性测试
    - **Property 8: 路由器斜杠命令参数传递**
    - **Property 9: 自然语言路由行为不变**
    - **Validates: Requirements 4.1, 4.3**

- [ ] 5. 扩展 CLI 支持 Skillpack 斜杠命令
  - [ ] 5.1 修改 CLI 的 REPL 循环识别 Skillpack 斜杠命令
    - 修改 `excelmanus/cli.py` 的 `_repl_loop` 函数
    - 在内置斜杠命令处理之后、"未知命令"提示之前，检查是否匹配已加载的 Skillpack
    - 匹配时提取命令名和参数串，通过 `engine.chat` 传递 `slash_command` 和 `raw_args`
    - 无参数且 `argument_hint` 非空时显示提示信息
    - _Requirements: 3.1, 3.2, 6.1_

  - [ ] 5.2 扩展 `/help` 命令和自动补全
    - 修改 `_render_help` 函数，追加 Skillpack 命令列表及 `argument_hint`
    - 扩展 `_compute_inline_suggestion` 函数，支持 Skillpack 名称补全
    - 需要将已加载 Skillpack 名称列表传入补全逻辑（通过模块级变量或参数）
    - _Requirements: 3.3, 3.4, 3.5_

  - [ ]* 5.3 编写 CLI 斜杠命令的属性测试和单元测试
    - **Property 6: CLI 斜杠命令解析**
    - **Property 7: 自动补全匹配**
    - 单元测试：未知命令提示、/help 输出包含 Skillpack 命令
    - **Validates: Requirements 3.2, 3.3, 3.4, 3.5**

- [ ] 6. 更新 `__init__.py` 导出
  - 修改 `excelmanus/skillpacks/__init__.py`，导出 `parse_arguments` 和 `substitute`
  - _Requirements: 全局_

- [ ] 7. 最终检查点 - 确保所有测试通过
  - 确保所有测试通过，如有问题请向用户确认。

## 备注

- 标记 `*` 的子任务为可选测试任务，可跳过以加速 MVP
- 每个任务引用了具体的需求编号，确保可追溯性
- 检查点确保增量验证
- 属性测试验证通用正确性，单元测试验证边界情况
