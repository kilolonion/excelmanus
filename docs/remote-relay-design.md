# ExcelManus 远程访问与中继部署设计

状态：方案设计

## 1. 目标与边界

为 ExcelManus 增加一条不依赖局域网、也不要求用户开放入站端口的远程访问链路，同时保留现在的服务器直连方式。部署者可以只部署 ExcelManus，也可以额外部署中继；桌面、Android 和普通 Web 客户端只包含连接能力，不携带中继服务或本地连接器。

ExcelManus 当前是单实例、单用户边界。一个远程实例对应一个 EXCELMANUS_HOME 和一个工作区，不能把中继设计成多人数据隔离系统。需要多人隔离时，应运行多个 ExcelManus 实例，再分别注册到中继。

## 2. 两种访问模式

### 2.1 服务器直连

适用于 ExcelManus 已经部署在公网服务器、云主机或 VPN 内网中：

~~~text
浏览器 / Desktop / Android
          │ HTTPS
          ▼
      Nginx / 网关
        ├── /       → ExcelManus Web
        └── /api/v1 → ExcelManus FastAPI
~~~

这条链路不启用中继。继续使用现有的同源反向代理、登录保护、管理令牌和 SSE 配置。建议将 Web 的运行时后端地址设为 same-origin，让所有 API、健康检查和 SSE 都走当前域名。

### 2.2 个人本地服务中继

适用于 ExcelManus 在个人电脑、家庭服务器或企业内网，无法接受公网入站连接：

~~~text
客户端 ── HTTPS ──> 公网 Relay ── WSS/mTLS ──> Connector ── HTTP loopback ──> 本地 Web / API
                         ▲                         │
                         └──── 仅出站 443 连接 ──────┘
~~~

Connector 主动连接中继，路由器、防火墙、NAT 和公网地址变化都不影响访问。中继不需要知道或探测局域网地址，也不使用 UPnP、端口映射或 LAN 广播。

服务器上的 ExcelManus 也可以登记为中继的 HTTP upstream。这样同一个公网域名可以统一管理服务器实例和个人本地实例；服务器实例不需要运行 Connector。

## 3. 组件边界

### 3.1 Relay Server（可选）

独立进程，包含控制面和数据面：

- 控制面：实例、Connector、设备、访问令牌、一次性注册码、撤销和审计元数据。
- 数据面：公网 HTTPS 入口、实例路由、HTTP 流代理、SSE 刷新、WebSocket 升级和 Connector 隧道复用。
- 路由目标有两类：`http-upstream`（服务器内部或私网服务）和 `connector`（出站隧道）。
- 默认不保存文件、对话正文或模型请求，只保存短期路由状态和必要的审计元数据。

第一版可以用 Node/TypeScript 实现，与当前 `web/server/lan-gateway.cjs` 共用安全的流式代理逻辑；协议保持语言无关，后续可以替换成 Go/Rust 实现而不修改客户端。

### 3.2 Connector（可选独立安装包）

运行在个人本地服务所在机器上，职责只有三件事：

1. 使用一次性注册码换取 Connector 身份和 mTLS 凭证。
2. 向 Relay 建立长连接并定期发送心跳、版本和能力信息。
3. 将隧道中的请求严格转发到配置的两个回环地址：Web（例如 `127.0.0.1:3000`）和 API（例如 `127.0.0.1:8000`）。

Connector 不接受公网入站连接，不允许任意 URL、任意 Host 或任意文件路径作为上游，以避免把它变成 SSRF 或开放代理。

### 3.3 客户端

- Web：增加连接配置和中继登录状态，不打包 Relay 或 Connector。
- Desktop：继续打包本地 ExcelManus Web/API；远程访问只保存连接配置，Connector 作为单独安装包提供，不放入 Electron `extraResources`。
- Android：继续使用 WebView，只增加 HTTPS 中继地址、实例和设备令牌处理；APK 不包含 Node、Python、Relay 或 Connector。

## 4. 数据面协议

客户端使用普通 HTTPS 访问实例域名，Connector 与 Relay 使用一条出站 WSS 连接。WSS 连接采用二进制帧并复用多个逻辑流，避免每个 API 请求都建立 TCP/TLS 连接。

建议的帧类型：

