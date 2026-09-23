// Runs the real composer, SSE reader and Univer ribbon against a timed fixture server.
// Supply Playwright via NODE_PATH, as for the other browser checks.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { mkdir, writeFile } from "node:fs/promises";
import assert from "node:assert/strict";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const { createServer } = await import("vite");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const output = path.resolve(root, "../output/playwright/chat-stream");
await mkdir(output, { recursive: true });
let aborts = 0;
const server = await createServer({ root, configFile: false, cacheDir: path.join(root, "node_modules/.vite-chat-stream"),
  resolve: { alias: [
    { find: "@/lib/univer-modules", replacement: path.join(root, "src/__tests__/fixtures/multi-workbook-modules-browser.ts") },
    { find: "@", replacement: path.join(root, "src") },
  ] }, esbuild: { jsx: "automatic" },
  optimizeDeps: { include: ["react", "react-dom/client", "react-dom", "react/jsx-dev-runtime", "zustand", "zustand/middleware", "next/dynamic", "lucide-react", "@univerjs/core", "@univerjs/presets", "@univerjs/preset-sheets-core", "@univerjs/preset-sheets-core/locales/zh-CN", "@univerjs/sheets-ui"] },
  define: { "process.env.NODE_ENV": JSON.stringify("development") }, server: { host: "127.0.0.1", port: 0 },
  plugins: [{ name: "timed-chat-api", configureServer(server) {
    server.middlewares.use("/api/v1", (req, res, next) => {
      if (req.url === "/chat/stream") {
        res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-transform" });
        const emit = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
        emit("stream_init", { stream_id: "fixture-stream", seq: 0 });
        let index = 0;
        const timer = setInterval(() => {
          emit("text_delta", { content: `第${++index}段正在逐步输出。`, seq: index });
          if (index === 24) { clearInterval(timer); emit("done", {}); res.end(); }
        }, 80);
        res.on("close", () => clearInterval(timer));
        return;
      }
      if (req.url?.startsWith("/chat/abort")) aborts++;
      if (req.url?.startsWith("/files/excel/view?")) {
        const query = new URL(req.url, "http://fixture").searchParams;
        const file = query.get("path"), sheet = query.get("sheet") || "明细";
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify({ file: { workspaceKey: "id:stream-ws", relative: file }, content_version: "v1", active_sheet: sheet,
          with_styles: query.get("with_styles") === "1", sheets: [{ name: sheet, sheet_id: sheet, used: { rows: 30, cols: 8 } }],
          windows: [{ sheet, rect: { r0: 1, c0: 1, r1: 200, c1: 50 }, cells: { "1,1": { t: "s", v: "销售额", cached: "yes" } } }],
          coverage: { loaded: [{ sheet, r0: 1, c0: 1, r1: 200, c1: 50 }], unloaded: [] } }));
        return;
      }
      if (req.url?.startsWith("/")) {
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify({ models: [], files: [], messages: [], skills: [], commands: [], status: "ok" }));
        return;
      }
      next();
    });
  } }],
});
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1000, height: 800 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${server.resolvedUrls.local[0]}src/__tests__/fixtures/chat-stream-browser.html`);
  await page.waitForFunction(() => window.chatStore);
  await page.locator("textarea").fill("流式验证");
  await page.locator('[data-coach-id="coach-send-btn"]').click();
  await page.waitForFunction(() => window.chatStore.getState().isStreaming);
  await page.waitForTimeout(300);
  const during = await page.evaluate(() => ({
    streaming: window.chatStore.getState().isStreaming,
    stopVisible: !!document.querySelector('[data-coach-id="coach-stop-btn"]'),
    sendVisible: !!document.querySelector('[data-coach-id="coach-send-btn"]'),
    text: document.querySelector(".streaming-cursor")?.textContent,
  }));
  console.log("during", during);
  await page.screenshot({ path: path.join(output, "streaming.png") });
  await page.waitForFunction(() => !window.chatStore.getState().isStreaming);
  await page.evaluate(() => window.openWorkspaceFile("销售.xlsx"));
  await page.waitForFunction(() => document.querySelector('[data-univer-container] [aria-label="ribbon.menu"] [data-em-ribbon="history"]'));
  await page.waitForTimeout(700);
  const ribbon = await page.locator('[data-univer-container] [aria-label="ribbon.menu"]').evaluate((el) => ({
    width: el.clientWidth, scrollWidth: el.scrollWidth,
    tabs: [...el.querySelectorAll('[role="tab"]')].map((tab) => ({ label: tab.textContent, width: tab.clientWidth, height: tab.clientHeight, nowrap: getComputedStyle(tab).whiteSpace })),
  }));
  console.log("ribbon", ribbon);
  await page.screenshot({ path: path.join(output, "narrow-panel.png") });
  await writeFile(path.join(output, "result.json"), JSON.stringify({ during, ribbon, errors, aborts }, null, 2));
  assert.equal(during.stopVisible, true, "an active stream must show the stop control");
  assert.equal(during.sendVisible, false, "the inactive send control must be removed");
  assert.ok(during.text, "deltas must paint while the connection is open");
  assert.ok(ribbon.tabs.every((tab) => tab.height < 40 && tab.nowrap === "nowrap"), "ribbon tabs must stay horizontal in a narrow desktop pane");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ result: "passed", screenshots: output }));
} finally { await browser?.close(); await server.close(); }
