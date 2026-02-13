# 实现计划：跨会话持久记忆

## 概述

基于设计文档，将跨会话持久记忆功能拆分为增量式编码任务。每个任务构建在前一个任务之上，最终通过集成将所有组件连接到 AgentEngine。

## 任务

- [x] 1. 数据模型与配置扩展
  - [x] 1.1 创建 `excelmanus/memory_models.py`，定义 MemoryCategory 枚举、CATEGORY_TOPIC_MAP 映射和 MemoryEntry 数据类
    - MemoryCategory: FILE_PATTERN, USER_PREF, ERROR_SOLUTION, GENERAL
    - MemoryEntry: content, category, timestamp, source
    - CATEGORY_TOPIC_MAP: 类别到主题文件名的映射
    - _Requirements: 5.4, 6.2_
  - [x] 1.2 在 `excelmanus/config.py` 的 ExcelManusConfig 中新增 memory_enabled、memory_dir、memory_auto_load_lines 三个配置字段，并在 load_config() 中添加对应的环境变量解析逻辑
    - memory_enabled: bool, 默认 True, 环境变量 EXCELMANUS_MEMORY_ENABLED
    - memory_dir: str, 默认 "~/.excelmanus/memory", 环境变量 EXCELMANUS_MEMORY_DIR
    - memory_auto_load_lines: int, 默认 200, 环境变量 EXCELMANUS_MEMORY_AUTO_LOAD_LINES
    - _Requirements: 8.1, 8.2, 8.3_

- [x] 2. PersistentMemory 核心实现
  - [x] 2.1 创建 `excelmanus/persistent_memory.py`，实现 PersistentMemory 类的 __init__（目录自动创建）、load_core（读取前 N 行）、load_topic（按需读取主题文件）方法
    - __init__: 接收 memory_dir 和 auto_load_lines，使用 Path.mkdir(parents=True, exist_ok=True) 创建目录
    - load_core: 读取 MEMORY.md 前 auto_load_lines 行，文件不存在返回空字符串
    - load_topic: 读取指定主题文件全部内容，文件不存在返回空字符串
    - _Requirements: 1.1, 1.2, 1.3, 1.5, 2.1, 2.3, 2.4, 2.5_
  - [x] 2.2 实现 format_entries 和 parse_entries 方法，完成 MemoryEntry 与 Markdown 格式的双向转换
    - format_entries: 将 MemoryEntry 列表序列化为 Markdown，每条格式为 `### [YYYY-MM-DD HH:MM] category\n\ncontent\n\n---`
    - parse_entries: 解析 Markdown 文本为 MemoryEntry 列表，跳过格式不合规的条目
    - _Requirements: 6.1, 6.2, 6.3, 6.4_
  - [ ]* 2.3 编写属性测试：MemoryEntry 序列化往返一致性
    - **Property 2: MemoryEntry 序列化往返一致性**
    - 使用 hypothesis 生成随机 MemoryEntry 列表，验证 format_entries → parse_entries 往返等价
    - **Validates: Requirements 6.2, 6.3, 6.4, 5.4, 3.4**
  - [x] 2.4 实现 save_entries 方法，按类别将 MemoryEntry 分发写入对应文件（MEMORY.md / file_patterns.md / user_prefs.md）
    - 使用 CATEGORY_TOPIC_MAP 确定目标文件
    - general 和 error_solution 类别写入 MEMORY.md
    - 使用临时文件 + 原子重命名确保写入完整性
    - _Requirements: 3.3, 3.4, 4.2_
  - [ ]* 2.5 编写属性测试：记忆条目按类别分发到正确文件
    - **Property 3: 记忆条目按类别分发到正确文件**
    - 使用 hypothesis 生成随机类别的 MemoryEntry，验证保存后出现在正确文件中
    - **Validates: Requirements 3.3, 4.2**
  - [x] 2.6 实现 _enforce_capacity 方法，当文件超过 500 行时保留最近条目使其降至 400 行以内
    - 从文件末尾向前保留条目
    - 记录日志说明移除条目数
    - _Requirements: 7.1, 7.2, 7.3_
  - [ ]* 2.7 编写属性测试：容量管理保持行数上限
    - **Property 4: 容量管理保持行数上限**
    - 使用 hypothesis 生成超过 500 行的文件内容，验证清理后行数 ≤ 400 且保留最近条目
    - **Validates: Requirements 7.1, 7.2**
  - [ ]* 2.8 编写属性测试：核心记忆加载行数限制
    - **Property 1: 核心记忆加载行数限制**
    - 使用 hypothesis 生成随机行数的文件，验证 load_core 返回行数 = min(N, auto_load_lines)
    - **Validates: Requirements 2.1, 2.5**
  - [ ]* 2.9 编写属性测试：目录自动创建
    - **Property 5: 目录自动创建**
    - 使用 hypothesis 生成随机临时目录路径，验证初始化后目录存在
    - **Validates: Requirements 1.2**