~~~text
hello       Connector 身份、协议版本、API schema、能力、上次序列号
open        stream_id、request_id、method、path、headers、deadline
data        stream_id、seq、binary payload
end         stream_id、HTTP status、headers、结束标记
cancel      stream_id、取消原因
window      stream_id、可接收窗口，用于反压
ping/pong   心跳与租约续期
goaway      优雅关闭，不再接受新流
~~~

Relay 对外按 HTTP 语义转发，因此现有的 `POST /api/v1/chat/stream`、文件上传、文件下载和 SSE 订阅无需改成新的客户端协议。SSE 每个 chunk 必须立即刷新，不能由 Relay 缓冲。Relay 只在请求尚未发送到 Connector 时重试安全的 GET；不会自动重试写入、上传或审批请求。

请求必须带 `request_id` 和 `X-Request-ID`。连接断开时，客户端通过现有会话状态和 `chat/subscribe` 恢复任务，不由 Relay 持久化 Agent 状态。大文件使用流式转发，默认沿用后端 100 MB 限制；Relay 只做上限、超时和带宽控制。

## 5. 公网地址与路由

优先使用每个实例一个子域名，而不是路径前缀：

~~~text
https://<instance-id>.relay.example.com/
wss://relay.example.com/v1/connector/connect
https://relay.example.com/v1/control/...
~~~

原因是当前 Next.js 应用和 OAuth 回调按根域名工作，路径前缀会引入 `basePath`、Cookie Path、静态资源和回调地址问题。Relay 需要配置通配符 DNS 和 TLS 证书。没有通配符 DNS 时再提供 `/r/<instance-id>/` 兼容模式，并在边缘层去掉前缀。

实例域名下的路由规则：

- `/api/v1/*` → ExcelManus API。
- 其他页面、静态文件和 Next 页面 → ExcelManus Web。
- Web 的 API 地址使用同源；HTTPS 页面加载本地构建的 loopback 运行时配置时，前端应回退到同源，不能让浏览器直接访问 `127.0.0.1`。
- LAN 手机配对管理接口不通过 Relay 暴露。

## 6. 注册、认证与安全

### Connector 注册

1. 管理员在 Relay 控制面创建实例并生成一次性注册码，默认 10 分钟有效、只能使用一次。
2. 本地执行 `excelmanus-connector enroll --relay https://relay.example.com --code ...`，Connector 先验证 TLS，再交换公钥和实例信息。
3. Relay 签发只属于该 Connector 的短期 mTLS 证书和刷新凭据；Connector 将私钥放在系统密钥库或权限为 0600 的文件中。
4. Relay 显示在线状态、最后心跳、版本和能力；撤销时立即关闭隧道和该实例的活动流。

### 客户端访问

- 客户端令牌与 Connector 凭证分离。客户端只拿到 Relay 的实例访问令牌，不能看到本地的 `EXCELMANUS_MANAGE_TOKEN`。
- Relay 完成实例 ACL、过期、限速和设备撤销后，向 Connector 发送已授权的请求上下文。Connector 只在本地回环请求中附加管理令牌。
- 服务器直连模式继续由 ExcelManus 的登录保护或管理令牌处理；Relay 不应替服务器给所有访客自动注入令牌。
- 令牌不放在 URL 查询参数中。Android 使用 Keystore，Desktop 使用系统凭据存储或本地 profile，浏览器使用 HttpOnly、Secure、SameSite Cookie。
- 全链路使用 HTTPS/WSS；启用 HSTS、Origin 校验、CSRF 防护、请求体上限、连接数上限和审计限速。日志只记录实例 ID、请求 ID、状态码、字节数和耗时，不记录令牌、文件内容或提示词。

标准部署下 Relay 是受信任的数据平面，可以看到转发中的明文。若未来有不信任 Relay 的需求，再增加应用层端到端加密；这需要签名的客户端静态资源和新的加密请求封装，不能只靠在现有反向代理上加一个开关解决。

## 7. 配置与部署形态

默认部署不启用中继：

~~~text
profiles:
  core/server       ExcelManus Web + FastAPI
  relay             Relay Server（可选）
  connector         Connector（独立可选安装包）
  desktop/android   客户端，不包含 relay/connector
~~~

建议的进程和配置：

~~~text
excelmanus-api.service       EXCELMANUS_DEPLOY_MODE=server
excelmanus-web.service       EXCELMANUS_RUNTIME_BACKEND_ORIGIN=same-origin
excelmanus-relay.service     RELAY_PUBLIC_ORIGIN=https://relay.example.com
excelmanus-connector.service CONNECTOR_RELAY_URL=wss://relay.example.com/v1/connector/connect
~~~

