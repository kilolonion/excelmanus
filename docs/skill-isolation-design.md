# 技能装载与工作区

适用版本：1.8.0 源码 · 更新日期：2026-09-19

[文档导航](README.md) · [Skillpack 协议](skillpack_protocol.md)

ExcelManus 采用单用户、多会话和可登记多个本机文件夹的工作区模型。技能不按登录用户分目录；用户技能可供同一实例使用，项目技能的扫描范围由加载器收到的工作区配置确定。

`SkillpackLoader` 按 system、user、project 来源扫描并处理同名覆盖；`SkillRouter` 处理显式调用，`SkillpackManager` 提供管理入口。加载不改变文件守卫、会话权限和审批规则。

| 来源 | 默认位置 | 说明 |
| --- | --- | --- |
| system | `excelmanus/skillpacks/system` | 随包提供的默认技能 |
| user | `~/.excelmanus/skillpacks` | 用户技能；外部工具目录可按开关参与发现 |
| project | `<workspace_root>/.excelmanus/skillpacks` | 项目技能；还可扫描受配置控制的 `.agents/skills` 等目录 |

完整顺序及覆盖规则以 [Skillpack 协议](skillpack_protocol.md) 为准。普通 `skills/` 不是默认发现入口；需要时通过 `EXCELMANUS_SKILLS_DISCOVERY_EXTRA_DIRS` 显式配置。

这些目录是技能来源约定，不是多租户隔离边界。自定义 `EXCELMANUS_HOME` 时，不应假定所有用户技能目录随之迁移；核对 `EXCELMANUS_SKILLS_USER_DIR`、项目目录和设置页中实际加载的列表。

旧 `users/{id}/skillpacks/` 不会自动迁移。需要保留时，选择要使用的技能拷贝到用户或项目技能目录，检查同名覆盖后再加载。
