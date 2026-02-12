# 需求文档

## 简介

ExcelManus v3 重构后代码审查发现了 8 个待清理问题，涵盖方法过长、逻辑重复、无用代码残留、解析器健壮性不足、路由匹配能力弱、配置硬编码以及文档过时等方面。本需求文档定义了逐一修复这些问题的验收标准。

## 术语表

- **AgentEngine**：`excelmanus/engine.py` 中的核心代理引擎类，负责 Tool Calling 循环编排
- **ToolRegistry**：`excelmanus/tools/registry.py` 中的工具注册中心，管理工具定义、schema 输出与调用执行
- **SkillRouter**：`excelmanus/skillpacks/router.py` 中的 Skillpack 路由器，负责根据用户消息匹配合适的 Skillpack
- **SkillpackLoader**：`excelmanus/skillpacks/loader.py` 中的 Skillpack 加载器，负责解析 SKILL.md 文件的 frontmatter 和正文
- **Frontmatter_Parser**：SkillpackLoader 内部的 `_parse_frontmatter()` 和 `_parse_scalar()` 方法，用于解析 SKILL.md 文件头部的 YAML-like 元数据
- **Prefilter**：SkillRouter 内部的 `_prefilter_candidates()` 方法，用于对 Skillpack 进行预筛选评分
- **ExcelManusConfig**：`excelmanus/config.py` 中的全局配置数据类
- **ToolNotAllowedError**：ToolRegistry 在工具未授权时抛出的异常
- **Steering_Files**：`.kiro/steering/` 目录下的 `product.md`、`structure.md`、`tech.md` 文件，为 AI 助手提供项目上下文

## 需求

### 需求 1：拆分 AgentEngine.chat() 方法

**用户故事：** 作为开发者，我希望 AgentEngine.chat() 方法被拆分为职责清晰的子方法，以便代码更易于理解和维护。

#### 验收标准

1. THE AgentEngine SHALL 提供 `_execute_tool_call()` 私有方法，负责单个工具调用的参数解析、执行和事件发射
2. THE AgentEngine SHALL 提供 `_tool_calling_loop()` 私有方法，负责迭代循环体（包含 LLM 请求、工具调用遍历、熔断检测）
3. WHEN chat() 被调用时，THE AgentEngine SHALL 仅负责路由、消息管理和调用 `_tool_calling_loop()` 的编排逻辑
4. WHEN 拆分完成后，THE AgentEngine SHALL 保持与拆分前完全一致的外部行为（返回值、事件发射顺序、异常类型）

### 需求 2：统一授权检查逻辑

**用户故事：** 作为开发者，我希望工具授权检查逻辑只存在于一处，以便消除重复代码并降低不一致风险。

#### 验收标准

1. THE AgentEngine SHALL 移除 chat() 方法中手动的 `tool_name not in allowed_tools` 检查逻辑
2. WHEN 工具调用时，THE AgentEngine SHALL 始终通过 ToolRegistry.call_tool() 的 tool_scope 参数进行授权检查
3. WHEN ToolRegistry 抛出 ToolNotAllowedError 时，THE AgentEngine SHALL 捕获该异常并格式化为与当前相同结构的 JSON 错误响应
4. WHEN 未授权工具被调用时，THE AgentEngine SHALL 产生与重构前一致的错误消息内容和事件

### 需求 3：移除 _registry_supports_tool_scope() 的 inspect 检测

**用户故事：** 作为开发者，我希望移除不必要的运行时签名检测，以便简化代码并消除对 inspect 模块的依赖。

#### 验收标准

1. THE AgentEngine SHALL 移除 `_registry_supports_tool_scope()` 方法
2. WHEN 调用 ToolRegistry.call_tool() 时，THE AgentEngine SHALL 始终传递 tool_scope 参数
3. WHEN 移除完成后，THE AgentEngine SHALL 不再导入或使用 `inspect` 模块（除非其他功能需要）

### 需求 4：清理旧 skills/ 目录

**用户故事：** 作为开发者，我希望旧的 skills/ 目录被清理，以便消除对已废弃代码的混淆。

#### 验收标准

