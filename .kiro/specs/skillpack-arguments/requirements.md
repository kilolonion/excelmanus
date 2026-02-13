# 需求文档：Skillpack 参数化模板

## 简介

为 ExcelManus 的 Skillpack 系统添加 `$ARGUMENTS` 参数化模板支持。用户可以通过斜杠命令（如 `/analyze 销售数据.xlsx`）直接调用 Skillpack 并传递参数，参数会替换 SKILL.md 正文中的占位符，从而消除多轮对话、大幅提升交互效率。

## 术语表

- **Skillpack**：由 SKILL.md 文件定义的技能包，包含 frontmatter 元数据和正文指令模板
- **SkillpackLoader**：负责扫描、解析和加载 SKILL.md 文件的组件
- **SkillRouter**：根据用户消息匹配合适 Skillpack 的路由组件
- **AgentEngine**：驱动 LLM 与工具之间 Tool Calling 循环的核心引擎
- **CLI**：基于 Rich 的命令行交互界面
- **Frontmatter**：SKILL.md 文件顶部 `---` 包裹的 YAML 元数据区域
- **占位符**：SKILL.md 正文中的参数模板变量，如 `$ARGUMENTS`、`$0`、`$1` 等
- **Argument_Hint**：frontmatter 中的可选字段，用于描述参数格式提示

## 需求

### 需求 1：SKILL.md Frontmatter 扩展

**用户故事：** 作为 Skillpack 作者，我希望在 SKILL.md 的 frontmatter 中声明参数提示信息，以便用户了解该技能包接受的参数格式。

#### 验收标准

1. WHEN SkillpackLoader 解析包含 `argument_hint` 字段的 SKILL.md 文件时，THE SkillpackLoader SHALL 将该字段值存储到 Skillpack 数据模型的 `argument_hint` 属性中
2. WHEN SKILL.md 的 frontmatter 不包含 `argument_hint` 字段时，THE SkillpackLoader SHALL 将 `argument_hint` 属性设为空字符串
3. WHEN `argument_hint` 字段的值不是字符串类型时，THE SkillpackLoader SHALL 抛出 SkillpackValidationError 异常

### 需求 2：参数占位符替换

**用户故事：** 作为用户，我希望在调用 Skillpack 时传递参数，参数能自动替换 SKILL.md 正文中的占位符，以便一条命令完成任务。

#### 验收标准

1. WHEN 用户提供参数字符串时，THE 参数替换引擎 SHALL 将正文中的 `$ARGUMENTS` 占位符替换为完整参数字符串
2. WHEN 用户提供参数字符串时，THE 参数替换引擎 SHALL 将正文中的 `$0`（或 `$ARGUMENTS[0]`）替换为第一个位置参数，`$1`（或 `$ARGUMENTS[1]`）替换为第二个位置参数，依此类推
3. WHEN 正文中引用的位置参数索引超出实际提供的参数数量时，THE 参数替换引擎 SHALL 将该占位符替换为空字符串
4. WHEN 用户未提供任何参数时，THE 参数替换引擎 SHALL 将所有占位符替换为空字符串
5. THE 参数替换引擎 SHALL 对正文进行占位符替换后输出结果文本（序列化/反序列化的往返一致性：对于不含占位符的正文，替换操作 SHALL 返回与原文相同的文本）

### 需求 3：CLI 斜杠命令注册

**用户故事：** 作为用户，我希望已加载的 Skillpack 自动注册为斜杠命令，以便我通过 `/技能名 参数` 的方式快速调用。

#### 验收标准

1. WHEN Skillpack 加载完成后，THE CLI SHALL 将每个 Skillpack 的名称注册为可用的斜杠命令
2. WHEN 用户输入 `/技能名 参数1 参数2 ...` 时，THE CLI SHALL 识别该命令并将参数传递给对应的 Skillpack
3. WHEN 用户输入的斜杠命令与已注册的 Skillpack 名称不匹配且不属于内置命令时，THE CLI SHALL 显示"未知命令"提示并建议可用命令
4. WHEN 用户输入 `/help` 时，THE CLI SHALL 在帮助信息中列出所有已注册的 Skillpack 斜杠命令及其 `argument_hint`
5. WHEN 用户输入斜杠命令前缀时，THE CLI SHALL 提供包含 Skillpack 命令的自动补全建议

### 需求 4：SkillRouter 参数传递

**用户故事：** 作为系统组件，SkillRouter 需要支持将用户参数传递到 Skillpack 的指令模板中，以便 AgentEngine 获得参数化后的上下文。

#### 验收标准

1. WHEN SkillRouter 通过斜杠命令匹配到 Skillpack 时，THE SkillRouter SHALL 将用户参数传递给参数替换引擎，并将替换后的指令文本包含在路由结果中
2. WHEN SkillMatchResult 包含参数化后的指令时，THE AgentEngine SHALL 使用替换后的指令文本作为系统上下文
3. WHEN Skillpack 通过自然语言路由（非斜杠命令）匹配时，THE SkillRouter SHALL 保持现有行为不变，不执行参数替换

### 需求 5：参数解析

**用户故事：** 作为用户，我希望参数解析支持空格分隔和引号包裹，以便传递包含空格的参数值。

#### 验收标准

1. THE 参数解析器 SHALL 按空格分隔用户输入的参数字符串为位置参数列表
2. WHEN 参数值包含空格时，THE 参数解析器 SHALL 支持使用双引号（`"`）或单引号（`'`）包裹该参数值，将其视为单个参数
3. IF 引号未正确闭合，THEN THE 参数解析器 SHALL 将剩余文本视为单个参数（容错处理）

### 需求 6：错误处理与边界情况

**用户故事：** 作为用户，我希望在参数使用不当时获得清晰的提示信息，以便快速纠正。

#### 验收标准

1. WHEN 用户通过斜杠命令调用 Skillpack 但未提供参数，且该 Skillpack 的 `argument_hint` 非空时，THE CLI SHALL 显示参数提示信息并继续执行（将占位符替换为空字符串）
2. WHEN 参数替换完成后正文为空或仅含空白字符时，THE 参数替换引擎 SHALL 返回空字符串
