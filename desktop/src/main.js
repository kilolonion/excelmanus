const { app, BrowserWindow, Menu, dialog, ipcMain, shell, screen, net: electronNet } = require("electron");
const { appendFileSync, createWriteStream, existsSync, mkdirSync, readFileSync, writeFileSync } = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const { spawn } = require("node:child_process");
const { stopProcess: stopChild } = require("./process-lifecycle");
const { configureWindowNavigation, initialWindowBounds, isAppUrl } = require("./window-compat");
const { readPickedFiles } = require("./picked-files");
const { closeWindowsBeforeShutdown } = require("./window-lifecycle");
const { allowPrivateNetwork } = require("./mobile-network");
const { createUpdateService } = require("./updates");
const { installDownloadedUpdate } = require("./update-install");

const LOOPBACK = "127.0.0.1";
const STARTUP_TIMEOUT_MS = 120_000;
const REPO_URL = "https://github.com/kilolonion/excelmanus";
const updates = createUpdateService({
  current: app.getVersion(),
  // Chromium 网络栈，自动使用系统代理，避免直连 api.github.com 失败
  fetchImpl: (url, init) => electronNet.fetch(url, init),
  downloadDirectory: () => path.join(app.getPath("userData"), "updates"),
  installImpl: installDesktopUpdate,
  onStatus: status => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    mainWindow.setProgressBar(status.phase === "downloading" ? (status.percent === null ? 2 : status.percent / 100) : -1);
    if (frontendUrl && isAppUrl(mainWindow.webContents.getURL(), frontendUrl)) {
      mainWindow.webContents.send("excelmanus:update-status", status);
    }
  },
});

let mainWindow = null;
let backendProcess = null;
let frontendProcess = null;
let logStream = null;
let shuttingDown = false;
let frontendUrl = null;
let backendPort = null;
let frontendPort = null;
let backendRestarts = 0;
let rendererRecoveries = 0;
let stopPromise = null;
let quitRequested = false;
let mobilePairing = null;
const RESTART_EXIT_CODE = 75;
// A second process must not open the same profile/database concurrently.
if (process.env.EXCELMANUS_HOME) app.setPath("userData", path.resolve(userDataRoot(), "browser"));
mkdirSync(app.getPath("userData"), { recursive: true });
const ownsInstance = app.requestSingleInstanceLock();
if (!ownsInstance) app.quit();
app.on("second-instance", () => {
  if (mainWindow) { mainWindow.restore(); mainWindow.focus(); }
});

function projectRoot() {
  return path.resolve(__dirname, "..", "..");
}

function resourceRoot() {
  return app.isPackaged ? process.resourcesPath : path.join(projectRoot(), "desktop", ".build");
}

function userDataRoot() {
  const root = process.env.EXCELMANUS_HOME || path.join(app.getPath("userData"), "profile");
  mkdirSync(root, { recursive: true });
  return root;
}

function log(message) {
  const line = `[${new Date().toISOString()}] ${message}\n`;
  if (logStream) logStream.write(line);
  else appendFileSync(path.join(userDataRoot(), "desktop.log"), line, "utf8");
  console.log(line.trim());
}

function openLogStream() {
  const logDir = path.join(userDataRoot(), "logs");
  mkdirSync(logDir, { recursive: true });
  logStream = createWriteStream(path.join(logDir, "desktop.log"), { flags: "a" });
}

function freePort(preferred = 0) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(preferred, LOOPBACK, () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}

