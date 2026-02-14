# P 阶段计划：工程清理与废弃代码移除

## WBS
1. 建立任务文档与基线清单
2. 清理误提交生成物与缓存
3. 清理无引用废弃脚本
4. 更新文档与目录说明
5. 运行测试并形成验收结论

## 验收标准（DoD）
- Git 不再跟踪 `.hypothesis/`、`.pytest_cache/`、`__pycache__/`、`*.pyc`、`frontend/node_modules/`、`frontend/dist/`、`outputs/`、`excelmanus.egg-info/`。
- 删除的脚本在仓库内无引用且非运行入口。
- `pytest` 通过（或明确列出失败原因）。
- 前端 `npm test` 通过（或明确列出失败原因）。
- 任务日志完整记录所有关键变更。

## 风险与依赖
- 依赖：本地 Python/Node 测试环境可用。
- 风险：若存在未显式声明的手工流程依赖，需在日志中标记并回退对应删除。
