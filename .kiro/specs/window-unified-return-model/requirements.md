# 需求文档：Window Unified Return Model (WURM)

## 简介

WURM 将 Window 从"工具结果的元数据叠加层"升级为"工具结果的统一数据容器"。工具不再向对话历史返回原始数据，而是将数据写入 Window；Agent 通过读取 Window 获取所有信息。对话历史仅保留轻量操作确认日志（约 30-50 tokens），不保留数据本身，从而大幅降低上下文 token 消耗（预计节省约 70%）。

## 术语表

- **Window**：与一个 (file, sheet) 对应的数据视口，包含结构信息和数据缓冲区
- **WindowManager**：窗口生命周期管理器（`WindowPerceptionManager`），负责窗口的创建、更新、降级和销毁
- **data_buffer**：Window 内存中的完整数据缓冲区，始终保持完整数据，降级仅影响序列化输出
- **detail_level**：序列化精度级别（FULL / SUMMARY / ICON），控制 Window 在 system notice 中的渲染详细程度
- **intent_tag**：窗口意图标签（aggregate / format / validate / formula / entry / general），控制序列化维度偏好
- **ingest**：将工具返回的原始数据解析并写入对应 Window 的 data_buffer 的过程
- **操作确认（confirmation）**：工具执行后写入对话历史的轻量文本（约 30-50 tokens），替代当前的完整数据返回
- **window_return_mode**：配置项，控制工具返回值处理模式（unified / anchored / enriched）
- **Engine**：核心 Agent 引擎（`AgentEngine`），负责 LLM 循环和工具调度
- **循环检测（loop detection）**：安全阀机制，检测 Agent 对同一 (file, sheet) 的重复读取行为

## 需求

### 需求 1：Window 数据模型扩展

**用户故事：** 作为 Agent 引擎开发者，我希望 Window 拥有完整的数据缓冲区和结构信息，以便 Window 成为工具数据的统一容器。

#### 验收标准

1. THE WindowState SHALL 包含 data_buffer 字段，用于在内存中保持工具返回的完整数据
2. THE WindowState SHALL 包含 schema 字段，用于记录列名、列类型等结构信息
3. THE WindowState SHALL 包含 detail_level 字段，取值为 FULL、SUMMARY 或 ICON，用于控制序列化输出精度
4. WHEN detail_level 发生变化时，THE WindowManager SHALL 仅改变序列化输出，data_buffer 中的完整数据保持不变
5. WHEN detail_level 从 SUMMARY 或 ICON 提升为 FULL 时，THE WindowManager SHALL 直接从 data_buffer 渲染完整数据，无需重新调用工具

### 需求 2：工具结果拦截与数据注入

**用户故事：** 作为 Agent 引擎开发者，我希望在 Engine 层拦截工具返回值并将数据注入 Window，以便对话历史不再包含原始数据。

#### 验收标准

1. WHEN 工具执行成功并返回结果时，THE WindowManager SHALL 通过 ingest 方法解析工具返回的数据并写入对应 Window 的 data_buffer
2. WHEN ingest 完成后，THE WindowManager SHALL 生成一条操作确认文本（约 30-50 tokens）替代原始工具返回值写入对话历史
3. THE 操作确认文本 SHALL 包含窗口标识、操作类型、影响范围和数据已融入窗口的提示
4. WHEN 工具执行失败时，THE Engine SHALL 将原始错误信息直接写入对话历史，不经过 ingest 流程
5. THE 工具层（tools/）SHALL 保持零改动，所有变化仅发生在 Engine 层的拦截点

### 需求 3：三级序列化策略

**用户故事：** 作为 Agent 引擎开发者，我希望 Window 根据活跃状态采用不同精度的序列化输出，以便在保证信息可用性的同时最小化 token 消耗。

#### 验收标准

1. WHILE Window 处于 ACTIVE 状态时，THE 序列化器 SHALL 以 FULL 精度渲染，输出完整数据表格（约 200-500 tokens）
2. WHILE Window 处于 BACKGROUND 状态时，THE 序列化器 SHALL 以 SUMMARY 精度渲染，输出摘要信息（约 40-80 tokens）
3. WHILE Window 处于 SUSPENDED 状态时，THE 序列化器 SHALL 以 ICON 精度渲染，输出图标级信息（约 15-25 tokens）
4. WHEN Window 从 SUSPENDED 恢复为 ACTIVE 时，THE 序列化器 SHALL 从 data_buffer 直接渲染 FULL 精度输出，无需重新调用工具
5. THE 序列化器 SHALL 对每种精度级别的输出进行 token 估算，确保不超出对应预算范围