async function waitForHttp(url, timeoutMs = STARTUP_TIMEOUT_MS, child = null) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    if (shuttingDown) throw new Error("启动已取消");
    if (child && (child.exitCode !== null || child.signalCode !== null)) throw new Error(`服务已退出，无法访问 ${url}`);
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1_500) });
      if (response.ok) return;
      lastError = new Error(`${url} returned HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`等待服务就绪超时: ${url}; ${lastError?.message || "unknown error"}`);
}

function spawnLogged(command, args, options, label) {
  log(`启动 ${label}: ${command} ${args.join(" ")}`);
  const child = spawn(command, args, {
    ...options,
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
    detached: process.platform !== "win32",
  });
  child.stdin.on("error", (error) => log(`${label} stdin: ${error.message}`));
  child.stdout.on("data", (chunk) => log(`[${label}] ${chunk.toString().trimEnd()}`));
  child.stderr.on("data", (chunk) => log(`[${label}:stderr] ${chunk.toString().trimEnd()}`));
  child.on("error", (error) => log(`${label} 进程错误: ${error.message}`));
  child.on("exit", (code, signal) => {
    log(`${label} 已退出 code=${code} signal=${signal || ""}`);
    if (!shuttingDown && label === "backend" && code === RESTART_EXIT_CODE && backendRestarts < 3) {
      backendRestarts += 1;
      // Drain the previous process group, then restart on the SAME origin/port.
      void stopProcess(child, label).then(async () => {
        if (shuttingDown) return;
        backendProcess = startBackend(backendPort, frontendPort);
        await waitForHttp(`http://${LOOPBACK}:${backendPort}/api/v1/health`, undefined, backendProcess);
        backendRestarts = 0;
      }).catch((error) => {
        if (shuttingDown) return;
        log(`重启失败: ${error.message}`);
        dialog.showErrorBox("ExcelManus 重启失败", error.message);
      });
      return;
    }
    if (!shuttingDown) {
      void dialog.showMessageBox({
        type: "error",
        title: "ExcelManus 启动失败",
        message: `${label} 进程异常退出。`,
        detail: `退出码: ${code ?? "unknown"}\n日志: ${path.join(userDataRoot(), "logs", "desktop.log")}`,
      });
    }
  });
  return child;
}

function pythonCommand() {
  if (process.env.EXCELMANUS_PYTHON) return process.env.EXCELMANUS_PYTHON;
  const candidate = process.platform === "win32"
    ? path.join(projectRoot(), ".venv", "Scripts", "python.exe")
    : path.join(projectRoot(), ".venv", "bin", "python");
  return existsSync(candidate) ? candidate : (process.platform === "win32" ? "python.exe" : "python3");
}

function backendExecutable() {
  const base = path.join(resourceRoot(), "backend", "excelmanus-backend");
  const name = process.platform === "win32" ? "excelmanus-backend.exe" : "excelmanus-backend";
  const candidates = [path.join(base, name), path.join(resourceRoot(), "backend", name)];
  return candidates.find((candidate) => existsSync(candidate)) || null;
}

function nodeExecutable() {
  const name = process.platform === "win32" ? "node.exe" : "node";
  const bundled = path.join(resourceRoot(), "runtime", name);
  if (!existsSync(bundled)) throw new Error(`缺少 Node runtime: ${bundled}`);
  return bundled;
}

function splashFile() {
  const file = path.join(resourceRoot(), "splash.html");
  if (!existsSync(file)) throw new Error(`缺少启动页: ${file}`);
  return file;
}

function frontendRoot() {
  const root = path.join(resourceRoot(), "frontend");
  if (!existsSync(path.join(root, "server.js"))) {
    throw new Error(`Next standalone server.js 不存在: ${root}`);
  }
  return root;
}

function startBackend(port, frontendPort) {
  const env = {
    ...process.env,
    EXCELMANUS_HOME: userDataRoot(),
    EXCELMANUS_DESKTOP: "1",
    EXCELMANUS_DESKTOP_CONTROL_STDIN: "1",
    // Redirect bytecode caches into userData: keeps the install tree read-only
    // while still letting source-run interpreters skip recompiles on relaunch.
    PYTHONPYCACHEPREFIX: path.join(userDataRoot(), "pycache"),
    EXCELMANUS_DEPLOY_MODE: "standalone",
    EXCELMANUS_API_HOST: LOOPBACK,
    EXCELMANUS_API_PORT: String(port),
    // REST/SSE requests from the standalone frontend are direct requests in
    // the desktop build. Allow the dynamically allocated frontend origin.
    EXCELMANUS_FRONTEND_PORT: String(frontendPort),
  };
  const executable = backendExecutable();
  if (executable) {
    const python = path.join(resourceRoot(), "runtime", "python", process.platform === "win32" ? "python.exe" : "bin/python3");
    if (!existsSync(python)) throw new Error(`缺少 Python runtime: ${python}`);
    env.EXCELMANUS_RUN_PYTHON = python;
    env.PATH = [path.dirname(python), path.join(resourceRoot(), "runtime"), process.env.PATH || ""].join(path.delimiter);
    const bundledCwd = path.join(userDataRoot(), "data");
    mkdirSync(bundledCwd, { recursive: true });
    return spawnLogged(executable, ["--host", LOOPBACK, "--port", String(port)], {
      cwd: bundledCwd,
      env,
    }, "backend");
  }
  if (app.isPackaged) throw new Error("打包产物缺少 backend");
  return spawnLogged(pythonCommand(), [path.join(projectRoot(), "desktop", "backend_runner.py"), "--host", LOOPBACK, "--port", String(port)], {
    cwd: projectRoot(),
    env,
    }, "backend");
}

