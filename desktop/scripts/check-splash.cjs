// Offline splash acceptance in a real, hidden Chromium renderer.
// Run: node desktop/scripts/check-splash.cjs
const path = require("node:path");
const fs = require("node:fs/promises");
const { spawn, execFileSync } = require("node:child_process");

async function run() {
  if (!process.versions.electron) {
    const { build } = require("../../web/node_modules/esbuild");
    const webRoot = path.resolve(__dirname, "../../web");
    const output = path.resolve(__dirname, "../.build/splash-check");
    await fs.mkdir(output, { recursive: true });
    execFileSync(process.execPath, [path.join(webRoot, "scripts/gen-splash.mjs"), path.join(output, "desktop.html")], { stdio: "inherit", windowsHide: true });
    const config = { bundle: true, tsconfig: path.join(webRoot, "tsconfig.json"), absWorkingDir: webRoot };
    await build({ ...config, platform: "node", format: "cjs", outfile: path.join(output, "markup.cjs"), stdin: {
      resolveDir: webRoot, loader: "tsx", contents: `
        import { renderToString } from "react-dom/server";
        import { LoadingScreen } from "@/components/ui/LoadingScreen";
        import { SPLASH_CRITICAL_CSS } from "@/components/ui/splash-critical";
        export const markup = renderToString(<LoadingScreen />);
        export const css = SPLASH_CRITICAL_CSS;
      `,
    } });
    const { markup, css } = require(path.join(output, "markup.cjs"));
    const icon = await fs.readFile(path.join(webRoot, "public/icon.png"));
    await fs.writeFile(path.join(output, "icon.png"), icon);
    const head = `<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style>`;
    await fs.writeFile(path.join(output, "web.html"), `<!doctype html><html lang="zh-CN"><head>${head}</head><body style="margin:0">${markup.replaceAll('"/icon.png"', `"data:image/png;base64,${icon.toString("base64")}"`)}</body></html>`);

    const stubs = {
      "next/navigation": "export const usePathname = () => '/';",
      "@/stores/auth-config-store": `
        const checkBackendHealth = async () => {
          window.fixture.healthCalls++;
          if (!window.fixture.serviceUp) throw Error('offline');
          return true;
        };
        export const useAuthConfigStore = Object.assign(selector => selector({ checkBackendHealth }), {
          getState: () => ({ authRequired: false, checkBackendHealth }),
          setState() {},
        });
      `,
      "@/lib/access-api": `
        export function fetchAccessStatus(options = {}) {
          window.fixture.accessCalls++;
          return new Promise((resolve, reject) => {
            const abort = () => { clearTimeout(timer); reject(Error('aborted')); };
            const timer = setTimeout(() => {
              options.signal?.removeEventListener('abort', abort);
              resolve({ auth_required: false, authenticated: true, login_method: 'none' });
            }, window.fixture.accessDelay);
            if (options.signal?.aborted) abort();
            else options.signal?.addEventListener('abort', abort, { once: true });
          });
        }
      `,
      "@/lib/api": "export const AUTH_REQUIRED_EVENT = 'auth-required';",
      "@/stores/health-hub-store": `
        export const ensureHealthHubPolling = () => { window.fixture.pollingStarted = true; };
        export const useHealthHubStore = selector => selector({});
      `,
      "@/components/VersionUpdateToast": "export const VersionUpdateToast = () => null;",
      "@/components/GlobalRestartOverlay": "export const GlobalRestartOverlay = () => null;",
      "@/components/LoginGate": "export const LoginGate = () => null;",
      "./client-layout": "if (window.fixture.layoutFails) throw Error('missing chunk'); export const ClientLayout = ({ children }) => children;",
    };
    await build({ ...config, platform: "browser", format: "iife", outfile: path.join(output, "shell.js"), define: { "process.env.NODE_ENV": '"development"' },
      plugins: [{ name: "startup-fixture", setup(builder) {
        builder.onResolve({ filter: /.*/ }, args => {
          // Use a native import so a rejected chunk stays rejected for both
          // Strict Mode effects, matching the application's module loader.
          if (args.path === "./client-layout") return { path: `data:text/javascript;base64,${Buffer.from(stubs[args.path]).toString("base64")}`, external: true };
          return stubs[args.path] ? { path: args.path, namespace: "fixture" } : undefined;
        });
        builder.onLoad({ filter: /.*/, namespace: "fixture" }, args => ({ contents: stubs[args.path], loader: "js" }));
      } }],
      stdin: { resolveDir: webRoot, loader: "tsx", contents: `
        import { StrictMode } from "react";
        import { createRoot } from "react-dom/client";
        import { AppShell } from "@/app/app-shell";
        const failure = location.search.includes('failure');
        window.fixture = { healthCalls: 0, accessCalls: 0, accessDelay: failure ? 0 : 600, serviceUp: failure, layoutFails: failure };
        createRoot(document.getElementById('root')).render(<StrictMode><AppShell><div id="workspace">Workspace ready</div></AppShell></StrictMode>);
      ` },
    });
    await fs.writeFile(path.join(output, "shell.html"), `<!doctype html><html lang="zh-CN"><head>${head}</head><body style="margin:0"><div id="root"></div><script src="shell.js"></script></body></html>`);
    const env = { ...process.env };
    delete env.ELECTRON_RUN_AS_NODE;
    const child = spawn(require("electron"), [__filename, output], { env, stdio: "inherit", windowsHide: true });
    child.on("error", error => { console.error(error); process.exitCode = 1; });
    child.on("exit", code => { process.exitCode = code ?? 1; });
    return;
  }

  const assert = require("node:assert/strict");
  const { once } = require("node:events");
  const { app, BrowserWindow } = require("electron");
  const output = process.argv[2];
  app.setPath("userData", await fs.mkdtemp(path.join(require("node:os").tmpdir(), "em-splash-check-")));
  let stage = "starting renderer";
  const deadline = setTimeout(() => { console.error(`Splash check timed out: ${stage}`); app.exit(1); }, 45_000);
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  let window;
  let code = 0;
  try {
    await app.whenReady();
    window = new BrowserWindow({ show: false, width: 1200, height: 900, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false, offscreen: true } });
    const evaluate = script => window.webContents.executeJavaScript(script);
    const waitFor = async script => {
      for (let i = 0; i < 80; i++) {
        if (await evaluate(script)) return;
        await delay(50);
      }
      throw Error(`Condition timed out: ${script}`);
    };
    const capture = async name => {
      // Resolve the root-relative public icon for this file:// fixture too.
      await evaluate(`Promise.all([...document.images].map(async image => {
        if (image.getAttribute('src') === '/icon.png') image.src = './icon.png';
        await image.decode();
      })).then(() => undefined)`);
      await fs.writeFile(path.join(output, name), (await window.webContents.capturePage()).toPNG());
    };
    stage = "loading desktop splash";
    await window.loadFile(path.join(output, "desktop.html"));
    const debuggerApi = window.webContents.debugger;
    debuggerApi.attach("1.3");
    await debuggerApi.sendCommand("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "no-preference" }] });
    stage = "desktop feedback";
    await evaluate("window.__emSplashSetStatus('正在启动本地服务...')");
    assert.equal(await evaluate("document.querySelector('.em-splash-status-text').textContent"), "正在启动本地服务...");
    await delay(4500);
    const transform = () => evaluate("getComputedStyle(document.querySelector('.em-splash-progress-fill')).transform");
    const first = await transform();
    await delay(250);
    assert.notEqual(await transform(), first, "progress must keep moving beyond the old 4.2s plateau");
    assert.match(await evaluate("document.querySelector('.em-splash-elapsed').textContent"), /已等待 [4-9] 秒/);

    // Offscreen paint receives compositor frames while renderer JavaScript is
    // deliberately busy. Compare only paints inside that blocked interval.
    stage = "busy main thread animation";
    const frames = [];
    const onPaint = (_event, _rect, image) => frames.push({ data: require("node:crypto").createHash("sha256").update(image.toBitmap()).digest("hex"), timestamp: Date.now() / 1000 });
    window.webContents.on("paint", onPaint);
    const blocked = await evaluate(`new Promise(resolve => setTimeout(() => {
      const start = Date.now() / 1000;
      const until = performance.now() + 2000;
      while (performance.now() < until) {}
      resolve({ start, end: Date.now() / 1000 });
    }, 200))`);
    window.webContents.removeListener("paint", onPaint);
    const movingFrames = frames.filter(frame => frame.timestamp > blocked.start + 0.2 && frame.timestamp < blocked.end - 0.2);
    assert(new Set(movingFrames.map(frame => frame.data)).size > 2, "compositor feedback must keep changing while JavaScript is blocked");

    stage = "desktop long wait";
    await evaluate("Date.now = ((original) => () => original() + 65000)(Date.now); void 0");
    await waitFor("document.querySelector('.em-splash-elapsed').textContent.includes('分')");
    assert.match(await evaluate("document.querySelector('.em-splash-hint').textContent"), /帮助/);
    await capture("desktop-long-wait.png");

    // SSR-only markup has no script or React: first-paint motion and the delayed
    // reload link must work without hydration.
    stage = "web first paint and reduced motion";
    await window.loadFile(path.join(output, "web.html"));
    assert.equal(await evaluate("getComputedStyle(document.querySelector('.em-splash-recovery')).visibility"), "hidden");
    await evaluate("document.querySelector('.em-splash-recovery').getAnimations()[0].currentTime = 31000");
    await waitFor("getComputedStyle(document.querySelector('.em-splash-recovery')).visibility === 'visible'");
    await capture("web-long-wait.png");
    await debuggerApi.sendCommand("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
    assert.equal(await evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"), true);
    const reducedFirst = await transform();
    await delay(350);
    assert.notEqual(await transform(), reducedFirst, "essential progress feedback must still move with reduced motion enabled");
    assert.equal(await evaluate("getComputedStyle(document.querySelector('.em-splash-arc')).animationName"), "none", "decorative rotation stays disabled in reduced motion");
    assert.equal(await evaluate("getComputedStyle(document.querySelector('.em-splash-recovery')).visibility"), "visible");
    window.setContentSize(375, 667);
    await delay(100);
    assert(await evaluate("document.querySelector('.em-splash-recovery a').getBoundingClientRect().bottom <= innerHeight"), "mobile recovery stays reachable");
    await capture("web-mobile.png");

    stage = "retry lifecycle";
    await window.loadFile(path.join(output, "shell.html"));
    await waitFor("window.fixture?.healthCalls === 2");
    await delay(200);
    assert.equal(await evaluate("window.fixture.healthCalls"), 2, "Strict Mode cleanup must not resurrect a retry loop");
    assert.equal(await evaluate("Boolean(document.querySelector('#workspace'))"), false);
    await waitFor("window.fixture.healthCalls === 3");
    assert.equal(await evaluate("Boolean(window.fixture.pollingStarted)"), false);
    await evaluate("window.fixture.serviceUp = true");
    await waitFor("Boolean(document.querySelector('#workspace'))");
    assert.equal(await evaluate("window.fixture.pollingStarted"), true);

    stage = "interface failure and reload";
    await window.loadFile(path.join(output, "shell.html"), { query: { failure: "1" } });
    await waitFor("Boolean(document.querySelector('[role=alert]'))");
    assert.equal(await evaluate("getComputedStyle(document.querySelector('.em-splash-recovery')).visibility"), "visible");
    assert.equal(await evaluate("Boolean(document.querySelector('[role=progressbar]'))"), false);
    await capture("load-error.png");
    const reloaded = once(window.webContents, "did-finish-load");
    await evaluate("document.querySelector('.em-splash-recovery a').click()");
    await reloaded;
    console.log("SPLASH_FEEDBACK_OK", JSON.stringify({ blockedAnimationFrames: movingFrames.length, output, scenarios: ["long wait", "busy main thread", "desktop status", "SSR without hydration", "reduced motion", "mobile layout", "Strict Mode retry cleanup", "service recovery", "chunk failure", "reload"] }));
  } catch (error) {
    code = 1;
    console.error(stage, error);
  } finally {
    clearTimeout(deadline);
    if (window && !window.isDestroyed()) window.destroy();
    app.exit(code);
  }
}

void run().catch(error => { console.error(error); process.exitCode = 1; });