源码和容器建议采用独立 profile：

~~~text
deploy/docker-compose.yml                  默认只启动 ExcelManus
deploy/docker-compose.relay.yml            显式启用 Relay
relay/package.json + relay/Dockerfile      Relay Server 独立依赖和镜像
connector/package.json                     Connector 独立依赖和平台包
~~~

Relay 元数据单机可用 SQLite；多节点时使用 PostgreSQL 保存配置、Redis 保存在线连接和发布/订阅，入口按 instance ID 对 Connector 长连接做粘性路由。Relay 重启不丢失实例注册；Connector 用指数退避加随机抖动自动重连。

## 8. 与当前代码的落点

1. 从 `web/server/lan-gateway.cjs` 提取“固定回环上游、请求头清理、SSE 不缓冲、超时、断开传播”的通用代理模块，LAN 网关和新 Connector 各自调用，不能直接把现有 LAN 网关暴露到公网。
2. 在 `web/src/lib/backend-origin.ts` 和运行时配置中明确 `same-origin` 的远程模式；远程页面不能使用桌面启动时的随机 loopback API 端口。
3. 在 Web 增加连接类型：`direct`、`relay`。连接对象至少包含 `origin`、`instance_id`、`access_token_ref`、`protocol_version` 和 `last_seen`，不保存管理令牌明文。
4. Desktop 的 `extraResources` 不加入 `relay/` 或 `connector/`。如需远程接入，设置页只生成/录入中继连接信息，用户单独安装 Connector。
5. Android 保留现有 HTTPS 手动连接能力；新增中继分享链接或扫码格式。现有 LAN pairing 和 `lan-gateway` 作为兼容功能单独标记，不与公网 Relay 共用认证入口。
6. 版本握手同时比较 Relay 协议版本、ExcelManus API schema 和 Web build ID；不兼容时返回可读的升级提示，而不是让页面出现大量 404 或 SSE 断流。

## 9. 建议的最小 API

~~~text
POST /v1/control/instances
POST /v1/control/instances/{id}/enrollments
POST /v1/control/instances/{id}/revoke
GET  /v1/control/instances/{id}/status
POST /v1/connector/enroll
WS   /v1/connector/connect
GET  /v1/instances/{id}/bootstrap
ANY  https://<instance-id>.relay.example.com/*
~~~

控制面只允许管理员或实例所有者访问。`bootstrap` 返回实例版本、连接能力和短期客户端配置，不返回 Connector 私钥或 ExcelManus 管理令牌。

## 10. 实施顺序

### Phase 1：直连和协议边界

- 把服务器直连作为正式 `direct` 模式。
- 固化 `instance_id`、API schema、Web build ID 和远程运行时配置。
- 提取当前 LAN 网关的流代理单元测试：上传、下载、SSE、取消、超时、头部清理和 Host 注入。

### Phase 2：单节点 Relay + Connector

- 实现单节点 Relay、SQLite 元数据和一次性注册。
- 实现 Node Connector 的出站 WSS、多路复用、重连、反压和两个回环上游。
- 使用一个公开实例子域名完成 Web、API、文件和长任务验收。

### Phase 3：客户端接入与运维

- Web、Desktop、Android 增加 direct/relay 连接配置和凭证撤销。
- 提供 Linux systemd、Windows 服务和 macOS launchd 的 Connector 安装脚本。
- 增加 Relay/Connector 指标、审计日志、健康检查和优雅停机。

### Phase 4：多节点与增强安全

- PostgreSQL、Redis、粘性路由和滚动升级。
- 连接器证书自动轮换、设备级 ACL、带宽配额和可选对象存储。
- 评估应用层端到端加密，不把它作为第一版中继的前提。

## 11. 验收标准

- 本地机器只需允许出站 TCP 443；关闭所有入站端口和 LAN 发现后仍可访问。
- Relay 停止时，客户端能区分“实例离线”和“ExcelManus 业务错误”；Connector 恢复后能自动上线。
- Chat SSE、长任务恢复、上传、下载、取消和审批流在 4G/公网环境可用。
- Relay 日志和数据库中没有管理令牌、文件内容和提示词。
- 默认服务器部署、Desktop 安装包和 Android APK 中不存在 Relay Server 或 Connector 可执行文件。
- `http-upstream` 和 `connector` 两类实例都能使用同一个客户端连接配置和同一个实例域名约定。
