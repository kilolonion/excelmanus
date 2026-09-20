// Offline acceptance in a real, hidden Electron renderer. No accounts or model
// calls: verifies isolation, IPC, OAuth callbacks and native blob downloads.
const path = require("node:path");
const fs = require("node:fs/promises");
const { tmpdir } = require("node:os");

async function run() {
  if (!process.versions.electron) {
    const { spawn } = require("node:child_process");
    const env = { ...process.env };
    delete env.ELECTRON_RUN_AS_NODE;
    const child = spawn(require("electron"), [__filename], { env, stdio: "inherit", windowsHide: true });
    child.on("error", (error) => { console.error(error); process.exitCode = 1; });
    child.on("exit", (code) => { process.exitCode = code ?? 1; });
    return;
  }
  const assert = require("node:assert/strict");
  const http = require("node:http");
  const { once } = require("node:events");
  const { app, BrowserWindow, ipcMain } = require("electron");
  const { configureWindowNavigation } = require("../src/window-compat");
  const { closeWindowsBeforeShutdown } = require("../src/window-lifecycle");
  const profile = await fs.mkdtemp(path.join(tmpdir(), "ExcelManus renderer 中文 "));
  app.setPath("userData", profile);
  const windows = [];
  const servers = [];
  const deadline = setTimeout(() => { console.error("RENDERER_TIMEOUT"); app.exit(1); }, 60_000);
  const listen = async (handler, port = 0) => {
    const server = http.createServer(handler);
    servers.push(server);
    server.listen(port, "127.0.0.1");
    await once(server, "listening");
    return `http://127.0.0.1:${server.address().port}`;
  };
  let code = 0;
  try {
    await app.whenReady();
    const frontendUrl = await listen((_req, res) => {
      res.setHeader("Content-Type", "text/html; charset=utf-8");
      res.end(`<html><body>桌面兼容验收<script>
        window.actions=[]; window.callback=null;
        window.addEventListener('excelmanus:menu-action', e=>window.actions.push(e.detail));
        window.addEventListener('message', e=>window.callback=e.data);
      </script></body></html>`);
    });
    const window = new BrowserWindow({ show: false, webPreferences: {
      sandbox: true, contextIsolation: true, nodeIntegration: false,
      preload: path.resolve(__dirname, "../src/preload.js"),
    } });
    windows.push(window);
    const external = [];
    configureWindowNavigation(window, {
      getFrontendUrl: () => frontendUrl,
      openExternal: async (url) => external.push(url),
      onError: (error) => { throw error; },
      oauthPreload: path.resolve(__dirname, "../src/oauth-preload.js"),
    });
    ipcMain.handle("excelmanus:select-folder", (event) => {
      assert.equal(event.senderFrame, window.webContents.mainFrame);
      return profile;
    });
    ipcMain.handle("excelmanus:pick-chat-files", () => ({
      files: [{ name: "图片.jpg", type: "image/jpeg", data: Buffer.from([255, 216, 255, 217]) }], skipped: [],
    }));
    await window.loadURL(frontendUrl);
    const evaluate = (script) => window.webContents.executeJavaScript(script, true);
    assert.equal(await evaluate("typeof require"), "undefined");
    assert.equal(await evaluate("window.excelManusDesktop.selectFolder()"), profile);
    assert.deepEqual(await evaluate(`window.excelManusDesktop.pickChatFiles().then(({files})=>{
      const f=files[0]; const file=new File([f.data.slice().buffer],f.name,{type:f.type});
      return {name:file.name,type:file.type,size:file.size};
    })`), { name: "图片.jpg", type: "image/jpeg", size: 4 });
    window.webContents.send("excelmanus:menu-action", "new-chat");
    const waitUntil = async (check) => {
      for (let attempt = 0; attempt < 100; attempt++) {
        if (await check()) return;
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      throw new Error("Renderer condition timed out");
    };
    await waitUntil(() => evaluate("window.actions.includes('new-chat')"));
    await evaluate("window.open('http://example.com/help','_blank'); void 0");
    await waitUntil(() => external.length === 1);
    assert.equal(external[0], "http://example.com/help");
    assert.equal(window.webContents.getURL(), frontendUrl + "/");

    const downloadPath = path.join(profile, "验收结果.txt");
    const downloaded = new Promise((resolve, reject) => {
      window.webContents.session.once("will-download", (_event, item) => {
        assert.equal(item.getFilename(), "验收结果.txt");
        item.setSavePath(downloadPath);
        item.once("done", (_event, state) => state === "completed" ? resolve() : reject(new Error(state)));
      });
    });
    await evaluate(`const a=document.createElement('a');
      a.href=URL.createObjectURL(new Blob(['desktop-download'])); a.download='验收结果.txt';
      document.body.appendChild(a); a.click(); a.remove();`);
    await downloaded;
    assert.equal(await fs.readFile(downloadPath, "utf8"), "desktop-download");

    await listen((_req, res) => {
      res.setHeader("Content-Type", "text/html; charset=utf-8");
      res.end(`<script>window.opener.postMessage({type:'callback',bridge:typeof window.excelManusDesktop},${JSON.stringify(frontendUrl)});</script>`);
    }, 1455);
    const created = once(window.webContents, "did-create-window");
    await evaluate("window.open('http://127.0.0.1:1455/auth/callback?code=fixture','codex-oauth','show=no'); void 0");
    const [popup] = await created;
    windows.push(popup);
    await waitUntil(() => evaluate("window.callback?.type==='callback'"));
    assert.equal(await evaluate("window.callback.bridge"), "undefined");
    await evaluate("window.onbeforeunload=()=>false; void 0");
    assert.equal(await closeWindowsBeforeShutdown([window]), false);
    assert.equal(window.isDestroyed(), false);
    await evaluate("window.onbeforeunload=null; void 0");
    assert.equal(await closeWindowsBeforeShutdown([popup, window]), true);
    assert.equal(window.isDestroyed(), true);
    console.log("RENDERER_COMPAT_OK", JSON.stringify({ platform: process.platform, electron: process.versions.electron, profile }));
  } catch (error) {
    code = 1;
    console.error(error);
  } finally {
    clearTimeout(deadline);
    for (const window of windows) if (!window.isDestroyed()) window.destroy();
    for (const server of servers) { server.closeAllConnections(); server.close(); }
    app.exit(code);
  }
}

void run().catch((error) => { console.error(error); process.exitCode = 1; });
