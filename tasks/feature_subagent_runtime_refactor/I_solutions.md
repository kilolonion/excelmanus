# I 方案

## 方案结论
采用“新增 `excelmanus/subagent/` 独立包 + 引擎主流程切换”的全量重构方案：

1. 子代理配置与执行解耦。
2. 元工具主入口切到 `delegate_to_subagent`。
3. 权限模式严格映射审批系统。
4. 统一对外术语为 `subagent`。