### 需求 4：增量写入与数据合并

**用户故事：** 作为 Agent 引擎开发者，我希望 Window 支持增量数据写入和智能合并，以便多次工具调用的结果能正确累积在同一 Window 中。

#### 验收标准

1. WHEN 新数据范围与 data_buffer 中已有范围连续或重叠时，THE WindowManager SHALL 合并去重，保留最新数据
2. WHEN 新数据范围与 data_buffer 中已有范围不连续时，THE WindowManager SHALL 替换视口为新数据范围
3. WHEN 工具操作切换到同一文件的不同 Sheet 时，THE WindowManager SHALL 创建新的 Window 实例
4. WHEN 工具执行筛选操作时，THE WindowManager SHALL 将 data_buffer 更新为筛选后的子集
5. WHEN 工具执行写入操作时，THE WindowManager SHALL 在 data_buffer 中原地更新受影响的单元格
6. WHEN data_buffer 中的行数超过 max_cached_rows 阈值时，THE WindowManager SHALL 按 LRU 策略淘汰最早未访问的行

### 需求 5：操作确认格式

**用户故事：** 作为 Agent 引擎开发者，我希望操作确认文本格式统一且信息密度高，以便 Agent 能从确认文本中获取足够的操作上下文。

#### 验收标准

1. THE 操作确认文本 SHALL 遵循统一格式：`✅ [W{id}: {filename} / {sheet}] {操作描述}: {范围} | {行}行×{列}列 | {变化摘要} → 数据已融入窗口W{id}`
2. THE 操作确认文本 SHALL 控制在 30-50 tokens 范围内
3. WHEN 操作涉及数据读取时，THE 操作确认 SHALL 包含已更新视口的范围信息
4. WHEN 操作涉及数据写入时，THE 操作确认 SHALL 包含受影响单元格的范围和数量
5. THE 操作确认格式化器 SHALL 将操作确认对象序列化为符合规范的文本字符串
6. FOR ALL 有效的操作确认对象，序列化后再解析 SHALL 还原出等价的操作确认对象（往返一致性）

### 需求 6：window_return_mode 配置与向后兼容

**用户故事：** 作为系统管理员，我希望通过配置项控制工具返回值处理模式，以便在新旧架构之间平滑切换。

#### 验收标准

1. THE ExcelManusConfig SHALL 包含 window_return_mode 配置项，取值为 unified、anchored 或 enriched
2. WHILE window_return_mode 为 enriched 时，THE Engine SHALL 使用当前架构的工具返回值处理逻辑，保持完全向后兼容
3. WHILE window_return_mode 为 unified 时，THE Engine SHALL 使用 WURM 架构，工具返回值经过 ingest 后仅写入操作确认
4. WHILE window_return_mode 为 anchored 时，THE Engine SHALL 使用中间模式，操作确认中包含锚定数据摘要（约 60 tokens）
5. WHEN window_return_mode 配置值无效时，THE 配置加载器 SHALL 回退到 enriched 模式并记录警告日志

### 需求 7：循环检测安全阀

**用户故事：** 作为 Agent 引擎开发者，我希望系统能检测 Agent 对同一数据源的重复读取行为，以便防止 Agent 因不信任 Window 数据而陷入无效循环。

#### 验收标准

1. WHEN Agent 对同一 (file, sheet) 连续发起 2 次读取操作且中间无写入操作时，THE 循环检测器 SHALL 判定为不信任循环
2. WHEN 不信任循环被检测到时，THE Engine SHALL 在操作确认中附加提示信息，引导 Agent 使用 Window 中已有的数据
3. THE 循环检测器 SHALL 维护每个 (file, sheet) 的连续读取计数器
4. WHEN 对同一 (file, sheet) 发生写入操作时，THE 循环检测器 SHALL 重置该 (file, sheet) 的连续读取计数器

### 需求 8：focus_window 工具

**用户故事：** 作为 Agent，我希望能主动切换焦点窗口，以便在多窗口场景下精确控制需要查看完整数据的 Window。

#### 验收标准