function startFrontend(port, backendPort) {
  const cwd = frontendRoot();
  const env = {
    ...process.env,
    PORT: String(port),
    HOSTNAME: LOOPBACK,
    NODE_PATH: path.join(cwd, "next_modules"),
    // Persist V8 compile cache across launches (Node >=22.1); trims standalone
    // server module loading on every start after the first.
    NODE_COMPILE_CACHE: path.join(userDataRoot(), "node-compile-cache"),
    BACKEND_INTERNAL_URL: `http://${LOOPBACK}:${backendPort}`,
    EXCELMANUS_RUNTIME_BACKEND_ORIGIN: `http://${LOOPBACK}:${backendPort}`,
  };
  return spawnLogged(nodeExecutable(), [path.join(resourceRoot(), "frontend-runner.cjs"), path.join(cwd, "server.js")], { cwd, env }, "frontend");
}

function configureWindowSecurity(window) {
  configureWindowNavigation(window, {
    getFrontendUrl: () => frontendUrl,
    openExternal: (url) => shell.openExternal(url),
    onError: (error) => log(`打开链接失败: ${error.message}`),
    oauthPreload: path.join(__dirname, "oauth-preload.js"),
  });
}

function attachRendererDiagnostics(window) {
  window.webContents.on("console-message", (event, ...legacy) => {
    let level = event && event.level;
    let message = event && event.message;
    let line = event && event.lineNumber;
    let sourceId = event && event.sourceId;
    if (level === undefined) {
      const [oldLevel, oldMessage, oldLine, oldSourceId] = legacy;
      level = { 2: "warning", 3: "error" }[oldLevel];
      message = oldMessage;
      line = oldLine;
      sourceId = oldSourceId;
    }
    if (level !== "warning" && level !== "error") return;
    log(`[renderer:${level}] ${message} (${sourceId}:${line})`);
  });
  window.webContents.on("render-process-gone", (event, details) => {
    log(`渲染进程异常退出: reason=${details.reason} exitCode=${details.exitCode}`);
    if (shuttingDown) return;
    if (frontendUrl && rendererRecoveries < 3) {
      rendererRecoveries += 1;
      loadFrontend(frontendUrl);
      return;
    }
    dialog.showErrorBox("ExcelManus 界面崩溃", `渲染进程异常退出: ${details.reason}\n日志: ${path.join(userDataRoot(), "logs", "desktop.log")}`);
  });
  window.webContents.on("did-fail-load", (event, code, description, url, isMainFrame) => {
    if (!isMainFrame || code === -3) return;
    log(`页面加载失败: ${code} ${description} ${url}`);
  });
  window.webContents.on("preload-error", (event, preloadPath, error) => {
    log(`preload 加载失败: ${preloadPath} ${error.message}`);
  });
  window.on("unresponsive", () => log("渲染进程无响应"));
  window.on("responsive", () => log("渲染进程恢复响应"));
}

function isFrontendSender(event) {
  if (!mainWindow || event.sender !== mainWindow.webContents || !frontendUrl) return false;
  // Older Electron builds can expose a new WebFrameMain wrapper for the same
  // main frame, so compare the trusted sender window and origin instead of
  // relying on object identity for `senderFrame`.
  const senderUrl = event.senderFrame?.url || event.sender.getURL();
  return isAppUrl(senderUrl, frontendUrl);
}