- [x] 3. 检查点 - 确保所有测试通过
  - 确保所有测试通过，如有问题请向用户确认。

- [x] 4. MemoryExtractor 实现
  - [x] 4.1 创建 `excelmanus/memory_extractor.py`，实现 MemoryExtractor 类，包含 extract 方法（调用 LLM 分析对话历史并返回 MemoryEntry 列表）
    - 构造提取 prompt，指导 LLM 输出 JSON 格式的记忆条目
    - 对话为空或仅含系统消息时直接返回空列表
    - LLM 调用失败时记录错误日志并返回空列表
    - 解析 LLM 返回的 JSON 为 MemoryEntry 列表
    - _Requirements: 3.1, 3.2, 3.5, 3.6, 5.1, 5.2, 5.3, 5.4_
  - [ ]* 4.2 编写单元测试：MemoryExtractor 的 LLM mock 测试、空对话跳过、错误处理
    - 使用 mock 模拟 LLM 返回，验证解析逻辑
    - 验证空对话和仅系统消息时跳过提取
    - 验证 LLM 异常时返回空列表
    - _Requirements: 3.1, 3.2, 3.5, 3.6_

- [x] 5. 主题文件按需加载工具
  - [x] 5.1 创建 `excelmanus/tools/memory_tools.py`，实现 memory_read_topic 工具函数并注册到 ToolRegistry
    - 工具名: memory_read_topic
    - 参数: topic (str) — 支持 file_patterns、user_prefs
    - 返回对应主题文件的内容
    - 在 registry.py 的 register_builtin_tools 中注册
    - _Requirements: 4.1, 4.3, 4.4_

- [x] 6. AgentEngine 集成
  - [x] 6.1 修改 `excelmanus/engine.py`，在 AgentEngine.__init__ 中接收 PersistentMemory 和 MemoryExtractor 参数，会话启动时自动加载核心记忆到 system prompt
    - 新增 persistent_memory 和 memory_extractor 可选参数
    - 加载核心记忆并追加到 system prompt
    - 文件不存在或为空时跳过
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 8.4_
  - [x] 6.2 在 AgentEngine 中添加 extract_and_save_memory 异步方法，供会话结束时调用
    - 获取对话历史，调用 MemoryExtractor.extract
    - 将提取结果通过 PersistentMemory.save_entries 持久化
    - _Requirements: 3.1, 3.3_
  - [x] 6.3 修改 `excelmanus/session.py`，在会话删除和过期清理时调用 extract_and_save_memory
    - 在 delete 方法中调用
    - 在 cleanup_expired 方法中调用
    - _Requirements: 3.1_

- [x] 7. 工厂函数与入口集成
  - [x] 7.1 修改会话创建流程（session.py 的 get_or_create 和 cli.py），根据 config.memory_enabled 决定是否创建 PersistentMemory 和 MemoryExtractor 并传入 AgentEngine
    - memory_enabled 为 false 时传入 None，跳过所有记忆操作
    - _Requirements: 8.1, 8.4_
  - [ ]* 7.2 编写集成测试：验证 memory_enabled 开关、记忆加载注入、会话结束提取流程
    - _Requirements: 2.2, 8.4_

- [x] 8. 最终检查点 - 确保所有测试通过
  - 确保所有测试通过，如有问题请向用户确认。

## 备注

- 标记 `*` 的子任务为可选测试任务，可跳过以加速 MVP
- 每个任务引用了具体的需求编号以确保可追溯性
- 检查点确保增量验证
- 属性测试验证通用正确性属性，单元测试验证具体示例和边界条件
