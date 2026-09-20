# ExcelManus Android 客户端

Android APK 通过 WebView 连接现有 ExcelManus 网站，电脑或服务器继续运行 Next.js、Python 和 Excel 工具。最低 Android 8.0（API 26），同时要求支持文档开始脚本与 Web 消息接口的较新 Android System WebView；不满足时应用会提示更新。

这是个人实例的客户端，不提供多人账户隔离，也不在手机中运行 Python 或本地模型。

## 扫码绑定（推荐）

电脑端与 APK 都需要使用包含扫码功能的新版本。当前兼容修正版 APK 的 `versionCode` 为 3，可覆盖安装同一机器签名的早期预览包。最新本地产物为 `outputs/android/excelmanus-android-1.8.0-compat-preview.apk`，检查结果见 [验证记录](VALIDATION.md)。

1. 在电脑侧边栏点击**设置左边的扫码图标**，进入“扫码连接手机”。
2. 手机与电脑连接同一个路由器（电脑可接网线）。确认后点击“检查服务并生成二维码”。桌面版会自动找到当前前后端端口、检查服务并开启局域网入口，不必另开终端或手填令牌。
3. 在 APK 首页点击“扫一扫连接电脑”，允许相机权限，扫描电脑上的二维码；确认电脑地址与可信网络。
4. 两端显示相同六位核对码后，在电脑点击“是我的手机，允许连接”。手机自动保存连接并打开工作区。
5. 如连接失败，展开电脑引导内的排查项：打开网络设置、检查访客 Wi-Fi/VPN/网卡，或点击“允许专用网络连接”。Windows 将单独请求管理员权限，仅允许本程序当前端口、本地子网、专用网络；不会关闭整个防火墙。手机也可直接打开 Wi-Fi 设置再重试。

二维码 3 分钟有效且只能使用一次，刷新会取消尚未确认的请求；扫码后的确认窗口为 2 分钟。二维码不包含后端管理令牌。每台手机使用独立凭据，电脑端仅持久化其哈希；可以在同一引导中解除绑定或关闭入口。解除绑定会拒绝后续请求并断开该设备现有的数据流。该客户端仍与电脑共用同一工作区，不是隔离的多人账户。

桌面版记住绑定和入口端口，正常重启后自动恢复。已绑定时如果端口被占用，会提示处理冲突，不会悄悄换端口。切换路由器或电脑 IP 变化后需重新扫码。网络类型、Wi-Fi 密码及路由器设备隔离由系统/路由器管理，引导会带到相关设置，不能绕过这些限制。

源码部署的网页也提供同一引导，但启动前必须为前后端设置同一个 `EXCELMANUS_MANAGE_TOKEN`，并在扫码引导内填写该令牌。网页配对管理接口不会信任伪造的 localhost Host 或转发头；绑定的手机不能访问此管理接口。网页服务重启后，在电脑重新打开扫码引导即可恢复入口；系统防火墙需在服务器上配置。无头服务器或容器必须保证显示的局域网地址与端口可从手机访问。

配对使用可信局域网 HTTP，传输本身不加密；公网继续使用下文 HTTPS 手动连接方式。旧版手动连接和 `android/tools/lan-gateway.mjs` 保留。

## 手动连接

1. 安装 `app-debug.apk` 或本地生成的 `excelmanus-android-1.8.0-compat-preview.apk`。
2. 在电脑/服务器启动 ExcelManus，并准备网站根地址和管理令牌。
3. 在 APK 中输入网站根地址（例如 `https://excel.example.com`），填写令牌，然后点“连接工作区”。网页和 `/api/v1/*` 必须可从同一个地址访问。
4. 聊天附件通过 Android 系统文件选择器上传；导出结果时会出现系统保存位置选择器。单文件上传遵循后端当前 100 MB 限制；客户端结果保存最多 256 MB。

顶部菜单可修改连接、重新加载或清除连接。修改服务器地址会清空令牌输入，避免把旧实例凭据带到新实例。切换/清除连接会清除 WebView 网站存储和 Cookie；令牌使用 Android Keystore 加密保存在应用私有目录，不参与备份或设备迁移。

外部 HTTPS 链接在系统浏览器打开。新版网站会在 Android 上优先使用设备码连接 ChatGPT；旧版网站可在“其他连接方式”中选择设备码。模型账户也可先在电脑上配置。

局域网页面的复制按钮由客户端补充系统剪贴板写入能力，一次最多 16,384 个字符；网页不能通过此接口读取剪贴板。系统返回键依次关闭键盘、网页弹层、手机侧栏，再返回上一页。相机权限被拒绝后，可从说明弹窗进入应用设置。

切到桌面再回来时保留现有页面，并通知网页刷新任务状态。若系统重建页面，恢复已保存的连接与会话路径；尚未发送的草稿、进行中的上传/保存及未完成的扫码绑定不保证跨进程恢复，需要按提示重试。手机时间不参与二维码过期判断，过期和一次性使用由电脑验证。

