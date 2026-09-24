// Run with Node; playwright may be supplied through NODE_PATH. Uses the real
// React component and installed Univer, with a deterministic HTTP test server.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import assert from "node:assert/strict";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const { createServer } = await import("vite");
const { default: tailwind } = await import("@tailwindcss/postcss");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const server = await createServer({
  root, configFile: false, css: { postcss: { plugins: [tailwind({ base: root })] } }, cacheDir: path.join(root, "node_modules/.vite-workbook-loading"),
  resolve: { alias: [
    { find: "@/lib/univer-modules", replacement: path.join(root, "src/__tests__/fixtures/univer-modules-browser.ts") },
    { find: "@", replacement: path.join(root, "src") },
  ] },
  esbuild: { jsx: "automatic" },
  optimizeDeps: { holdUntilCrawlEnd: false, noDiscovery: true, include: ["react", "react-dom/client", "react-dom", "react/jsx-dev-runtime", "zustand", "zustand/middleware", "lucide-react", "clsx", "tailwind-merge", "@univerjs/core", "@univerjs/presets", "@univerjs/preset-sheets-core", "@univerjs/preset-sheets-core/locales/zh-CN", "@univerjs/sheets-ui"] },
  define: { "process.env.NODE_ENV": JSON.stringify("development") },
  server: { host: "127.0.0.1", port: 0, watch: { ignored: ["**/.next/**", "**/.build/**"] } },
});
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ headless: true, ...(process.env.WORKBOOK_BROWSER_CHANNEL ? { channel: process.env.WORKBOOK_BROWSER_CHANNEL } : {}) });
  const page = await browser.newPage({ viewport: { width: Number(process.env.WORKBOOK_TEST_WIDTH || 1280), height: 800 } });
  if (process.env.WORKBOOK_DEBUG) {
    page.on("request", (r) => console.log("request", r.url()));
    page.on("requestfinished", (r) => console.log("finished", r.url()));
    page.on("requestfailed", (r) => console.log("failed", r.url(), r.failure()));
  }
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
  await page.route("**/api/v1/workbooks/observe?**", async (route) => {
    const params = new URL(route.request().url()).searchParams;
    requests.push(Object.fromEntries(params));
    const sheet = params.get("sheet") || "Main";
    const styles = (params.get("facets") || "").split(",").includes("presentation");
    if (!sheets.includes(sheet)) return route.fulfill({ status: 404, json: { code: "SHEET_NOT_FOUND", error: "missing sheet" } });
    const expected = params.get("expected_version");
    if (expected && expected !== `v${version}`) return route.fulfill({ status: 409, json: { code: "STALE_VIEW", error: "stale", content_version: `v${version}` } });
    const [first, last] = (params.get("range") || "A1:AX200").split(":");
    const [r0, c0] = parseCell(first), [r1, c1] = parseCell(last);
    const within = Object.fromEntries(Object.entries(cells).filter(([key]) => { const [r,c] = key.split(",").map(Number); return r >= r0 && r <= r1 && c >= c0 && c <= c1; }).map(([key, cell]) => [key, { ...cell, ...(styles ? { s: { bl: 1 } } : {}) }]));
    const otherFile = params.get("path").endsWith("other.xlsx");
    if (otherFile && within["1,1"]) within["1,1"].v = "other file";
    const data = {
      file: { workspaceKey: "id:browser-ws", relative: params.get("path").replace(/^\.\//, "") }, content_version: `v${version}`, schema_version: "workbook/2", active_sheet: "Main", request: { facets: (params.get("facets") || "").split(",") },
      sheets: sheets.map((name) => ({ name, sheet_id: name, used: { rows: 600, cols: 60 } })),
      regions: [{ sheet, rect: { r0,c0,r1,c1 }, cells: within, ...(styles ? { merges: r0 <= 2 && r1 >= 2 && c0 === 1 ? [{ min_row: 2,min_col: 1,max_row: 2,max_col: 2 }] : [], geometry: {
        columns: Array.from({length:c1-c0+1},(_,i)=>({index:c0+i,native:c0+i===1?24:8.43,pixels:c0+i===1?173:64,hidden:false})),
        rows: Array.from({length:r1-r0+1},(_,i)=>({index:r0+i,native:r0+i===1?30:15,pixels:r0+i===1?40:20,hidden:false})), width_px:0,height_px:0 } } : {}) }],
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
  await page.route("**/api/v1/workbooks/changes", async (route) => {
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
  await page.goto(url, { waitUntil: "domcontentloaded" });
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
    return sheet?.getSheet().getSnapshot().columnData?.[0]?.w === 173 && !document.querySelector('[role="alert"]');
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
  assert.equal(requests.filter((r) => !r.sheet && r.facets === "data,geometry").length, 1);
  assert.equal(await page.evaluate(() => window.workbookCounters.created), 1);
  if (!process.env.WORKBOOK_INTERACTIONS_ONLY) {
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

  const beforeRemoteRefresh = await page.evaluate(() => window.workbookCounters.created);
  delete cells["5,3"]; delete cells["1,2"]; version++;
  await page.evaluate((v) => window.excelStore.getState().notifyWorkbookChanged("book.xlsx", "id:browser-ws", v), `v${version}`);
  await page.waitForFunction(() => {
    const s=window.workbookAPI.getActiveWorkbook().getActiveSheet();
    return s.getRange("C5").getValue() == null && s.getRange("B1").getCellData()?.f == null;
  });
  // An external version replaces the snapshot and its undo history; local saves above do not.
  assert.equal(await page.evaluate(() => window.workbookCounters.created), beforeRemoteRefresh + 1);
  await ready();
  await page.waitForTimeout(600); // Finish the styled window and adjacent-page prefetch before measuring idle reads.
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
  assert.equal(await page.evaluate(() => window.workbookCounters.created), beforeRemoteRefresh + 2);

  // A stale local edit remains visible and stops the file's write queue.
  await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().scrollToCell(0, 0));
  await ready();
  await page.waitForTimeout(400);
  version++;
  await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("F5").setValue("unsaved"));
  await page.getByRole("alert").waitFor().catch(async (error) => {
    console.error(JSON.stringify({ phase:"conflict", version, writes:writes.slice(-4), requests:requests.slice(-6), state:await page.evaluate(() => ({text:document.body.innerText.slice(-900),sheet:window.workbookAPI.getActiveWorkbook().getActiveSheet().getSheetName(),value:window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("F5").getValue()})) }));
    throw error;
  });
  assert.equal(await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("F5").getValue()), "unsaved");
  assert.equal(cells["5,6"], undefined);
  const writesAtConflict = writes.length;
  await page.evaluate(() => { try { window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("G5").setValue("must not save"); } catch {} });
  await page.waitForTimeout(400);
  assert.equal(writes.length, writesAtConflict);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "重新加载" }).click();
  await page.waitForFunction(() => !document.querySelector('[role="alert"]') && window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("F5").getValue() == null);
  assert.equal(await page.evaluate(() => window.workbookCounters.created), beforeRemoteRefresh + 3);

  // Sheet deletion also replaces the snapshot and drops obsolete sheets.
  sheets = ["Main"]; version++;
  await page.evaluate((v) => window.excelStore.getState().notifyWorkbookChanged("book.xlsx", "id:browser-ws", v), `v${version}`);
  await page.waitForFunction(() => window.workbookAPI.getActiveWorkbook().getSheets().length === 1);
  await ready();
  assert.equal(await page.evaluate(() => window.workbookCounters.created), beforeRemoteRefresh + 4);
  await page.getByText("Switch file").click();
  await page.waitForTimeout(100);
  await page.getByText("Switch file").click();
  await page.waitForFunction((expected) => window.workbookCounters.created === expected, beforeRemoteRefresh + 6);
  await ready();
  await page.waitForTimeout(500);
  assert.equal(await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("A1").getValue()), "original");
  assert.equal(await page.locator("[data-univer-container]").isVisible(), true);
  }
  // Exercise range presentation and the actual selection -> HTTP answer path.
  const interactionVersion = `v${version}`;
  const writesBeforeInteraction = writes.length;
  await page.evaluate((v) => {
    const sheet = window.workbookAPI.getActiveWorkbook().getActiveSheet();
    window.beforeHighlightCells = JSON.stringify(sheet.getSheet().getSnapshot().cellData);
    window.overlayStats = { created: 0, disposed: 0 };
    const proto = Object.getPrototypeOf(sheet);
    const highlight = proto.highlightRanges;
    proto.highlightRanges = function (...args) {
      window.overlayStats.created++;
      const overlay = highlight.apply(this, args);
      return { dispose: () => { window.overlayStats.disposed++; overlay.dispose(); } };
    };
    window.interactionTarget = { file_path: "book.xlsx", workspace_id: "browser-ws", sheet: "Main", ranges: ["A1:B2"], content_version: v };
    window.showWorkbookPresentation({ kind: "workbook_presentation", target: window.interactionTarget, stage: "planned", summary: "准备整理这些单元格" }, "browser");
  }, interactionVersion);
  await page.getByText("准备修改的区域", { exact: true }).waitFor();
  await page.waitForFunction(() => window.overlayStats.created > 0);
  // The breathing window updates live native controls, without rebuilding marks.
  const focusSnapshot = () => page.evaluate(() => [...window.workbookMarks.getShapeMap().values()].map((shape) => ({
    style: shape.selection.style,
    rendered: { stroke: shape.control?.currentStyle.stroke, fill: shape.control?.currentStyle.fill },
  })));
  const firstFocus = await focusSnapshot();
  assert.equal(firstFocus.length, 2);
  await page.waitForTimeout(400);
  const breathingFocus = await focusSnapshot();
  assert.notDeepEqual(breathingFocus, firstFocus);
  breathingFocus.forEach(({ style, rendered }) => assert.deepEqual(rendered, { stroke: style.stroke, fill: style.fill }));
  assert.equal(await page.evaluate(() => window.overlayStats.created), 2);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.waitForFunction(() => [...window.workbookMarks.getShapeMap().values()].some((shape) => shape.selection.style.stroke === "rgba(245,158,11,0.790)"));
  const steadyFocus = await focusSnapshot();
  await page.waitForTimeout(200);
  assert.deepEqual(await focusSnapshot(), steadyFocus);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().zoom(1.25));
  await page.waitForTimeout(150);
  const zoomedFocus = await focusSnapshot();
  assert.equal(zoomedFocus.length, 2);
  zoomedFocus.forEach(({ style, rendered }) => assert.deepEqual(rendered, { stroke: style.stroke, fill: style.fill }));
  await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().zoom(1));
  if (process.env.WORKBOOK_SCREENSHOT) {
    const screenshot = path.parse(process.env.WORKBOOK_SCREENSHOT);
    await page.screenshot({ path: path.join(screenshot.dir, `${screenshot.name}-planned${screenshot.ext}`) });
  }
  assert.equal(await page.evaluate(() => JSON.stringify(window.workbookAPI.getActiveWorkbook().getActiveSheet().getSheet().getSnapshot().cellData) === window.beforeHighlightCells), true);
  const answers = [];
  await page.route("**/api/v1/chat/browser/answer", async (route) => {
    answers.push(route.request().postDataJSON());
    await route.fulfill({ json: { status: "answered" } });
  });
  await page.evaluate(() => {
    window.chatStore.getState().setPendingQuestion({ id: "browser-question", header: "范围", text: "请选择范围", options: [], multiSelect: false, selection: window.interactionTarget, sessionId: "browser" });
    window.openWorkbookQuestion("browser-question", window.interactionTarget, "browser");
  });
  await page.getByText("请选区并确认", { exact: true }).waitFor();
  await page.evaluate(() => {
    const sheet = window.workbookAPI.getActiveWorkbook().getActiveSheet();
    sheet.setActiveRange(sheet.getRange("A1:B2"));
    try { sheet.getRange("A1").setValue("must not write during question"); } catch {}
  });
  await page.waitForFunction(() => window.excelStore.getState().draftRange?.range === "A1:B2");
  // Univer reports the intentional blocked edit in its own modal.
  const protectedRangeDialog = page.getByRole("button", { name: "确定", exact: true });
  if (await protectedRangeDialog.isVisible()) await protectedRangeDialog.click();
  assert.equal(await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("A1").getValue()), "original");
  assert.equal(answers.length, 0);
  await page.getByRole("button", { name: "确认此区域" }).click({ timeout: 5000 }).catch(async (error) => {
    console.error(JSON.stringify(await page.evaluate(() => ({ text: document.body.innerText.slice(-1800),
      pending: window.chatStore.getState().pendingQuestion, mode: window.excelStore.getState().selectionMode,
      draft: window.excelStore.getState().draftRange }))));
    if (process.env.WORKBOOK_SCREENSHOT) await page.screenshot({ path: process.env.WORKBOOK_SCREENSHOT });
    throw error;
  });
  await page.waitForFunction(() => !window.chatStore.getState().pendingQuestion);
  assert.equal(answers.length, 1);
  assert.deepEqual(answers[0].selection.ranges, ["A1:B2"]);
  assert.equal(answers[0].selection.content_version, interactionVersion);
  assert.equal(writes.length, writesBeforeInteraction);
  assert.ok(await page.evaluate(() => window.overlayStats.disposed > 0));
  const overlaysBeforeChange = await page.evaluate(() => window.overlayStats.created);
  version++;
  cells["3,8"] = { t: "s", v: "agent change", cached: "yes" };
  await page.evaluate((v) => {
    window.excelStore.getState().notifyWorkbookChanged("book.xlsx", "id:browser-ws", v);
    window.showWorkbookPresentation({ kind: "workbook_presentation", target: { ...window.interactionTarget, ranges: ["H3"], content_version: v }, stage: "changed", summary: "已更新此单元格" }, "browser");
  }, `v${version}`);
  await page.getByText("已修改的区域", { exact: true }).waitFor();
  await page.waitForFunction((count) => window.overlayStats.created > count, overlaysBeforeChange);
  assert.equal(await page.evaluate(() => window.workbookAPI.getActiveWorkbook().getActiveSheet().getRange("H3").getValue()), "agent change");
  assert.equal(writes.length, writesBeforeInteraction);
  if (process.env.WORKBOOK_SCREENSHOT) await page.screenshot({ path: process.env.WORKBOOK_SCREENSHOT });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: "passed", mode: process.env.WORKBOOK_INTERACTIONS_ONLY ? "interactions" : "all", scenarios: [...(process.env.WORKBOOK_INTERACTIONS_ONLY ? [] : ["stable parent render", "local edit", "queued edits", "remote delete", "file scope", "sheet switch", "viewport boundary", "hidden refresh", "conflict retention", "conflict reload", "sheet deletion", "late file response"]), "native loading shell", "stable shell geometry", "no edits before data", "first paint", "breathing without mark recreation", "live reduced motion", "zoomed focus", "planned overlay without style writes", "selection confirmation HTTP", "selection read-only", "overlay cleanup"], requests: requests.length, writes: writes.length, loadingLayout }));
} finally {
  await browser?.close();
  await Promise.race([server.close(), new Promise((resolve) => setTimeout(resolve, 3000))]);
}
