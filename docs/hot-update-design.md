# ExcelManus 升级与部署

适用版本：1.8.0 源码 · 更新日期：2026-09-19

[文档导航](README.md) · [运维手册](ops-manual.md) · [English operations guide](ops-manual_en.md)

ExcelManus 当前有桌面应用、本机 Git 源码和服务器三种运行形态。升级会影响正在执行的任务，应在合适的时间进行；当前流程不承诺无中断切换。

## 桌面应用

桌面版从应用资源启动随包前后端及代码运行时，更新方式是安装新的应用包。应用数据保留在 Electron 用户数据目录中的 `profile/`，或显式指定的 `EXCELMANUS_HOME`。

桌面模式禁用源码 Git 更新、应用级备份恢复和远程部署控制。工作簿修订恢复仍属于工作区文件功能。安装新包前，备份 profile 及其之外的已登记工作区。构建、签名和验证见 [Desktop README](../desktop/README.md)。

## 本机 Git 源码

设置页的更新入口调用 `POST /api/v1/version/upgrade`。API 记录请求并启动独立的 `excelmanus.upgrade` 辅助进程，由它完成停机和更新：

1. 根据 `$EXCELMANUS_HOME/runtime.json` 停止启动脚本及相关进程。
2. 将实现覆盖的数据库及 `data/`、`memory/`、`skillpacks/` 等数据备份到 data home 的 `backups/`。这不是整个 profile 的完整复制。
3. 获取远端代码并尝试 fast-forward。分支分叉或冲突会使更新失败，不会静默强制重置本机分支。
4. 安装依赖，按需更新并构建前端。
5. 重新执行启动脚本，恢复服务。

当前内置升级备份未包含 `.secret_key`。迁移或灾难恢复需要该密钥，请在升级前另行备份完整 profile，并保存外部工作区和自定义外部数据库。仅依赖自动备份目录不能保证凭证可恢复。

也可以停止服务后运行 `./deploy/update.sh`；脚本会拒绝在服务仍运行时直接更新。Windows 使用对应的 `update.ps1` / `update.bat`。

配置保存触发的重启由 `restart.py` 处理，与版本更新是两条流程。版本检查也会比较 Git 提交，不要求包版本号一定增大。更新后应同时核对健康状态、代码版本和实际任务；服务重新响应不一定表示更新成功，例如 fast-forward 失败后可能重新启动原版本。

升级、应用备份恢复和远程部署等破坏性接口受本机来源及部署模式限制。经代理访问仍需管理令牌及入口访问控制，不能将 loopback 来源判断视为完整鉴权。

## 服务器

后端进程设置 `EXCELMANUS_DEPLOY_MODE=server` 后，生产 API 拒绝自身升级和远程部署执行。由运维机在正确的部署清单下运行：

```bash
bash ./deploy/deploy.sh check
bash ./deploy/deploy.sh --venv .venv
bash ./deploy/deploy.sh history
bash ./deploy/deploy.sh rollback-to --commit COMMIT_SHA
```

脚本支持单机、分机和本地拓扑，更新内容包括代码、依赖、前端产物和服务重启。清单、代理和持久化目录的准备见 [运维手册](ops-manual.md)。

服务器 Git 同步和部分回滚路径会重置部署副本，不能在该目录保留未提交的开发工作。`rollback` 与 `rollback-to` 的行为以各自脚本为准；代码回滚不等于数据库及全部文件回滚。部署历史位于运维机的 `deploy/.deploy_history` 和 `deploy/.deploy_history.json`。

本机 standalone 实例可在配置部署清单后作为运维控制台；这不会让 server 实例获得自身升级权限。

## 数据迁移与恢复

完整恢复需要相匹配的数据库、加密密钥和工作区文件。旧容器卷与旧 `users/{id}/` 目录应由维护者选择后手工迁移，不自动合并多个历史用户库。

首次打开工作区时，系统会尝试把旧 `outputs/backups` 导入 `.excelmanus/revisions/`，迁移标记为 `.excelmanus/migrations/overlay-backups.json`。确需重新执行时使用：

```bash
uv run python -m excelmanus.workspace.migrate /path/to/workspace --force
```

`--force` 表示重新执行迁移；运行前先备份该工作区。当前不提供 Docker 安装流程、应用内蓝绿切换或多 worker 无中断滚动升级。
