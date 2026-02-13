# P 阶段执行计划：Accept 门禁与审计

## WBS
1. 审批核心模块
- 新增 `excelmanus/approval.py`
- 定义 `PendingApproval`、`AppliedApprovalRecord`、`FileChangeRecord`、`BinarySnapshotRecord`
- 实现高风险工具识别、审计落盘、undo 回滚

2. 引擎接入
- `engine.chat` 增加 pending 阻塞
- 增加 `/accept`、`/reject`、`/undo` 控制命令
- 高风险工具在非 `fullAccess` 下转 pending，在 `fullAccess` 下直接执行并审计

3. CLI 与文档
- `cli.py` 增加新命令展示和路由
- `README.md` 增加门禁与审计说明

4. 测试与验收
- 新增 `tests/test_approval.py`
- 更新 `tests/test_engine.py`、`tests/test_cli.py`、`tests/test_api.py`
- 运行聚焦测试并修复回归

## DoD
- 非 `fullAccess` 下高风险操作必须先 `accept`
- `outputs/approvals/<id>/` 产物完整
- `/undo <id>` 对可回滚记录有效，冲突检测生效
- 相关测试通过

## 风险
- 脚本执行副作用不可完全穷举，仅保证审计可追溯
- 二进制快照可能较大，需控制在目标文件级别
