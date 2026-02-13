# 需求文档：跨会话持久记忆

## 简介

为 ExcelManus 添加跨会话持久记忆功能，类似 Claude Code 的 auto-memory 机制。系统在会话结束时自动提取"值得记住的信息"并持久化到磁盘，在新会话启动时自动加载核心记忆到 system prompt，从而实现跨会话的知识积累与复用。

## 术语表

- **PersistentMemory**：持久记忆管理器，负责记忆文件的读写、加载与维护
- **MemoryExtractor**：记忆提取器，在会话结束时调用 LLM 从对话历史中提取值得记住的信息
- **MEMORY.md**：核心记忆文件，前 200 行自动加载到每个新会话的 system prompt
- **TopicFile**：主题文件（如 `file_patterns.md`、`user_prefs.md`），按需读取的专项记忆
- **MemoryEntry**：单条记忆条目，包含内容、来源、时间戳等元数据
- **AgentEngine**：ExcelManus 核心代理引擎，驱动 LLM 与工具之间的 Tool Calling 循环
- **ConversationMemory**：对话记忆管理器，管理单次会话内的多轮对话上下文
- **MemoryDir**：记忆存储目录，默认路径为 `~/.excelmanus/memory/`

## 需求

### 需求 1：记忆存储与目录结构

**用户故事：** 作为 ExcelManus 用户，我希望系统有一个统一的持久记忆存储位置，以便跨会话保留和复用知识。

#### 验收标准

1. THE PersistentMemory SHALL 使用 `~/.excelmanus/memory/` 作为默认记忆存储目录
2. WHEN MemoryDir 不存在时，THE PersistentMemory SHALL 自动创建该目录及所有必要的父目录
3. THE PersistentMemory SHALL 在 MemoryDir 中维护一个 `MEMORY.md` 核心记忆文件
4. THE PersistentMemory SHALL 在 MemoryDir 中支持多个 TopicFile，每个 TopicFile 为独立的 Markdown 文件
5. WHEN 用户通过环境变量 `EXCELMANUS_MEMORY_DIR` 指定自定义路径时，THE PersistentMemory SHALL 使用该自定义路径替代默认路径

### 需求 2：核心记忆自动加载

**用户故事：** 作为 ExcelManus 用户，我希望每次新会话自动加载之前积累的核心记忆，以便系统了解我的项目上下文和偏好。

#### 验收标准

1. WHEN 新会话启动时，THE AgentEngine SHALL 读取 `MEMORY.md` 的前 200 行内容
2. WHEN `MEMORY.md` 的前 200 行内容被读取后，THE AgentEngine SHALL 将该内容注入到会话的 system prompt 中
3. WHEN `MEMORY.md` 文件不存在时，THE AgentEngine SHALL 跳过记忆加载并正常启动会话
4. WHEN `MEMORY.md` 文件内容为空时，THE AgentEngine SHALL 跳过记忆注入并正常启动会话
5. WHEN `MEMORY.md` 文件内容少于 200 行时，THE AgentEngine SHALL 加载全部内容

### 需求 3：会话结束时自动提取记忆

**用户故事：** 作为 ExcelManus 用户，我希望系统在会话结束时自动从对话中提取有价值的信息，以便未来会话可以复用这些知识。

#### 验收标准

1. WHEN 会话结束时，THE MemoryExtractor SHALL 调用 LLM 分析当前会话的对话历史
2. WHEN LLM 分析完成后，THE MemoryExtractor SHALL 从分析结果中提取值得记住的 MemoryEntry 列表
3. WHEN MemoryEntry 列表非空时，THE PersistentMemory SHALL 将新条目追加到 `MEMORY.md` 文件末尾
4. WHEN 追加新条目时，THE PersistentMemory SHALL 为每条 MemoryEntry 添加时间戳标记
5. IF LLM 调用失败，THEN THE MemoryExtractor SHALL 记录错误日志并跳过本次记忆提取，不影响会话正常结束
6. IF 对话历史为空或仅包含系统消息，THEN THE MemoryExtractor SHALL 跳过记忆提取

