# R1 调研

- 已核对现有实现：`explore_data` 为硬编码只读子循环，缺少通用子代理注册/执行框架。
- 已核对可复用基础：`EventType.SUBAGENT_*`、审批审计、路由子模型、CLI `/subagent` 控制开关。
- 主要改造面：engine 元工具、权限桥接、skillpack frontmatter、CLI/API/渲染文案、测试体系。