1. THE excelmanus/skills/ 目录 SHALL 仅保留 `__init__.py` 文件
2. WHEN 外部代码导入 `excelmanus.skills` 中的旧类名（如 SkillRegistry）时，THE `__init__.py` SHALL 抛出 ImportError 并附带迁移提示信息
3. THE excelmanus/skills/ 目录 SHALL 移除所有 `*_skill.py` 模块文件（chart_skill.py、data_skill.py、file_skill.py、format_skill.py）

### 需求 5：增强 Frontmatter 解析器健壮性

**用户故事：** 作为开发者，我希望 Skillpack 的 frontmatter 解析器能处理更多 YAML 语法场景，以便用户自定义 Skillpack 时不易出错。

#### 验收标准

1. THE Frontmatter_Parser SHALL 正确解析带引号的字符串值（单引号和双引号）
2. THE Frontmatter_Parser SHALL 正确解析包含冒号的值（如 URL `https://example.com`）
3. IF frontmatter 包含不支持的语法（多行字符串、嵌套对象）时，THEN THE Frontmatter_Parser SHALL 抛出 SkillpackValidationError 并附带明确的错误描述
4. THE Frontmatter_Parser SHALL 将支持的语法子集记录在 SkillpackLoader 的模块文档字符串中
5. THE Frontmatter_Parser SHALL 对输入进行格式化输出（pretty print），FOR ALL 合法的 frontmatter 字典，解析再格式化再解析 SHALL 产生等价的字典对象（round-trip 属性）

### 需求 6：增强路由器预筛选的语义匹配能力

**用户故事：** 作为开发者，我希望 SkillRouter 的预筛选能基于描述文本进行简单的语义匹配，以便用户使用同义词时也能正确路由到对应 Skillpack。

#### 验收标准

1. WHEN 用户消息与 Skillpack 的 triggers 不匹配但与 description 有词汇交集时，THE Prefilter SHALL 为该 Skillpack 给予正向评分
2. THE Prefilter SHALL 对中文文本进行基于字符级 n-gram 的分词，以支持无空格的中文查询匹配
3. WHEN triggers 精确匹配时，THE Prefilter SHALL 给予比 description 匹配更高的评分权重
4. WHEN 预筛选评分逻辑变更后，THE SkillRouter SHALL 保持与变更前一致的 route_mode 决策流程

### 需求 7：将 CORS 配置提取到 ExcelManusConfig

**用户故事：** 作为开发者，我希望 API 的 CORS 允许来源列表可通过环境变量配置，以便部署时无需修改代码。

#### 验收标准

1. THE ExcelManusConfig SHALL 包含 `cors_allow_origins` 字段，类型为字符串列表，默认值为 `["http://localhost:5173"]`
2. WHEN 环境变量 `EXCELMANUS_CORS_ALLOW_ORIGINS` 存在时，THE load_config() SHALL 将其按逗号分隔解析为来源列表
3. WHEN API 服务启动时，THE api.py SHALL 从 ExcelManusConfig 读取 CORS 允许来源列表，而非使用硬编码值
4. IF 环境变量值为空字符串时，THEN THE load_config() SHALL 将 cors_allow_origins 设为空列表

### 需求 8：更新 Steering 文件

**用户故事：** 作为开发者，我希望 `.kiro/steering/` 下的文档反映 v3 的实际架构，以便 AI 助手获得正确的项目上下文。

#### 验收标准

1. THE product.md SHALL 将架构描述从"基于 LangChain ReAct Agent"更新为"基于 OpenAI SDK 原生 Responses API + Tools/Skillpacks 双层架构"
2. THE product.md SHALL 将版本号更新为 3.0.0
3. THE structure.md SHALL 将项目根目录从 `excelagent/` 更新为 `excelmanus/`，并反映当前的目录结构（包含 tools/、skillpacks/、config.py、engine.py、api.py、cli.py 等）
4. THE tech.md SHALL 将核心依赖从 langchain/langchain-openai 更新为 openai SDK、fastapi、uvicorn，并保留 pandas、openpyxl、matplotlib、pydantic 等不变的依赖
5. THE tech.md SHALL 将常用命令更新为当前实际的启动方式
