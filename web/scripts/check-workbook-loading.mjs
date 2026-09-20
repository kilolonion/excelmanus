// Run with Node; playwright may be supplied through NODE_PATH. Uses the real
// React component and installed Univer, with a deterministic HTTP test server.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const { createServer } = await import("vite");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const server = await createServer({
  root, configFile: false,
  resolve: { alias: [
    { find: "@/lib/univer-modules", replacement: path.join(root, "src/__tests__/fixtures/univer-modules-browser.ts") },
    { find: "@", replacement: path.join(root, "src") },
  ] },
  esbuild: { jsx: "automatic" },
  optimizeDeps: { include: ["react", "react-dom/client", "react-dom", "react/jsx-dev-runtime", "zustand", "zustand/middleware", "lucide-react", "@univerjs/core", "@univerjs/presets", "@univerjs/preset-sheets-core", "@univerjs/preset-sheets-core/locales/zh-CN"] },
  define: { "process.env.NODE_ENV": JSON.stringify("development") },
  server: { host: "127.0.0.1", port: 0 },
});
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: Number(process.env.WORKBOOK_TEST_WIDTH || 1280), height: 800 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") console.error(message.text()); });
  const requests = [];
  const writes = [];
  let writeDelay = 0;
  let releaseFirstRead;
  const firstReadGate = new Promise((resolve) => { releaseFirstRead = resolve; });
  let firstReadHeld = false;
  let version = 1;
  let sheets = ["Main", "Second"];
  let cells = { "1,1": { t: "s", v: "original", cached: "yes" }, "1,2": { t: "n", v: 2, f: "=1+1", cached: "yes" }, "2,1": { t: "s", v: "merged", cached: "yes" }, "201,1": { t: "n", v: 201, cached: "yes" }, "401,1": { t: "n", v: 401, cached: "yes" } };
  const parseCell = (a1) => { const [, letters, row] = a1.match(/([A-Z]+)(\d+)/); return [Number(row), [...letters].reduce((v, c) => v * 26 + c.charCodeAt(0) - 64, 0)]; };
  await page.route("**/api/v1/files/excel/view?**", async (route) => {
    const params = new URL(route.request().url()).searchParams;
    requests.push(Object.fromEntries(params));
    const sheet = params.get("sheet") || "Main";
    const styles = params.get("with_styles") === "1";
    if (!sheets.includes(sheet)) return route.fulfill({ status: 404, json: { code: "SHEET_NOT_FOUND", error: "missing sheet" } });
    const expected = params.get("expected_version");
    if (expected && expected !== `v${version}`) return route.fulfill({ status: 409, json: { code: "STALE_VIEW", error: "stale", content_version: `v${version}` } });
    const [first, last] = (params.get("rect") || "A1:AX200").split(":");
    const [r0, c0] = parseCell(first), [r1, c1] = parseCell(last);
    const within = Object.fromEntries(Object.entries(cells).filter(([key]) => { const [r,c] = key.split(",").map(Number); return r >= r0 && r <= r1 && c >= c0 && c <= c1; }).map(([key, cell]) => [key, { ...cell, ...(styles ? { s: { bl: 1 } } : {}) }]));
    const otherFile = params.get("path").endsWith("other.xlsx");
    if (otherFile && within["1,1"]) within["1,1"].v = "other file";
    const data = {
      file: { workspaceKey: "id:browser-ws", relative: params.get("path").replace(/^\.\//, "") }, content_version: `v${version}`, active_sheet: "Main", with_styles: styles,
      sheets: sheets.map((name) => ({ name, sheet_id: name, used: { rows: 600, cols: 60 } })),
      windows: [{ sheet, rect: { r0,c0,r1,c1 }, cells: within, ...(styles ? { merges: r0 <= 2 && r1 >= 2 && c0 === 1 ? [{ min_row: 2,min_col: 1,max_row: 2,max_col: 2 }] : [], col_widths: c0 === 1 ? { A: 24 } : {}, row_heights: r0 === 1 ? { "1": 30 } : {} } : {}) }],
      coverage: { loaded: [{ sheet,r0,c0,r1,c1 }], unloaded: [] },
    };
    if (!styles && !firstReadHeld) {
      firstReadHeld = true;
      await firstReadGate;
    }
    if (styles) await new Promise((resolve) => setTimeout(resolve, 250));
    if (otherFile && !styles) await new Promise((resolve) => setTimeout(resolve, 400));
    await route.fulfill({ json: data }).catch(() => {});
  });
  await page.route("**/api/v1/files/excel/write", async (route) => {
    const body = route.request().postDataJSON(); writes.push(body);
    if (body.expected_version !== `v${version}`) return route.fulfill({ status: 409, json: { code: "VERSION_CONFLICT", status: "conflict", cells_written: 0 } });
    for (const op of body.operations) for (const cell of op.cells || []) {
      const [r,c] = parseCell(cell.cell);
      if (cell.value == null) delete cells[`${r},${c}`];
      else cells[`${r},${c}`] = { t: typeof cell.value === "number" ? "n" : "s", v: cell.value, cached: "yes" };
    }
    version++;
    if (writeDelay) await new Promise((resolve) => setTimeout(resolve, writeDelay));
    await route.fulfill({ json: { status: "success", cells_written: 1, content_version: `v${version}` } });
  });
  const url = `${server.resolvedUrls.local[0]}src/__tests__/fixtures/workbook-browser.html`;
  await page.goto(url);
  await page.waitForFunction(() => window.workbookAPI?.getActiveWorkbook()?.getSheetBySheetId("__excelmanus_loading__"));
  await page.locator('[data-univer-container] [data-em-ribbon="history"]').waitFor({ state: "visible" });
  await page.locator('[data-univer-container] [role="tab"]').filter({ hasText: "公式" }).waitFor({ state: "visible" });
  await page.locator('[data-univer-container] canvas').first().waitFor({ state: "visible" });
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const shellLayout = () => page.evaluate(() => {
    const root = document.querySelector("[data-univer-container]");
    return Object.fromEntries([
      'header[data-u-comp="headerbar"]', '[data-u-comp="formula-bar"]',
      '[role="tablist"][aria-label="ribbon.menu"]', 'footer',
      '[data-em-ribbon="history"]',
    ].map((selector) => {
      const element = root.querySelector(selector);
      if (!element) throw new Error(`Missing native shell: ${selector}`);
      const { x, y, width, height } = element.getBoundingClientRect();
      return [selector, { x, y, width, height }];
    }));
  });
  const loadingLayout = await shellLayout();
  await page.evaluate(() => { window.loadingHeader = document.querySelector('[data-univer-container] header'); });
  assert.equal(await page.locator('[data-workbook-loading="true"]').count(), 1);
  assert.equal(await page.locator(".grid-cols-6").count(), 0);
  await page.evaluate(() => { try { window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("A1").setValue("must not save while loading"); } catch {} });
  assert.equal(await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("A1").getValue()), null);
  assert.equal(writes.length, 0);
  if (process.env.WORKBOOK_SCREENSHOT) await page.screenshot({ path: process.env.WORKBOOK_SCREENSHOT.replace(/\.png$/, ".loading.png") });
  releaseFirstRead();
  const ready = () => page.waitForFunction(() => {
    const sheet = window.workbookAPI?.getActiveWorkbook()?.getActiveSheet();
    return sheet?.getSheet().getSnapshot().columnData?.[0]?.w === 180 && !document.querySelector('[role="alert"]');
  }, { timeout: 60000 });
  await ready().catch(async (error) => {
    console.error(JSON.stringify(await page.evaluate(() => ({ text: document.body.innerText.slice(-1200), counters: window.workbookCounters,
      sheets: window.workbookAPI?.getActiveWorkbook()?.getSheets().map((s) => ({ name: s.getSheetName(), cells: s.getSheet().getSnapshot().cellData, columns: s.getSheet().getSnapshot().columnData })) }))));
    console.error(JSON.stringify({ errors, requests }));
    throw error;
  });
  await page.waitForFunction(() => !document.querySelector('[role="status"]'));
  assert.deepEqual(await shellLayout(), loadingLayout, "Native shell geometry must stay fixed during hydration");
  assert.equal(await page.evaluate(() => window.loadingHeader === document.querySelector('[data-univer-container] header')), true);
  assert.equal(await page.evaluate(() => Boolean(window.workbookAPI.getActiveWorkbook().getSheetBySheetId("__excelmanus_loading__"))), false);
  if (process.env.WORKBOOK_SCREENSHOT) await page.screenshot({ path: process.env.WORKBOOK_SCREENSHOT.replace(/\.png$/, ".ready.png") });
  assert.equal(requests.filter((r) => !r.sheet && r.with_styles === "0").length, 1);
  assert.equal(await page.evaluate(() => window.workbookCounters.created), 1);
  const requestsBeforeRender = requests.length;
  for (let i=0;i<5;i++) await page.getByText("Parent render").click();
  await page.waitForTimeout(400);
  assert.equal(requests.length, requestsBeforeRender);
  assert.equal(await page.evaluate(() => window.workbookCounters.created), 1);

  await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("C5").setValue("local edit"));
  await page.waitForFunction(() => window.excelStore.getState().getContentVersion("book.xlsx", "id:browser-ws") === "v2");
  await page.waitForTimeout(600);
  assert.equal(writes.length, 1);
  assert.equal(cells["5,3"].v, "local edit");
  assert.equal(await page.evaluate(() => window.workbookCounters.created), 1);
  assert.equal(await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("C5").getValue()), "local edit");

  writeDelay = 350;
  await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("D5").setValue("first queued"));
  await page.waitForTimeout(250);
  await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("E5").setValue("second queued"));
  await page.waitForFunction(() => window.excelStore.getState().getContentVersion("book.xlsx", "id:browser-ws") === "v4");
  await page.waitForTimeout(600);
  assert.equal(writes.length, 3);
  assert.equal(cells["5,5"].v, "second queued");
  assert.equal(writes[2].expected_version, "v3");
  assert.equal(await page.evaluate(() => window.workbookCounters.created), 1);
  writeDelay = 0;

  delete cells["5,3"]; delete cells["1,2"]; version++;
  await page.evaluate((v) => window.excelStore.getState().notifyWorkbookChanged("book.xlsx", "id:browser-ws", v), `v${version}`);
  await page.waitForFunction(() => {
    const s=window.workbookAPI.getActiveWorkbook().getActiveSheet();
    return s.getRange("C5").getValue() == null && s.getRange("B1").getCellData()?.f == null;
  });
  assert.equal(await page.evaluate(() => window.workbookCounters.created), 1);
  const countBeforeOther = requests.length;
  await page.evaluate(() => window.excelStore.getState().notifyWorkbookChanged("unrelated.xlsx", "id:browser-ws", "v9"));
  await page.waitForTimeout(400);
  assert.equal(requests.length, countBeforeOther);

  await page.evaluate(() => window.workbookAPI.getActiveWorkbook().setActiveSheet("sheet-Second"));
  await page.waitForFunction(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("A1").getValue() === "original");
  assert(requests.some((r) => r.sheet === "Second"));
  await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().scrollToCell(195, 48));
  await page.waitForFunction(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("A201").getValue() === 201);

  await page.getByText("Toggle visibility").click();
  const countBeforeHidden = requests.length;
  version++;
  await page.evaluate((v) => window.excelStore.getState().notifyWorkbookChanged("book.xlsx", "id:browser-ws", v), `v${version}`);
  await page.waitForTimeout(400);
  assert.equal(requests.length, countBeforeHidden);
  await page.getByText("Toggle visibility").click();
  await page.waitForTimeout(700);
  assert(requests.length > countBeforeHidden);
  assert.equal(await page.evaluate(() => window.workbookCounters.created), 1);

  // A stale local edit remains visible and stops the file's write queue.
  await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().scrollToCell(0, 0));
  await ready();
  await page.waitForTimeout(400);
  version++;
  await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("F5").setValue("unsaved"));
  await page.getByRole("alert").waitFor();
  assert.equal(await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("F5").getValue()), "unsaved");
  assert.equal(cells["5,6"], undefined);
  const writesAtConflict = writes.length;
  await page.evaluate(() => { try { window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("G5").setValue("must not save"); } catch {} });
  await page.waitForTimeout(400);
  assert.equal(writes.length, writesAtConflict);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "重新加载" }).click();
  await page.waitForFunction(() => !document.querySelector('[role="alert"]') && window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("F5").getValue() == null);
  assert.equal(await page.evaluate(() => window.workbookCounters.created), 1);

  // Sheet deletion is the exceptional structural rebuild path.
  sheets = ["Main"]; version++;
  await page.evaluate((v) => window.excelStore.getState().notifyWorkbookChanged("book.xlsx", "id:browser-ws", v), `v${version}`);
  await page.waitForFunction(() => window.workbookAPI.getActiveWorkbook().getSheets().length === 1);
  await ready();
  assert.equal(await page.evaluate(() => window.workbookCounters.created), 2);
  await page.getByText("Switch file").click();
  await page.waitForTimeout(100);
  await page.getByText("Switch file").click();
  await page.waitForFunction(() => window.workbookCounters.created === 4);
  await ready();
  await page.waitForTimeout(500);
  assert.equal(await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("A1").getValue()), "original");
  assert.equal(await page.locator("[data-univer-container]").isVisible(), true);
  if (process.env.WORKBOOK_SCREENSHOT) await page.screenshot({ path: process.env.WORKBOOK_SCREENSHOT });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: "passed", scenarios: ["native loading shell", "stable shell geometry", "no edits before data", "first paint", "stable parent render", "local edit", "queued edits", "remote delete", "file scope", "sheet switch", "viewport boundary", "hidden refresh", "conflict retention", "conflict reload", "sheet deletion", "late file response"], requests: requests.length, writes: writes.length, nonStructuralWorkbookCreates: 1, afterStructuralChange: 2, afterFileSwitch: 4, loadingLayout }));
} finally {
  await browser?.close();
  await server.close();
}