### 需求 4：主题文件管理

**用户故事：** 作为 ExcelManus 用户，我希望系统能按主题分类存储记忆，以便在需要时按需加载特定领域的知识。

#### 验收标准

1. THE PersistentMemory SHALL 支持以下预定义 TopicFile：`file_patterns.md`（常见文件结构记录）和 `user_prefs.md`（用户偏好）
2. WHEN MemoryExtractor 提取到与特定主题相关的记忆时，THE PersistentMemory SHALL 将该记忆写入对应的 TopicFile 而非 `MEMORY.md`
3. WHEN AgentEngine 处理与 Excel 文件结构相关的任务时，THE AgentEngine SHALL 按需读取 `file_patterns.md` 的内容
4. WHEN AgentEngine 处理与图表样式或输出格式相关的任务时，THE AgentEngine SHALL 按需读取 `user_prefs.md` 的内容

### 需求 5：记忆内容分类

**用户故事：** 作为 ExcelManus 用户，我希望系统能智能识别不同类型的有价值信息，以便记忆内容结构化且易于检索。

#### 验收标准

1. THE MemoryExtractor SHALL 识别并提取以下类别的信息：项目中常用的 Excel 文件结构（列名、数据类型、行数量级）
2. THE MemoryExtractor SHALL 识别并提取以下类别的信息：用户偏好的图表样式和输出格式
3. THE MemoryExtractor SHALL 识别并提取以下类别的信息：常见错误的解决方案
4. WHEN 提取记忆时，THE MemoryExtractor SHALL 为每条 MemoryEntry 标注所属类别（file_pattern、user_pref、error_solution、general）

### 需求 6：记忆文件格式与序列化

**用户故事：** 作为 ExcelManus 用户，我希望记忆文件使用人类可读的格式存储，以便我可以手动查看和编辑记忆内容。

#### 验收标准

1. THE PersistentMemory SHALL 使用 Markdown 格式存储所有记忆文件
2. WHEN 写入 MemoryEntry 时，THE PersistentMemory SHALL 使用统一的条目格式，包含时间戳、类别标签和内容正文
3. WHEN 读取记忆文件时，THE PersistentMemory SHALL 将 Markdown 内容解析为结构化的 MemoryEntry 列表
4. FOR ALL 有效的 MemoryEntry 列表，将其序列化为 Markdown 再解析回 MemoryEntry 列表 SHALL 产生等价的结果（往返一致性）

### 需求 7：记忆容量管理

**用户故事：** 作为 ExcelManus 用户，我希望系统能自动管理记忆容量，以便记忆文件不会无限增长。

#### 验收标准

1. WHEN `MEMORY.md` 文件超过 500 行时，THE PersistentMemory SHALL 触发容量管理策略
2. WHEN 容量管理策略触发时，THE PersistentMemory SHALL 保留最近的条目并移除最早的条目，使文件行数降至 400 行以内
3. WHEN 移除旧条目时，THE PersistentMemory SHALL 记录日志说明移除了多少条目

### 需求 8：配置集成

**用户故事：** 作为 ExcelManus 用户，我希望通过环境变量控制持久记忆功能的行为，以便灵活调整功能参数。

#### 验收标准

1. THE ExcelManusConfig SHALL 新增 `memory_enabled` 配置项（默认值为 `true`），通过环境变量 `EXCELMANUS_MEMORY_ENABLED` 控制
2. THE ExcelManusConfig SHALL 新增 `memory_dir` 配置项（默认值为 `~/.excelmanus/memory/`），通过环境变量 `EXCELMANUS_MEMORY_DIR` 控制
3. THE ExcelManusConfig SHALL 新增 `memory_auto_load_lines` 配置项（默认值为 `200`），通过环境变量 `EXCELMANUS_MEMORY_AUTO_LOAD_LINES` 控制
4. WHEN `memory_enabled` 为 `false` 时，THE AgentEngine SHALL 跳过所有记忆加载和提取操作
