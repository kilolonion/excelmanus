# 实施计划：v3-post-refactor-cleanup

## 概述

按照代码审查发现的 8 个问题，分 4 个任务完成清理。任务 1 处理 engine.py 核心重构（问题 1-3），任务 2 处理旧代码清理和解析器增强（问题 4-5），任务 3 处理路由器和配置增强（问题 6-7），任务 4 更新文档（问题 8）。

## 任务

- [x] 1. 重构 AgentEngine：拆分 chat()、统一授权、移除 inspect
  - [x] 1.1 提取 `_execute_tool_call()` 和 `_tool_calling_loop()` 方法，使 chat() 仅做编排
    - 从 chat() 提取单个工具调用逻辑到 `_execute_tool_call()`（参数解析、执行、事件发射）
    - 从 chat() 提取迭代循环体到 `_tool_calling_loop()`（LLM 请求、thinking 提取、工具遍历、熔断检测）
    - 移除手动 `tool_name not in allowed_tools` 检查，改为捕获 `ToolNotAllowedError` 并格式化为相同结构的 JSON 错误
    - 始终传递 `tool_scope` 给 `self._registry.call_tool()`
    - 删除 `_registry_supports_tool_scope()` 方法和 `import inspect`
    - 确保外部行为（返回值、事件顺序、异常类型）完全不变
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3_

  - [x] 1.2 编写属性测试：未授权工具调用和错误响应格式
    - **Property 1: 未授权工具调用抛出 ToolNotAllowedError**
    - **Property 2: 未授权工具错误响应格式正确**
    - 使用 hypothesis 生成随机工具名和 scope，验证异常和 JSON 错误字段
    - **Validates: Requirements 2.2, 2.3, 2.4**

- [x] 2. 清理旧 skills/ 目录 + 增强 Frontmatter 解析器
  - [x] 2.1 删除旧 skill 文件、替换 __init__.py、增强解析器
    - 删除 `excelmanus/skills/` 下所有 `*_skill.py` 文件
    - 将 `__init__.py` 替换为迁移提示模块（SkillRegistry 实例化时抛出 ImportError）
    - 增强 `_parse_scalar()` 支持引号字符串（单引号和双引号）
    - `_parse_frontmatter()` 对 `|`、`>`、`{` 开头值抛出 SkillpackValidationError
    - 新增 `_format_frontmatter()` 静态方法（字典 → frontmatter 文本）
    - 更新模块文档字符串记录支持的语法子集
    - _Requirements: 4.1, 4.2, 4.3, 5.1, 5.2, 5.3, 5.4, 5.5_

  - [x] 2.2 编写属性测试：解析器健壮性
    - **Property 3: 引号字符串解析正确**
    - **Property 4: 不支持的 frontmatter 语法抛出异常**
    - **Property 5: Frontmatter round-trip**
    - 使用 hypothesis 验证引号解析、非法语法拒绝、format→parse 往返一致性
    - **Validates: Requirements 5.1, 5.3, 5.5**

- [x] 3. 增强路由器语义匹配 + CORS 配置提取
  - [x] 3.1 新增 description 评分和 CORS 配置化
    - 在 SkillRouter 新增 `_tokenize()`（英文空格分词 + 中文字符级 bigram）和 `_score_description()`（词汇交集评分，每词 +1）
    - 在 `_prefilter_candidates()` 中调用 `_score_description()`
    - ExcelManusConfig 新增 `cors_allow_origins` 字段（tuple[str, ...]，默认 `("http://localhost:5173",)`）
    - `load_config()` 解析 `EXCELMANUS_CORS_ALLOW_ORIGINS` 环境变量（逗号分隔，空字符串 → 空 tuple）
    - api.py 从 config 读取 CORS 配置，将中间件添加移到 lifespan 内部
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 7.1, 7.2, 7.3, 7.4_

  - [x] 3.2 编写属性测试：路由器和 CORS
    - **Property 6: Description 词汇交集正向评分**
    - **Property 7: 中文 n-gram 分词包含所有 bigram**
    - **Property 8: Triggers 评分权重高于 description 单词评分**
    - **Property 9: CORS 环境变量逗号分隔解析**
    - **Validates: Requirements 6.1, 6.2, 6.3, 7.2**

- [x] 4. 运行全量测试

  - [x] 4.1 最终检查点 - 确保所有测试通过
    - 运行全量测试套件，确保无回归
    - 如有问题请询问用户

## 备注

- 标记 `*` 的子任务为可选属性测试，可跳过以加速交付
- 属性测试使用 hypothesis 库，每个测试至少 100 次迭代
