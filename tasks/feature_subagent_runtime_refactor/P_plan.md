# P 计划

1. 新增 `subagent` 包：models/tool_filter/builtin/registry/executor。
2. 扩展配置与事件字段。
3. 重构 `engine`：移除 `explore_data`，接入 `delegate_to_subagent` + `list_subagents`。
4. 扩展 `/subagent` 命令：`list/run`。
5. 移除 skillpack `context` 能力并更新系统 skill。
6. 同步 API/Renderer 文案与事件 payload。
7. 新增/更新测试并执行回归。
