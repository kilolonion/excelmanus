# ExcelManus 运维手册

适用版本：1.8.1 源码 · 更新日期：2026-09-21

[文档导航](README.md) · [English](ops-manual_en.md) · [配置参考](configuration.md)

本手册介绍源码启动、服务器部署、数据备份和故障排查。路径、域名与服务账号均为示例，请按实际环境替换。桌面安装包的构建和数据目录见 [Desktop README](../desktop/README.md)。

## 1. 选择运行方式

| 方式 | 适用场景 | 启动与更新 |
| --- | --- | --- |
| 桌面应用 | 本机使用 | 由应用启动随包服务，安装新包更新 |
| 本机 Git 源码 | 开发或本机长期使用 | `deploy/start.*` 启动，停止服务后更新 |
| 服务器 | 经受控入口远程访问 | PM2 / systemd 管理进程，由运维机执行部署 |

ExcelManus 是单用户软件。工作区和多会话共享模型凭证、记忆与外部服务配置；管理令牌不提供多用户隔离。源码运行需要 Python ≥ 3.10、Node.js ≥ 20.9、Git；推荐使用 uv。服务器还需要相应的进程管理器与反向代理。

单机后端保持 **1 个 worker**。正在运行的会话、审批和任务状态包含进程内数据，不能仅靠增加 worker 扩容。

## 2. 本机源码启动

在仓库根目录运行：

```bash
./deploy/start.sh
./deploy/start.sh --prod
./deploy/start.sh --backend-port 9000 --frontend-port 8080
./deploy/start.sh --backend-only
./deploy/start.sh --no-open --log-dir ./logs
./deploy/start.sh --help
```

Windows 使用 PowerShell：

```powershell
.\deploy\start.ps1
.\deploy\start.ps1 -Production
.\deploy\start.ps1 -BackendPort 9000 -FrontendPort 8080
```

