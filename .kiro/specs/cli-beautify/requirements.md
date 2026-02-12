# 需求文档：CLI 美化

## 简介

ExcelManus 当前的 CLI 在执行 LLM 工具调用循环时，仅显示一个 "思考中..." 的 spinner，用户无法看到工具调用过程和 LLM 思考内容。本需求旨在美化 CLI 输出体验，将工具调用过程可视化为卡片样式，并支持 LLM 思考过程的折叠/展开显示，使整体 CLI 体验更加美观和信息丰富。

## 术语表

- **CLI**：命令行界面（Command Line Interface），用户与 ExcelManus 交互的终端界面
- **AgentEngine**：核心代理引擎，驱动 LLM 与工具之间的 Tool Calling 循环
- **ToolCallCard**：工具调用卡片，以可视化面板形式展示单次工具调用的名称、参数、状态和结果摘要
- **ThinkingBlock**：思考过程块，以可折叠/展开形式展示 LLM 的推理过程
- **StreamRenderer**：流式渲染器，负责将 AgentEngine 的中间状态实时渲染到终端
- **ToolCallEvent**：工具调用事件，AgentEngine 在工具调用生命周期中产生的结构化事件数据
- **Rich**：Python 终端富文本渲染库，项目已有依赖

## 需求

### 需求 1：AgentEngine 事件回调机制

**用户故事：** 作为开发者，我希望 AgentEngine 在工具调用循环中产生结构化事件，以便 CLI 层能够实时获取并渲染中间状态。

#### 验收标准

1. WHEN AgentEngine 开始一次工具调用时，THE AgentEngine SHALL 通过回调函数发出包含工具名称和参数的 tool_call_start 事件
2. WHEN 工具调用执行完成时，THE AgentEngine SHALL 通过回调函数发出包含执行结果和成功/失败状态的 tool_call_end 事件
3. WHEN LLM 返回包含思考内容（reasoning/thinking）的响应时，THE AgentEngine SHALL 通过回调函数发出包含思考文本的 thinking 事件
4. WHEN 未注册任何回调函数时，THE AgentEngine SHALL 保持当前行为不变，无副作用
5. WHEN LLM 开始新一轮迭代时，THE AgentEngine SHALL 通过回调函数发出包含当前轮次编号的 iteration_start 事件

### 需求 2：工具调用卡片渲染

**用户故事：** 作为用户，我希望在 CLI 中看到每次工具调用的可视化卡片，以便了解代理正在执行的操作。

#### 验收标准

1. WHEN 收到 tool_call_start 事件时，THE StreamRenderer SHALL 渲染一个包含工具名称和参数摘要的卡片面板
2. WHEN 工具参数包含文件路径时，THE StreamRenderer SHALL 在卡片中高亮显示文件路径
3. WHEN 收到 tool_call_end 事件且工具执行成功时，THE StreamRenderer SHALL 更新卡片状态为成功（绿色标记）并显示结果摘要
4. WHEN 收到 tool_call_end 事件且工具执行失败时，THE StreamRenderer SHALL 更新卡片状态为失败（红色标记）并显示错误信息
5. WHEN 工具结果文本超过 200 个字符时，THE StreamRenderer SHALL 截断结果并附加省略标记
6. WHEN 同一轮迭代中有多个工具调用时，THE StreamRenderer SHALL 按顺序依次渲染每个工具调用卡片

### 需求 3：LLM 思考过程显示

**用户故事：** 作为用户，我希望能够查看 LLM 的思考过程，以便理解代理的推理逻辑。

#### 验收标准

1. WHEN 收到 thinking 事件时，THE StreamRenderer SHALL 以折叠形式渲染思考内容块，默认显示首行摘要
2. WHEN 思考内容为空字符串时，THE StreamRenderer SHALL 跳过渲染，不显示空的思考块
3. WHEN 思考内容超过 500 个字符时，THE StreamRenderer SHALL 在折叠摘要中显示前 80 个字符并附加省略标记

### 需求 4：CLI 集成与交互体验

**用户故事：** 作为用户，我希望美化后的 CLI 保持流畅的交互体验，不影响现有功能。

#### 验收标准

1. WHEN 用户发送自然语言指令时，THE CLI SHALL 替换当前的 spinner 为实时的事件流渲染
2. WHEN AgentEngine 执行完成并返回最终回复时，THE CLI SHALL 在所有工具调用卡片之后渲染最终回复
3. WHEN 工具调用过程中发生异常时，THE CLI SHALL 显示错误信息并保持可继续交互的状态
4. THE CLI SHALL 保持现有的 /help、/history、/clear、exit/quit 命令功能不变
5. WHEN 终端宽度小于 60 列时，THE StreamRenderer SHALL 自动调整卡片布局以适应窄终端

### 需求 5：事件数据模型

**用户故事：** 作为开发者，我希望事件数据有清晰的结构定义，以便在 Engine 和 Renderer 之间传递类型安全的数据。

#### 验收标准

1. THE ToolCallEvent SHALL 包含事件类型、工具名称、参数字典、结果文本、成功状态和时间戳字段
2. THE ToolCallEvent SHALL 使用 Python dataclass 定义，所有字段具有明确的类型注解
3. WHEN 序列化 ToolCallEvent 为字典时，THE ToolCallEvent SHALL 产生与原始数据等价的字典表示（支持 round-trip）