function frontendSenderWindow(event) {
  return isFrontendSender(event) ? BrowserWindow.fromWebContents(event.sender) : null;
}

function getMobilePairing() {
  if (!frontendUrl || !backendPort) throw new Error("工作区正在启动，请稍后再试");
  if (!mobilePairing) {
    const modulePath = app.isPackaged
      ? path.join(process.resourcesPath, "mobile-server", "mobile-pairing.cjs")
      : path.join(projectRoot(), "web", "server", "mobile-pairing.cjs");
    const { MobilePairing } = require(modulePath);
    mobilePairing = new MobilePairing({
      frontend: frontendUrl.replace(/\/$/, ""), backend: `http://${LOOPBACK}:${backendPort}`,
      backendToken: process.env.EXCELMANUS_MANAGE_TOKEN || "",
      stateFile: path.join(userDataRoot(), "mobile-pairing.json"),
    });
  }
  return mobilePairing;
}

ipcMain.handle("excelmanus:mobile-pairing", async (event, action, input = {}) => {
  if (!isFrontendSender(event)) throw new Error("手机连接请求来自无效页面");
  const pairing = getMobilePairing();
  if (action === "status") return { ...pairing.status(), platform: process.platform, desktop: true };
  if (action === "issue") return pairing.issue(typeof input.address === "string" ? input.address : undefined);
  if (action === "approve") return pairing.approve(String(input.id));
  if (action === "reject") return pairing.reject(String(input.id));
  if (action === "revoke") return pairing.revoke(String(input.id));
  if (action === "stop") return pairing.stop();
  if (action === "network-settings") {
    if (process.platform === "win32") await shell.openExternal("ms-settings:network-status");
    else throw new Error("请打开系统网络设置，让手机和电脑连接同一个路由器");
    return pairing.status();
  }
  if (action === "allow-firewall") {
    if (!pairing.status().enabled) throw new Error("请先开启手机连接");
    await allowPrivateNetwork(pairing.port);
    return pairing.status();
  }
  throw new Error("不支持的手机连接操作");
});

ipcMain.handle("excelmanus:select-folder", async (event) => {
  const senderWindow = frontendSenderWindow(event);
  if (!senderWindow) {
    throw new Error("文件夹选择请求来自无效页面");
  }
  if (!senderWindow.isDestroyed()) senderWindow.focus();
  const result = await dialog.showOpenDialog(senderWindow, {
    title: "选择源文件夹",
    buttonLabel: "选择文件夹",
    properties: ["openDirectory"],
  });
  return result.canceled ? null : (result.filePaths[0] || null);
});

ipcMain.handle("excelmanus:check-update", async event => {
  if (!isFrontendSender(event)) throw new Error("更新请求来自无效页面");
  return updates.check();
});

ipcMain.handle("excelmanus:download-update", async event => {
  if (!isFrontendSender(event)) throw new Error("更新请求来自无效页面");
  return updates.download();
});

for (const [action, handler] of [["update-status", () => updates.status()],
  ["cancel-update", () => updates.cancel()], ["install-update", () => updates.install()]]) {
  ipcMain.handle(`excelmanus:${action}`, async event => {
    if (!isFrontendSender(event)) throw new Error("更新请求来自无效页面");
    return handler();
  });
}

async function installDesktopUpdate(filename) {
  if (quitRequested || shuttingDown) throw new Error("应用正在退出，请稍后重试");
  quitRequested = true;
  try {
    return await installDownloadedUpdate({
      closeWindows: () => closeWindowsBeforeShutdown(BrowserWindow.getAllWindows()),
      stopServices: stopAll,
      // openPath honours OS security prompts/UAC and reports launch failures.
      // Windows starts NSIS; macOS opens the DMG for replacement in Finder.
      launch: () => shell.openPath(filename),
      recover: async () => {
        stopPromise = null;
        shuttingDown = false;
        // Finish any interrupted cleanup before opening another backend.
        await stopAll();
        stopPromise = null;
        shuttingDown = false;
        await boot();
      },
      exit: () => app.exit(0),
    });
  } catch (error) {
    if (!mainWindow) dialog.showErrorBox("更新未完成", `${error.message}\n安装包和用户数据已保留，请重新打开应用或手动安装。`);
    throw error;
  } finally { quitRequested = false; }
}