1. WHEN Agent 调用 focus_window 并指定窗口标识时，THE WindowManager SHALL 将目标窗口的 detail_level 提升为 FULL
2. WHEN Agent 调用 focus_window 时，THE WindowManager SHALL 将之前的焦点窗口降级为 BACKGROUND 或 SUSPENDED
3. IF focus_window 指定的窗口标识不存在，THEN THE WindowManager SHALL 返回错误信息列出当前可用窗口
4. THE focus_window 工具 SHALL 注册在工具注册中心（tools/registry.py），遵循现有工具注册规范

### 需求 9：Intent Layer（意图层序列化）

**用户故事：** 作为 Agent，我希望 Window 不仅知道“有什么数据”，也知道“当前为什么看这份数据”，以便不同任务输出不同维度重点。

#### 验收标准

1. THE WindowState SHALL 包含 intent_tag 字段，支持 aggregate、format、validate、formula、entry、general 六类
2. WHEN detail_level 相同时，THE 序列化器 SHALL 根据 intent_tag 切换输出维度偏好，而不仅调整输出粒度
3. THE Intent 推断 SHALL 采用规则优先，并同时参考 user_intent_summary 与工具类型
4. WHEN 用户语义与工具行为冲突时，THE 用户语义 SHALL 优先
5. WHEN intent 发生更新时，THE WindowManager SHALL 应用粘性锁（默认 3 轮），避免频繁抖动
6. WHEN focus_window(clear_filter) 执行成功时，THE WindowManager SHALL 将 intent_tag 置为 validate 并重置锁
7. THE RepeatDetector SHALL 按 (file, sheet, range, intent) 维度计数，并在写入后清空同 sheet 全部 intent 计数

### 需求 10：Rule-Driven Window v2 与回退策略

**用户故事：** 作为系统维护者，我希望窗口规则具备集中管理、可解释和可回退能力，以便在提升行为一致性的同时降低发布风险。

#### 验收标准

1. THE ExcelManusConfig SHALL 提供 `window_rule_engine_version`（`v1|v2`），并支持环境变量 `EXCELMANUS_WINDOW_RULE_ENGINE_VERSION`
2. WHILE `window_rule_engine_version=v2` 时，THE WindowManager SHALL 通过统一规则注册表完成工具分类、intent 判定与 repeat 阈值决策
3. WHILE `window_rule_engine_version=v1` 时，THE WindowManager SHALL 继续走 v1 分支，保持旧行为可回退
4. THE ingest 语义 SHALL 采用“范围几何 + 行位置/主键”合并，不以整行内容签名作为主去重策略
5. WHEN 写入目标可映射到缓存时，THE ingest 写入流程 SHALL 原地 patch `data_buffer`；仅在无法映射时设置 `stale_hint`
6. THE confirmation 生成流程 SHALL 先构造结构化记录，再按 anchored/unified 协议序列化，并支持反序列化回读
7. THE `focus_window` 语义 SHALL 满足：旧焦点降级、无效窗口返回 `available_windows`、`clear_filter` 重置为 validate 并刷新锁
8. THE bench 三路对比 SHALL 支持标记 `invalid_for_perf`，并通过 `EXCELMANUS_BENCH_DISABLE_PLAN_INTERCEPT=1` 禁止 plan 短路干扰

## 实现状态（WURM v2，2026-02-15）

- ✅ 规则中心化：新增 `rule_registry.py`，并在 manager 中按 `window_rule_engine_version` 分支接入
- ✅ 配置与回退：新增 `window_rule_engine_version` + 环境变量，默认 `v1`
- ✅ ingest 语义修复：读取合并改为几何+行位置信息；写入优先 patch，失败才 stale
- ✅ confirmation 协议化：新增 `confirmation.py`，anchored/unified 均由 `ConfirmationRecord` 序列化
- ✅ focus 规则对齐：旧焦点降级、无效窗口可用列表、`clear_filter` validate 锁刷新
- ✅ RepeatDetector 扩展：read-like 覆盖 `read_excel/read_sheet/analyze_data/filter_data/transform_data/read_cell_styles`
- ✅ advisor 可解释性：引入 `reason_code`，并在 manager debug 日志输出生命周期原因
- ✅ bench 可比性：修复多 suite 传参，加入 plan-intercept 禁用开关与 `invalid_for_perf` 统计
- ✅ 测试覆盖：新增 registry/confirmation/ingest-merge/focus 语义测试并调整现有断言
