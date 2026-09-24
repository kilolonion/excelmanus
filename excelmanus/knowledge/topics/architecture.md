# 系统设计与能力边界

## 会话与能力层次 {#scope}

ExcelManus 是围绕工作区文件工作的 agent。主会话持有模型、消息、工具注册表、技能、任务和审批状态；子代理有自己的会话、模型上下文和收窄后的能力目录。界面显示的功能、产品支持的功能、当前会话授权的工具、已经披露参数的工具是不同层次。

## 请求与执行流程 {#request-flow}

一次请求的大体流程是：接收用户目标和附件 → 组装核心原则、符合条件的策略及当前能力地图 → 模型调用工具 → 宿主检查参数、权限和依赖 → 工作区或服务执行 → 将结果、版本、覆盖范围和必要附件反馈给模型 → 持续执行或交付。跨步骤状态以实际工具结果为准。

## 有效目录与按需披露 {#catalog}

当前有效执行目录共同决定工具执行、Python SDK、能力地图与工具详情。模型首轮只收到核心工具 schema；其余工具通过查询详情按需披露。工具未展示参数不表示不可用。产品文档介绍某种功能不代表当前会话有权执行；查看[工具目录](knowledge:tools)和[运行状态](knowledge:runtime)。

## 说明与运行时事实源 {#sources}

提示词正文按身份、核心原则、领域原则、策略组织；schema 负责参数语义；宿主负责路径、审批、版本和提交。统一门户的文章随产品版本分发，工具、技能、设置和错误解释从运行中的事实源读取。文章不复制整套工具字段表；具体字段和输出通过工具引用继续查询。

## 统一入口 {#navigation}

门户由 introspect_capability 提供：knowledge_index 查看入口，knowledge_search 检索主题，knowledge_read 读取返回的 ref。结果带来源、版本、相关链接和可直接使用的 next_call。分页沿 next_call 继续；内容改变时重新读取第一页，避免拼接不同版本。找不到或当前不可用时会给出返回目录的入口，不应凭记忆编造。

## 相关主题 {#next}

继续阅读：[工作流程](knowledge:doc:workflows)、[配置](knowledge:doc:configuration)、[执行边界](knowledge:doc:execution)、[上下文](knowledge:doc:context)、[协作](knowledge:doc:collaboration)、[恢复](knowledge:doc:recovery)。