## 连接自己的 Windows 电脑

手机与电脑处于同一可信局域网。以下从仓库根目录执行。

在第一个 PowerShell 窗口设置管理令牌并按现有方式启动前后端：

```powershell
$env:EXCELMANUS_MANAGE_TOKEN = Read-Host '设置一个至少 16 字符的随机管理令牌'
.\deploy\start.ps1 -Production -NoOpen -NoKillPorts
```

在第二个 PowerShell 窗口输入相同令牌，启动局域网入口：

```powershell
$env:EXCELMANUS_MANAGE_TOKEN = Read-Host '输入刚才设置的相同管理令牌'
node android/tools/lan-gateway.mjs
```

入口默认监听 8787，将网页转发到本机 3000、API 转发到本机 8000。终端会列出可填写到手机的 IP 地址。若已有服务使用其他端口：

```powershell
node android/tools/lan-gateway.mjs --frontend http://127.0.0.1:3001 --backend http://127.0.0.1:8001 --port 8787
```

在手机填写 `http://电脑局域网IP:8787`，勾选“允许连接可信局域网的 HTTP 地址”。只需允许手机访问电脑的 8787 端口；前后端可以继续绑定 loopback。`localhost`、`127.0.0.1` 在手机上指向手机自身。

局域网入口仅通过令牌保护 API，不加密 HTTP；公网部署请使用 HTTPS 反向代理。前后端的管理令牌与入口令牌必须相同。它不会自动发现桌面安装版的随机端口，需显式提供；推荐先用源码部署的固定端口。

入口不会启动或结束已有服务。关闭入口终端会断开手机连接，后端保持原有生命周期。推荐使用生产前端；入口不转发开发服务器的 HMR WebSocket。

## 连接服务器

沿用项目 `deploy/nginx.conf` 的同域转发方式，配置有效 HTTPS 证书和 `EXCELMANUS_MANAGE_TOKEN`。所有 `/api/v1/*` 转发到后端；SSE 关闭代理缓冲。手机只填 HTTPS 网站根地址，无需启用 HTTP 选项。地址不支持子路径部署、内嵌用户名密码或查询参数。

客户端把 API Origin 固定为当前所选网站，覆盖网页中指向服务器 loopback 的运行时设置。所有原生桥消息还会检查主框架和精确 Origin。证书错误不会被跳过；新实例首次连接会检查健康接口与令牌。

## 构建

需要 JDK 17 或与 Gradle 8.11.1 兼容的 JDK、Android SDK Platform 35 和 Build Tools 35.0.0。Gradle wrapper 已包含，并固定分发文件 SHA-256。

设置 `ANDROID_HOME`，或在 `android/local.properties` 中写入本机 `sdk.dir`；该文件不入库。

```powershell
cd android
.\gradlew.bat testDebugUnitTest lintDebug assembleDebug
```

macOS/Linux 使用 `bash gradlew`。产物：`android/app/build/outputs/apk/debug/app-debug.apk`。

调试包 ID 为 `com.excelmanus.android.debug`，标记为 preview；使用本机构建环境的调试签名。不同构建机器的调试签名可能不同，不能保证直接覆盖安装。正式发布应由发布者配置固定签名并逐次提高 `versionCode`；未配置签名的 release 产物不能直接安装。

## 验证

```powershell
node --test android/tests/bridge.test.cjs android/tests/lan-gateway.test.mjs android/tests/mobile-pairing.test.cjs
cd android
.\gradlew.bat testDebugUnitTest lintDebug assembleDebug assembleDebugAndroidTest
# 连接测试手机或启动模拟器，并确保测试设备已有 Wi-Fi 的私网 IPv4 地址后：
.\gradlew.bat connectedDebugAndroidTest
```

本地 Java 测试覆盖地址/Origin 校验、HTTP 范围和文件分块完整性；Node 测试覆盖原生桥初始化、中文名二进制分块、旧网页下载、令牌验证和 SSE 无缓冲转发。

Android 仪器测试使用设备自身私网地址上的 HTTP 测试页面及系统文档选择协议，验证真实 WebView 的启动注入、认证、文件上传/保存、局域网复制、返回键、后台恢复、Activity 重建、页面进程终止恢复及扫码绑定。电脑时钟偏移和旧页面回调由测试注入；摄像头扫描结果与文件选择结果由测试提供。不调用真实模型，也不使用用户账户。测试文件在运行后清理。测试缺少局域网地址时直接失败，避免误把跳过测试统计为通过。

持续构建见 `.github/workflows/android-build.yml`。初期仍需在目标真机上验收中文输入法、Univer 大表编辑、锁屏恢复、厂商文件选择器和真实订阅授权；自动测试不能代替这些验收。