async function checkUpdateFromMenu() {
  try {
    const info = await updates.check();
    const result = await dialog.showMessageBox({
      type: "info", title: "检查 ExcelManus 更新",
      message: info.hasUpdate ? `发现新版本 ${info.latest}` : `当前版本 ${info.current} 已是最新正式版本`,
      detail: info.hasUpdate
        ? `${info.downloadUrl ? "应用内显示下载进度；下载并校验完成后退出并打开安装包。请先保存工作、等待任务完成。macOS 需在 Finder 中将新版替换到原位置。" : "该版本尚无适用的安装包，请稍后重试。"}\nWindows 安装时可选择迁移数据安装或卸载后安装。两种方式都保留设置、会话、工作区和用户文件，只替换 ExcelManus 程序。`
        : "检查完成，未修改应用或用户文件。",
      buttons: info.hasUpdate && info.downloadUrl ? ["下载安装包", "稍后"] : ["知道了"],
      defaultId: 0, cancelId: info.hasUpdate && info.downloadUrl ? 1 : 0,
    });
    if (result.response === 0 && info.hasUpdate && info.downloadUrl) await updates.download();
  } catch (error) {
    dialog.showErrorBox("检查更新失败", error.message);
  }
}

ipcMain.handle("excelmanus:pick-chat-files", async (event) => {
  const senderWindow = frontendSenderWindow(event);
  if (!senderWindow) {
    throw new Error("文件选择请求来自无效页面");
  }
  if (!senderWindow.isDestroyed()) senderWindow.focus();
  const result = await dialog.showOpenDialog(senderWindow, {
    title: "上传文件",
    buttonLabel: "上传",
    properties: ["openFile", "multiSelections"],
  });
  return readPickedFiles(result.canceled ? [] : result.filePaths);
});

function sendMenuAction(action) {
  mainWindow?.webContents.send("excelmanus:menu-action", action);
}

function showAboutDialog() {
  void dialog.showMessageBox({
    type: "info",
    title: "关于 ExcelManus",
    message: "ExcelManus",
    detail: [
      `版本 ${app.getVersion()}`,
      `Electron ${process.versions.electron}`,
      `Chromium ${process.versions.chrome}`,
      `Node.js ${process.versions.node}`,
      `数据目录: ${userDataRoot()}`,
    ].join("\n"),
  });
}

