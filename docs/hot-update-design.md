# ExcelManus 升级与部署

安装形态只有两种：**本机 Git（standalone）** 和 **远程服务器（server，PM2 / systemd）**。没有 Docker 安装轨，也没有应用内蓝绿 / 灰度。

## 本机（standalone）

设置页「执行更新」调用 `POST /api/v1/version/upgrade`。API 只负责写 upgrade-request、拉起独立 helper，然后退出。

Helper（`python -m excelmanus.upgrade`）顺序：

1. 按 `$EXCELMANUS_HOME/runtime.json` 杀掉 `start.sh` / `start.ps1` 进程组
2. 备份 `$EXCELMANUS_HOME`（数据库、`config.env`、`data/`、`memory/` 等）到 `$EXCELMANUS_HOME/backups/`
3. `git fetch` + **fast-forward only**（落后当前分支的提交即可更新；冲突则失败并保留备份，**禁止**静默 `reset --hard`）
4. 安装依赖，必要时 `npm ci` / `npm run build`
5. `exec` 回 start 脚本

CLI：`./deploy/update.sh`（服务在跑则拒绝）。配置保存仍走 `restart.py`（读 runtime.json 端口），**版本升级不走这条路径**。

成功条件：health 先失败再起来（进程被换掉）。指纹变化表示拉到了新代码；ff-only 失败时 helper 仍会拉起旧进程，overlay 在服务恢复后刷新，不再死等指纹。

破坏性接口（升级 / 恢复 / 远程部署）仅接受 **loopback**。

落后远程若干 commit 即视为有更新，**不要求** `pyproject.toml` 版本号变大。

## 服务器（server）

`EXCELMANUS_DEPLOY_MODE=server` 时，生产 API **禁止** `/version/upgrade`、`/deploy/execute`、`/deploy/rollback*`。

运维机运行：

```bash
./deploy/deploy.sh                  # 同步 + 依赖 + 重启 + /health
./deploy/deploy.sh rollback-to --commit <hash>
```

拓扑：`single | split | local`。回滚 = 远端 checkout 该 commit → 装依赖 → 重启 → 健康检查。历史写 `.deploy_history.json`。

本机若配置了 `deploy/.env.deploy`，设置页可当运维控制台调本地 `deploy.sh`（此时本机仍是 standalone）。

## 明确不做

- 真蓝绿双目录、Nginx 切流、灰度权重
- 容器内 git pull、发布镜像
- 多 worker 滚动、无中断 SSE
- 活进程里 `git merge` / `pip install -e`
