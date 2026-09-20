const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { appendFileSync, createWriteStream, existsSync, mkdirSync, readFileSync, writeFileSync } = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const { spawn } = require("node:child_process");
const { stopProcess: stopChild } = require("./process-lifecycle");

const LOOPBACK = "127.0.0.1";
const STARTUP_TIMEOUT_MS = 120_000;

let mainWindow = null;
let backendProcess = null;
let frontendProcess = null;
let logStream = null;
let shuttingDown = false;
let frontendUrl = null;
let backendPort = null;
let frontendPort = null;
let backendRestarts = 0;
let stopPromise = null;
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
    await new Promise((resolve) => setTimeout(resolve, 250));
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
      }).catch((error) => { log(`重启失败: ${error.message}`); void dialog.showErrorBox("ExcelManus 重启失败", error.message); });
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
    PYTHONDONTWRITEBYTECODE: "1",
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
  }, "backend-dev");
}

function startFrontend(port, backendPort) {
  const cwd = frontendRoot();
  const env = {
    ...process.env,
    PORT: String(port),
    HOSTNAME: LOOPBACK,
    NODE_PATH: path.join(cwd, "next_modules"),
    BACKEND_INTERNAL_URL: `http://${LOOPBACK}:${backendPort}`,
    EXCELMANUS_RUNTIME_BACKEND_ORIGIN: `http://${LOOPBACK}:${backendPort}`,
  };
  return spawnLogged(nodeExecutable(), [path.join(resourceRoot(), "frontend-runner.cjs"), path.join(cwd, "server.js")], { cwd, env }, "frontend");
}

function configureWindowSecurity(window) {
  const allowedPopup = (url) => {
    try {
      const parsed = new URL(url);
      return !parsed.username && !parsed.password && ["https://auth.openai.com", "http://localhost:1455", "http://127.0.0.1:1455"].includes(parsed.origin);
    } catch { return false; }
  };
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (allowedPopup(url)) return { action: "allow" };
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) => {
    try { if (new URL(url).origin === new URL(frontendUrl).origin || allowedPopup(url)) return; } catch {}
    event.preventDefault();
  });
}

ipcMain.handle("excelmanus:select-folder", async (event) => {
  if (!mainWindow || event.sender !== mainWindow.webContents || !frontendUrl) {
    throw new Error("文件夹选择请求来自无效窗口");
  }
  let senderOrigin;
  try {
    senderOrigin = new URL(event.sender.getURL()).origin;
  } catch {
    throw new Error("无法确认文件夹选择请求来源");
  }
  if (senderOrigin !== new URL(frontendUrl).origin) {
    throw new Error("文件夹选择请求来自无效页面");
  }
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择源文件夹",
    buttonLabel: "选择文件夹",
    properties: ["openDirectory"],
  });
  return result.canceled ? null : (result.filePaths[0] || null);
});

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
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
    // Drain backend first while the UI can still observe the shutdown.
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
  createMainWindow();
  await mainWindow.loadFile(splashFile());
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
  setSplashStatus("正在启动后端服务...");
  backendProcess = startBackend(backendPort, frontendPort);
  await waitForHttp(`http://${LOOPBACK}:${backendPort}/api/v1/health`, undefined, backendProcess);
  if (shuttingDown) return;
  setSplashStatus("正在启动界面服务...");
  frontendProcess = startFrontend(frontendPort, backendPort);
  await waitForHttp(`http://${LOOPBACK}:${frontendPort}/`, undefined, frontendProcess);
  frontendUrl = `http://${LOOPBACK}:${frontendPort}/`;
  setSplashStatus("即将进入工作空间...");
  if (shuttingDown || !mainWindow) return;
  loadFrontend(frontendUrl);
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
  if (process.platform === "darwin" && !mainWindow && frontendUrl) {
    createMainWindow();
    loadFrontend(frontendUrl);
  }
});

app.on("before-quit", (event) => {
  event.preventDefault();
  if (stopPromise) return;
  void stopAll().then(() => app.exit(0)).catch(error => {
    log(`退出失败: ${error.message}`);
    if (!mainWindow && frontendUrl) { createMainWindow(); loadFrontend(frontendUrl); }
    dialog.showErrorBox("ExcelManus 未能完全退出", `${error.message}\n请重试退出；日志保留在用户数据目录。`);
  });
});

process.on("SIGTERM", () => app.quit());
process.on("SIGINT", () => app.quit());

// Test/CLI control is an inherited pipe, never an HTTP endpoint.
if (process.env.EXCELMANUS_DESKTOP_CONTROL_STDIN === "1") {
  require("node:readline").createInterface({ input: process.stdin })
    .on("line", line => { if (line === "shutdown") app.quit(); });
}
