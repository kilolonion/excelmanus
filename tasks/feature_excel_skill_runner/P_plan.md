# P 阶段计划：Excel 大文件可执行 Skill

## WBS
1. 初始化 skill 目录骨架
2. 编写大文件探查脚本（profile）
3. 编写脚本执行器（run）
4. 编写参考文档与 SKILL.md 工作流
5. 执行验证与任务验收

## DoD（完成定义）
- [x] skill 目录结构完整，可被 Codex 识别
- [x] `SKILL.md` 说明触发条件、执行步骤、注意事项
- [x] 至少 2 个可执行脚本，覆盖“探查 + 执行”闭环
- [x] 脚本本地运行通过
- [x] `quick_validate.py` 校验通过

## 依赖与风险
- 依赖：`pandas`、`openpyxl` 在当前环境可用
- 风险：用户脚本执行安全边界依赖工作区路径限制