CMD 可使用 `deploy\start.bat` 或 `deploy\start.bat --prod`。浏览器默认访问 [http://localhost:3000](http://localhost:3000)，后端默认监听 `127.0.0.1:8000`。启动后在设置页添加并激活模型档案。

手动启动时，在两个终端分别运行：

```bash
# 终端一：仓库根目录
uv sync --frozen --extra web --extra analysis
uv run excelmanus-api --host 127.0.0.1 --port 8000
```

```bash
# 终端二：仓库根目录
cd web
npm ci
npm run dev
```

自定义端口时，同时配置前端连接地址，见 [Web README](../web/README.md)。

## 3. 配置与数据目录

| 内容 | 位置与管理方式 |
| --- | --- |
| 模型档案和产品设置 | 主库 `model_profiles` / `config_kv`；通过设置页或配置导入管理 |
| 主数据库 | 默认 `$EXCELMANUS_HOME/excelmanus.db` |
| 凭证加密密钥 | 默认 `$EXCELMANUS_HOME/.secret_key`，须与数据库一起保留 |
| 默认工作区 | `EXCELMANUS_DATA_ROOT`，未指定时通常为 `$EXCELMANUS_HOME/data` |
| 其他工作区 | 用户登记的本机目录，保留在原路径 |
| 文件修订 | 每个工作区的 `.excelmanus/revisions/` |
| 部署清单 | 运维机的 `deploy/.env.deploy`，由模板复制并填写 |
| 前端连接参数 | Next.js 进程环境或 `web/.env.local` |

源码版 `EXCELMANUS_HOME` 默认是 `~/.excelmanus`；桌面版使用 Electron 用户数据目录下的 `profile/`。二者不会自动合并。不要让桌面版与源码服务同时使用同一数据目录。

项目 `.env`、用户 `config.env` 不再提供产品设置。`EXCELMANUS_HOME`、`EXCELMANUS_DB_PATH`、监听参数、`EXCELMANUS_DEPLOY_MODE`、`EXCELMANUS_MANAGE_TOKEN` 等由启动进程提供。完整分类见 [配置参考](configuration.md)。

## 4. 服务器访问保护

推荐的单机拓扑：

```text
浏览器 ── HTTPS ── Nginx
                  ├─ /api/ ── 127.0.0.1:8000（FastAPI）
                  └─ /     ── 127.0.0.1:3000（Next.js）
```

1. 为运行进程设置 `EXCELMANUS_DEPLOY_MODE=server`；服务器默认禁用网页自身更新和远程部署执行，备份恢复接口仍受本机来源限制。如需允许管理员从网页一键更新，另见第 7 节。
2. 配置至少 16 字符的 `EXCELMANUS_MANAGE_TOKEN`。即使后端监听 loopback，只要反向代理对外暴露，也应配置访问保护。
3. 浏览器在令牌提示页输入相同令牌；API 客户端使用 `Authorization: Bearer <token>`。不要在公开前端变量中保存令牌，也不要由未经认证的代理为所有访客自动注入令牌。
4. 同机部署只需让代理访问应用端口；分机部署通过私网、VPN 或明确受控的后端入口连接，不必把 3000/8000 端口向所有公网来源开放。

令牌设置后，健康检查和 CORS 预检仍可无令牌访问。公开前端页面、静态资源和 API 文档不由管理令牌统一保护；需要限制整个站点时，在网络入口配置访问控制。

下面是 systemd 的配置示例。先创建专用服务账号，准备 `/srv/excelmanus` 源码及其 `.venv`，并赋予该账号读写 `/var/lib/excelmanus` 的权限：

```ini
# /etc/systemd/system/excelmanus-api.service
[Unit]
Description=ExcelManus API
After=network-online.target

[Service]
Type=simple
User=excelmanus
WorkingDirectory=/srv/excelmanus
EnvironmentFile=/etc/excelmanus/runtime.env
ExecStart=/srv/excelmanus/.venv/bin/excelmanus-api --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/excelmanus/runtime.env` 仅供服务管理器读取，内容示例：

```dotenv
EXCELMANUS_HOME=/var/lib/excelmanus
EXCELMANUS_DEPLOY_MODE=server
EXCELMANUS_MANAGE_TOKEN=replace-with-your-own-random-token
```

将令牌占位符替换为自己的随机值，并限制该文件的访问权限。模型密钥在 Web 设置页保存，不放入此文件。使用 PM2 时，把相同启动参数配置到后端进程环境，并确认重启后仍然存在。

## 5. 反向代理与前端地址

生产环境可为 Next.js 设置：

```dotenv
EXCELMANUS_RUNTIME_BACKEND_ORIGIN=same-origin
BACKEND_INTERNAL_URL=http://127.0.0.1:8000
```

前者在 Next.js 运行时读取，让浏览器经同源代理访问 API；后者用于 Next.js rewrite，在构建时也应保持正确。跨域直连时还需在产品设置中配置 `EXCELMANUS_CORS_ALLOW_ORIGINS`。

以下片段放入已配置域名与 TLS 证书的 Nginx `server` 块：

```nginx
client_max_body_size 100m;

location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Authorization $http_authorization;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 600s;
}

location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

对整个 `/api/` 关闭缓冲可覆盖首次对话和重连事件流。上传限制应与应用配置相匹配；这里的 `100m` 只是代理示例。修改后先运行 `nginx -t`，再重载 Nginx。证书续期方式和有效期以部署环境为准。

## 6. 使用部署脚本

部署脚本在**运维机**运行。先复制清单并填写服务器、SSH 密钥、远程路径和分支：

```bash
cp deploy/.env.deploy.example deploy/.env.deploy
bash ./deploy/deploy.sh --help
bash ./deploy/deploy.sh check --single-server --host server.example.com --venv .venv
bash ./deploy/deploy.sh --single-server --host server.example.com --venv .venv --dry-run
bash ./deploy/deploy.sh --single-server --host server.example.com --venv .venv
```

`uv sync` 使用 `.venv`，因此示例明确传入 `--venv .venv`。脚本支持 `--single-server`、`--split-server`、`--local`，以及 PM2 / systemd。Windows 运维入口为 `deploy/deploy.ps1`，具体参数以脚本帮助为准。

首次上线前检查服务环境中的管理令牌、持久化目录和代理配置。脚本不会替你生成管理令牌或建立多用户账号系统。默认后端绑定 loopback；分机拓扑还需自行准备受控的跨机连接，不能直接把另一台服务器的 loopback 当作可达地址。

常用操作：

```bash
bash ./deploy/deploy.sh --backend-only --venv .venv
bash ./deploy/deploy.sh --frontend-only
bash ./deploy/deploy.sh status
bash ./deploy/deploy.sh history
bash ./deploy/deploy.sh logs
bash ./deploy/deploy.sh rollback
bash ./deploy/deploy.sh rollback-to --commit COMMIT_SHA
```

以上命令沿用部署清单；请先确认清单中的拓扑与目标。脚本的 Git 同步及部分回滚路径会执行 `git reset --hard`，部署目录应作为发布副本使用，不应保存未提交的开发改动。`--from-local` 会同步本地文件，运行前检查同步范围。

低内存服务器可接收预先构建的前端产物。从仓库根目录执行：

```bash
npm --prefix web ci
npm --prefix web run build
mkdir -p web-dist
tar -czf web-dist/frontend-standalone.tar.gz -C web .next/standalone .next/static public
bash ./deploy/deploy.sh --frontend-only --frontend-artifact ./web-dist/frontend-standalone.tar.gz
```

产物应与目标环境的操作系统、架构和 Node.js 运行时兼容，不能默认把 macOS 构建直接用于 Linux。部署锁、构建检查和健康检查能发现部分故障，但不等于业务验收或无中断升级。

## 7. 升级、备份与恢复

本机源码版可从「设置 → 版本 → 一键更新前后端」停机升级，或停止服务后运行 `./deploy/update.sh`。更新会备份应用数据并尝试 fast-forward；有未提交修改、分支分叉或文件冲突时停止，不自动 stash 或强制覆盖。网页更新要求由 `deploy/start.sh` / `start.ps1` 启动的单 worker 前后端同机实例，并拒绝在任务运行中开始；更新期间有停机窗口，服务恢复后页面才提示刷新。服务器版默认关闭该入口，需显式设置 `EXCELMANUS_WEB_UPGRADE_ENABLED=1` 并启用管理员登录保护；多 worker、分机部署或安装包环境仍由运维机部署，桌面版安装新包。详见 [升级与部署](hot-update-design.md)。

内置升级备份包含主库各数据库、`config.env`、`.secret_key` 及 `data/`、`memory/`、`skillpacks/` 等目录，但不是完整 profile 备份；登记在数据目录外的工作区、外部数据库和部署清单不在其中。迁移和灾难恢复不能仅依赖自动生成的备份目录。

备份前先停止相关进程，使 SQLite、运行状态和文件保持一致。至少保存：

- 整个 `EXCELMANUS_HOME`，包括主数据库与 `.secret_key`；若指定了外部 `EXCELMANUS_DB_PATH`，单独备份该数据库。
- 登记在数据目录外的所有工作区，连同其中的 `.excelmanus/revisions/`。
- 自定义技能、MCP 配置及进程管理器配置；它们可能不在 `EXCELMANUS_HOME` 下。

例如，停止服务后将 profile 备份到其目录之外：

```bash
tar -czf /secure-backups/excelmanus-profile.tar.gz -C /path/to/profile .
```

恢复时先备份当前数据，再恢复相匹配的数据库、密钥和文件，确认属主及权限后启动。代码回滚不会自动回滚数据库结构或所有工作区文件。会话删除也不等于清理文件修订、独立日志或历史备份。

旧 `users/{id}/` 目录和旧容器卷需要手工选择并搬迁，不能直接合并多个用户数据库。旧 `outputs/backups` 的修订迁移说明见 [配置参考](configuration.md)。

## 8. 检查与故障排查

部署后先检查服务，再执行一个小规模的实际文件任务：

```bash
curl --fail http://127.0.0.1:8000/api/v1/health
curl --fail https://your-domain.example/api/v1/health
```

还应确认管理令牌能阻止未授权 API 请求、模型能够响应、上传和下载可用、SSE 持续到达，以及一次编辑与版本恢复符合预期。不要仅凭 HTTP 200 判断发布成功。

| 现象 | 优先检查 |
| --- | --- |
| 前端 502 | Next.js / API 进程、代理目标端口、构建产物与服务日志 |
| API 401 | 浏览器输入的管理令牌、进程环境、代理是否转发 Authorization |
| 模型无法调用 | 当前激活档案、协议、Base URL、模型权限、服务商限流 |
| SSE 长时间无内容 | Nginx 缓冲、读超时、运行时后端地址、跨域设置 |
| 端口变更后连错后端 | `EXCELMANUS_RUNTIME_BACKEND_ORIGIN` 和构建时旧地址 |
| 凭证无法解密 | 数据库是否与原 `.secret_key` 配套，是否切换了 data home |
| 重启后任务中断 | 用 `/resume` 查看恢复结果；在任务面板继续子代理，不重复启动同一写入 |
| 表格版本冲突 | 重新读取最新工作簿后再编辑，不覆盖其他会话的新版本 |
| 内存不足 | 前端使用兼容的预构建产物；检查文件规模、进程数量与日志 |

PM2 可使用 `pm2 list`、`pm2 logs excelmanus-api --lines 50 --nostream`；systemd 可使用 `systemctl status excelmanus-api`、`journalctl -u excelmanus-api -n 50`。分享日志前移除凭证、文件内容及其他敏感信息。