function buildAppMenu() {
  const isMac = process.platform === "darwin";
  const template = [
    ...(isMac ? [{
      label: app.name,
      submenu: [
        { role: "about", label: `关于 ${app.name}` },
        { type: "separator" },
        { role: "services", label: "服务" },
        { type: "separator" },
        { role: "hide", label: `隐藏 ${app.name}` },
        { role: "hideOthers", label: "隐藏其他" },
        { role: "unhide", label: "全部显示" },
        { type: "separator" },
        { role: "quit", label: `退出 ${app.name}` },
      ],
    }] : []),
    {
      label: "文件",
      submenu: [
        { label: "新建对话", accelerator: "CmdOrCtrl+N", click: () => sendMenuAction("new-chat") },
        { label: "上传文件…", accelerator: "CmdOrCtrl+U", click: () => sendMenuAction("upload-file") },
        { type: "separator" },
        { label: "设置…", accelerator: "CmdOrCtrl+,", click: () => sendMenuAction("open-settings") },
        { type: "separator" },
        { label: "打开数据目录", click: () => void shell.openPath(userDataRoot()) },
        ...(isMac ? [] : [
          { type: "separator" },
          { role: "quit", label: "退出" },
        ]),
      ],
    },
    {
      label: "编辑",
      submenu: [
        { role: "undo", label: "撤销" },
        { role: "redo", label: "重做" },
        { type: "separator" },
        { role: "cut", label: "剪切" },
        { role: "copy", label: "复制" },
        { role: "paste", label: "粘贴" },
        ...(isMac ? [{ role: "pasteAndMatchStyle", label: "粘贴并匹配样式" }] : []),
        { role: "selectAll", label: "全选" },
      ],
    },
    {
      label: "视图",
      submenu: [
        { label: "对话", accelerator: "CmdOrCtrl+1", click: () => sendMenuAction("show-chat") },
        { label: "表格", accelerator: "CmdOrCtrl+2", click: () => sendMenuAction("show-sheet") },
        { type: "separator" },
        { label: "侧边栏", accelerator: "CmdOrCtrl+B", click: () => sendMenuAction("toggle-sidebar") },
        { type: "separator" },
        { role: "reload", label: "重新加载" },
        { role: "forceReload", label: "强制重新加载" },
        { role: "toggleDevTools", label: "开发者工具" },
        { type: "separator" },
        { role: "resetZoom", label: "实际大小" },
        { role: "zoomIn", label: "放大" },
        { role: "zoomOut", label: "缩小" },
        { type: "separator" },
        { role: "togglefullscreen", label: "切换全屏" },
      ],
    },
    {
      label: "窗口",
      submenu: [
        { role: "minimize", label: "最小化" },
        ...(isMac ? [{ role: "zoom", label: "缩放" }] : []),
        { role: "close", label: "关闭" },
        ...(isMac ? [{ type: "separator" }, { role: "front", label: "全部置于顶层" }] : []),
      ],
    },
    {
      label: "帮助",
      submenu: [
        { label: "检查更新…", click: () => void checkUpdateFromMenu() },
        { label: "打开日志目录", click: () => void shell.openPath(path.join(userDataRoot(), "logs")) },
        { type: "separator" },
        { label: "项目主页", click: () => void shell.openExternal(REPO_URL) },
        { label: "反馈问题", click: () => void shell.openExternal(`${REPO_URL}/issues`) },
        ...(isMac ? [] : [
          { type: "separator" },
          { label: "关于 ExcelManus", click: showAboutDialog },
        ]),
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    ...initialWindowBounds(screen.getPrimaryDisplay().workArea),
    backgroundColor: "#ffffff",
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  mainWindow.once("ready-to-show", () => mainWindow?.show());
  configureWindowSecurity(mainWindow);
  attachRendererDiagnostics(mainWindow);
  mainWindow.on("closed", () => {
    mainWindow = null;
    // 启动阶段关窗即取消启动（继承原 startupWindow 语义）。
    if (!frontendUrl && !shuttingDown) app.quit();
  });
}

function loadFrontend(url) {
  if (!mainWindow) return;
  void mainWindow.loadURL(url).then(() => log("主窗口加载完成")).catch(error => log(`页面加载失败: ${error.message}`));
}

function setSplashStatus(text) {
  mainWindow?.webContents.executeJavaScript(`window.__emSplashSetStatus?.(${JSON.stringify(text)})`).catch(() => {});
}

function stopProcess(child, label) { return stopChild(child, label, log); }

function stopAll() {
  if (stopPromise) return stopPromise;
  shuttingDown = true;
  stopPromise = (async () => {
    if (mobilePairing) await mobilePairing.shutdown();
    // Renderers have closed before normal quit, so polling cannot race teardown.
    await stopProcess(backendProcess, "backend");
    backendProcess = null;
    await stopProcess(frontendProcess, "frontend");
    frontendProcess = null;
    if (logStream) { const stream = logStream; logStream = null; stream.end(); }
  })().catch(error => { stopPromise = null; shuttingDown = false; throw error; });
  return stopPromise;
}

async function boot() {
  openLogStream();
  buildAppMenu();
  createMainWindow();
  // Paint the splash while ports and child processes spin up underneath it.
  const splashReady = mainWindow.loadFile(splashFile())
    .catch((error) => log(`启动页加载失败: ${error.message}`));
  backendPort = await freePort();
  const portFile = path.join(userDataRoot(), "frontend-port");
  let preferred = 0;
  try { preferred = Number(readFileSync(portFile, "utf8")); } catch {}
  if (!Number.isInteger(preferred) || preferred < 1024 || preferred > 65535 || preferred === backendPort) preferred = 0;
  try { frontendPort = await freePort(preferred); } catch { frontendPort = await freePort(); }
  while (frontendPort === backendPort) frontendPort = await freePort();
  // Keep localStorage/IndexedDB origin stable across normal restarts.
  writeFileSync(portFile, String(frontendPort));
  if (shuttingDown) return;
  // Backend warmup (seconds of Python lifespan) and the standalone Next server
  // are independent — run both at once. As soon as the frontend can serve, the
  // app's own health-retry loading screen takes over the remaining wait.
  backendProcess = startBackend(backendPort, frontendPort);
  frontendProcess = startFrontend(frontendPort, backendPort);
  const backendReady = waitForHttp(`http://${LOOPBACK}:${backendPort}/api/v1/health`, undefined, backendProcess);
  const frontendReady = waitForHttp(`http://${LOOPBACK}:${frontendPort}/`, undefined, frontendProcess)
    .then(() => {
      frontendUrl = `http://${LOOPBACK}:${frontendPort}/`;
      if (shuttingDown || !mainWindow) return;
      loadFrontend(frontendUrl);
    });
  const allReady = Promise.all([backendReady, frontendReady]);
  await splashReady;
  setSplashStatus("正在启动本地服务...");
  await allReady;
  try {
    const pairing = getMobilePairing();
    if (pairing.enabled) await pairing.start();
  } catch (error) { log(`手机连接未恢复: ${error.message}`); }
}

app.whenReady().then(() => ownsInstance ? boot() : undefined).catch(async (error) => {
  if (shuttingDown) return;
  log(`启动失败: ${error.stack || error.message}`);
  await dialog.showMessageBox({ type: "error", title: "ExcelManus 启动失败", message: error.message });
  try { await stopAll(); app.exit(1); }
  catch (stopError) { log(`清理失败: ${stopError.message}`); dialog.showErrorBox("ExcelManus 清理失败", stopError.message); }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("activate", () => {
  if (process.platform === "darwin" && !mainWindow && frontendUrl && !quitRequested && !shuttingDown) {
    createMainWindow();
    loadFrontend(frontendUrl);
  }
});

app.on("before-quit", (event) => {
  event.preventDefault();
  if (stopPromise || quitRequested) return;
  log("收到退出请求，等待窗口关闭");
  quitRequested = true;
  void closeWindowsBeforeShutdown(BrowserWindow.getAllWindows()).then(async (closed) => {
    if (!closed) {
      log("窗口取消或未完成关闭，保留后台服务");
      quitRequested = false;
      await dialog.showMessageBox({ type: "info", title: "尚未退出", message: "窗口尚未关闭。请完成保存或处理未保存的更改后，再次退出。" });
      return;
    }
    log("所有窗口已关闭，正在停止后台服务");
    await stopAll();
    app.exit(0);
  }).catch(error => {
    quitRequested = false;
    log(`退出失败: ${error.message}`);
    if (!mainWindow && frontendUrl) { createMainWindow(); loadFrontend(frontendUrl); }
    dialog.showErrorBox("ExcelManus 未能完全退出", `${error.message}\n请重试退出；日志保留在用户数据目录。`);
  });
});

process.on("SIGTERM", () => app.quit());
process.on("SIGINT", () => app.quit());

// Test/CLI control is an inherited pipe, never an HTTP endpoint.
if (process.env.EXCELMANUS_DESKTOP_CONTROL_STDIN === "1") {
  // Electron replaces process.stdin with an already-ended stream on Windows.
  // Read the inherited pipe through fs's worker pool instead of that shim.
  const controlInput = process.platform === "win32"
    ? require("node:fs").createReadStream(null, { fd: 0, autoClose: false })
    : process.stdin;
  controlInput.on("error", error => log(`桌面控制通道错误: ${error.message}`));
  require("node:readline").createInterface({ input: controlInput })
    .on("line", line => { if (line === "shutdown") app.quit(); });
}
