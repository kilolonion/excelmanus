---
name: agent_self_management
description: 查询当前 agent 的实际能力、工具、技能和配置，并按用户任务调整当前会话的推理、上下文、并发及工具开关。用户需先在系统设置启用 Agent 自我管理。
version: "1.0.0"
---

# Agent 自我管理

本技能和 `inspect_agent` / `configure_agent` 工具默认关闭，由用户在设置 → 系统 → 能力 → Agent 自我管理中启用。加载本技能后才可使用这两个工具。

1. 先调用 `inspect_agent(section="all")`，以返回的当前状态为准，不根据过往对话猜测能力。
2. 向用户说明与任务相关的配置，按需求作最小修改。只修改返回值中 `writable=true` 的字段，遵守其 schema 和 `thinking_effort_options`。
3. 用 `configure_agent(changes={...}, reason="具体目的")` 修改配置；使用 `disable_tools` 暂停不需要的工具，使用 `enable_tools` 恢复之前暂停的工具。恢复工具不改变宿主授权、工作区限制、只读或计划模式。
4. 检查结果中的前后值及生效时机；必要时再次查询。报告实际成功的变更，不能把错误结果当作成功。

所有变更只保留在当前内存会话，重建会话后恢复用户默认设置。`max_iterations` 从下一轮用户请求生效；其他可写配置供后续调用使用，不中断已运行的操作。当前轮的时间、token 和费用预算不由此工具修改。

可加载的技能通过现有 `skill` 工具启用；更多工具参数通过 `introspect_capability` 查询。不要用文件、代码、shell 或全局设置绕过工具约束，不得自行开启总开关、提升审批/文件访问权限、读取或更改密钥。只读及计划模式仅允许查询。

示例：`configure_agent(changes={"thinking_effort":"high","parallel_tool_max":2}, reason="复杂公式核查需要更深入推理，并减少并发请求")`。
