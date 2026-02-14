# P 阶段执行计划

## WBS
1. 建立 `tools` 层并迁移现有工具实现。
2. 建立 `skillpacks` 层（模型、加载、路由、内置 SKILL.md）。
3. 接入引擎：路由前置、system 注入策略、tool_scope 下发、未授权硬校验。
4. 接入 API/CLI：新增 `skill_hints`、`/skills`、health 字段升级。
5. 升级版本与文档，回归测试。

> 历史版本注记（2026-02-14）：
> - 第 4 项中的 `skill_hints` 已在后续版本废弃，仅保留历史计划语义。

## DoD
- 单测全通过。
- API/CLI 返回和命令行为符合 v3 约定。
- 主链路不依赖旧 `skills` 自动发现。
