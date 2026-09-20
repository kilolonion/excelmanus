# 服务器登录保护

ExcelManus 使用一组管理员凭据保护整个实例，不恢复注册、多用户、用户目录或第三方身份登录。

## 在设置中管理

打开 **设置 → 安全**，使用“登录保护”开关，填写管理员账号和至少 12 个字符的密码后保存。确认密码必须一致；修改设置时密码留空会保留原密码。

开启或修改凭据后，所有浏览器会话被撤销，当前页面返回登录入口；关闭后允许直接访问。关闭选择在重启后仍然保留，服务器部署建议保持开启。退出登录也在同一页面。

## 首次部署

本机 standalone 默认关闭。`EXCELMANUS_DEPLOY_MODE=server`（即使后端只监听 loopback）首次启动必须有凭据，避免刚部署的服务被抢先进入。可在后端服务的进程环境设置：

```dotenv
EXCELMANUS_DEPLOY_MODE=server
EXCELMANUS_LOGIN_USERNAME=admin
EXCELMANUS_LOGIN_PASSWORD=替换为自己生成的至少12字符长密码
EXCELMANUS_LOGIN_SESSION_HOURS=12
EXCELMANUS_LOGIN_COOKIE_SECURE=true
```

`COOKIE_SECURE=true` 用于 HTTPS。本机 HTTP 测试用 `auto`；不要在 HTTP 测试时强制 Secure，否则浏览器可能拒绝保存 Cookie。

这些是后端进程环境变量，不是模型设置。项目根 `.env` 不会自动加载。systemd 可使用一个仅服务账号可读的 `EnvironmentFile`，PM2 则在启动/重载进程时注入环境并使用 `--update-env`。不要将密码写入 `NEXT_PUBLIC_*` 或前端运行时配置。

也可以先在本地打开设置页配置并启用登录，再带着同一个 `EXCELMANUS_HOME` 部署。设置页保存的账号、密码和开关优先于环境中的初始值。没有设置过开关时，配置密码或现有 `EXCELMANUS_MANAGE_TOKEN` 会自动开启保护。只有令牌时显示令牌登录；设置密码后显示账号密码登录。桌面和自动化客户端仍可携带原管理令牌请求接口。

## 反向代理

将前端和 `/api/` 放在同一 HTTPS 域名，后端监听 `127.0.0.1:8000`。前端设置 `EXCELMANUS_RUNTIME_BACKEND_ORIGIN=same-origin`，确保健康检查、登录、文件和 SSE 都走同一代理，不回退到浏览器所在机器的 8000 端口。

Nginx 的 API 转发应保留 Cookie、`Authorization`、`X-Requested-With` 和 `X-Forwarded-Proto`；不能为公网请求自动注入管理令牌，否则会绕过登录。使用仓库中的 `deploy/nginx.conf`，保留 SSE 的关闭缓冲设置。只有可信代理地址应获得后端对转发头的信任。

Cookie 使用 `HttpOnly`、`SameSite=Strict`，默认有效 12 小时，不使用 localStorage/sessionStorage 保存密码。跨站前后端部署可能被浏览器阻止 Cookie，优先采用同源代理；允许的跨域来源仅填写可信前端。旧的 URL `manage_token` 参数不再接受，脚本改用请求头。

## 持久化与恢复

`{EXCELMANUS_HOME}/access.db` 保存开关、账号、带随机盐的密码哈希、会话摘要及登录限速，不保存明文密码或原始 Cookie。该文件及 SQLite 辅助文件被工作区文件守卫保护，不参与模型配置导入/导出。多个 worker 必须使用同一个 `EXCELMANUS_HOME`。登录会话可跨重启继续使用；修改凭据、退出或超过有效期后失效。

忘记密码时，先停止后端，把 `access.db` **移到工作区之外的安全备份目录**，为后端配置新的 `EXCELMANUS_LOGIN_PASSWORD` 后重新启动。这会重置登录设置、清除会话并恢复环境中的账号密码，不修改主数据库或工作簿。不要在仍可公开访问时移除配置，避免 standalone 回到未保护状态。

模型、工作区和会话 API 继续使用当前版本字段，不需要 `user_id`、角色或旧版 JWT。入口为 `GET /api/v1/auth/status`、`POST /api/v1/auth/login`、`POST /api/v1/auth/logout`、`GET/PUT /api/v1/auth/settings`。浏览器的写请求须带 `X-Requested-With: ExcelManus`，共享前端 API 客户端已自动处理，包括上传和 SSE。
