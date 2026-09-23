// Uses the real workbook panes and Univer. Supply Playwright through NODE_PATH
// and WORKBOOK_BROWSER_CHANNEL=msedge when using the system browser on Windows.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { mkdir } from "node:fs/promises";
import assert from "node:assert/strict";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const { createServer } = await import("vite");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const output = path.resolve(root, "../output/playwright/multi-workbook");
await mkdir(output, { recursive: true });
const server = await createServer({ root, configFile: false, cacheDir: path.join(root, "node_modules/.vite-multi-workbook"),
  resolve: { alias: [
    { find: "@/lib/univer-modules", replacement: path.join(root, "src/__tests__/fixtures/multi-workbook-modules-browser.ts") },
    { find: "@", replacement: path.join(root, "src") },
  ] }, esbuild: { jsx: "automatic" },
  optimizeDeps: { include: ["react", "react-dom/client", "react-dom", "react/jsx-dev-runtime", "next/dynamic", "zustand", "zustand/middleware", "lucide-react", "@univerjs/core", "@univerjs/presets", "@univerjs/preset-sheets-core", "@univerjs/preset-sheets-core/locales/zh-CN", "@univerjs/sheets-ui"] },
  define: { "process.env.NODE_ENV": JSON.stringify("development") }, server: { host: "127.0.0.1", port: 0 },
});
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ headless: true, ...(process.env.WORKBOOK_BROWSER_CHANNEL ? { channel: process.env.WORKBOOK_BROWSER_CHANNEL } : {}) });
  const page = await browser.newPage({ viewport: { width: 1560, height: 960 } });
  const errors = [], writes = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") console.error(message.text()); });
  const versions = new Map();
  const values = new Map();
  await page.route("**/api/v1/files/excel/compare?**", (route) => route.fulfill({ json: { file_a: { sheets: ["明细"] }, file_b: { sheets: ["明细"] }, relationships: { shared_columns: [] } } }));
  const read = (file) => {
    if (!versions.has(file)) { versions.set(file, 1); values.set(file, { "1,1": { t: "s", v: file, cached: "yes" }, "2,1": { t: "n", v: 10, cached: "yes" } }); }
    return `v${versions.get(file)}`;
  };
  await page.route("**/api/v1/files/excel/view?**", async (route) => {
    const params = new URL(route.request().url()).searchParams;
    const file = params.get("path").replace(/^\.\//, ""), version = read(file), sheet = params.get("sheet") || "明细";
    await route.fulfill({ json: { file: { workspaceKey: "id:multi-ws", relative: file }, content_version: version, active_sheet: sheet,
      with_styles: params.get("with_styles") === "1", sheets: ["明细", "汇总"].map((name) => ({ name, sheet_id: name, used: { rows: 60, cols: 12 } })),
      windows: [{ sheet, rect: { r0: 1, c0: 1, r1: 200, c1: 50 }, cells: values.get(file) }],
      coverage: { loaded: [{ sheet, r0: 1, c0: 1, r1: 200, c1: 50 }], unloaded: [] } } });
  });
  await page.route("**/api/v1/files/excel/write", async (route) => {
    const body = route.request().postDataJSON(), file = body.path.replace(/^\.\//, "");
    writes.push(body);
    assert.equal(body.expected_version, read(file));
    for (const operation of body.operations) for (const cell of operation.cells ?? []) {
      const [, col, row] = cell.cell.match(/([A-Z]+)(\d+)/);
      const index = [...col].reduce((sum, letter) => sum * 26 + letter.charCodeAt(0) - 64, 0);
      values.get(file)[`${row},${index}`] = { t: typeof cell.value === "number" ? "n" : "s", v: cell.value, cached: "yes" };
    }
    versions.set(file, versions.get(file) + 1);
    await route.fulfill({ json: { status: "success", cells_written: 1, content_version: read(file) } });
  });
  await page.goto(`${server.resolvedUrls.local[0]}src/__tests__/fixtures/multi-workbook-browser.html`);
  const ready = (count) => page.waitForFunction((count) => Object.values(window.workbookConversations?.getState().views ?? {}).filter((view) => view.status === "ready").length >= count, count);
  const painted = async () => {
    await page.waitForFunction(() => [...document.querySelectorAll("[data-workbook-pane]")].every((pane) => pane.querySelector("canvas")?.getBoundingClientRect().height > 0));
    // Let the canvas renderer settle after container resize and asynchronous hydration.
    await page.waitForTimeout(500);
  };
  await ready(3);
  assert.equal(await page.locator("[data-workbook-pane]").count(), 3);
  await painted();
  await page.screenshot({ path: path.join(output, "desktop.png") });
  const context = await page.evaluate(() => window.prepareWorkbookRequest("汇总这些表", "multi-browser"));
  assert.equal(context.sheetContext.path, "库存.xlsx");
  assert.deepEqual(context.sheetContexts.map((view) => view.path), ["销售.xlsx", "预算.xlsx", "库存.xlsx"]);
  // All three independent facades save against their own file/version.
  for (const [index, name] of ["销售.xlsx", "预算.xlsx", "库存.xlsx"].entries()) {
    await page.getByRole("tab").filter({ hasText: name }).click();
    await page.evaluate(({ name, value }) => {
      const api = window.multiWorkbookAPIs.find((api) => api.getActiveWorkbook()?.getActiveSheet()?.getRange("A1").getValue() === name);
      api.getActiveWorkbook().getActiveSheet().getRange("B3").setValue(value);
    }, { name, value: 100 + index });
    await page.waitForFunction((name) => window.excelStore.getState().getContentVersion(name, "id:multi-ws") === "v2", name);
  }
  assert.deepEqual(writes.map((write) => write.path.replace(/^\.\//, "")).sort(), ["库存.xlsx", "预算.xlsx", "销售.xlsx"].sort());
  // An unfocused engine must ignore global undo dispatches.
  const inactiveUndo = await page.evaluate(async () => {
    const api = window.multiWorkbookAPIs.find((api) => api.getActiveWorkbook()?.getActiveSheet()?.getRange("A1").getValue() === "销售.xlsx");
    await api.executeCommand("univer.command.undo");
    return api.getActiveWorkbook().getActiveSheet().getRange("B3").getValue();
  });
  assert.equal(inactiveUndo, 100);
  await page.locator('[data-workbook-pane="库存.xlsx"] canvas[id^="univer-sheet-main-canvas_"]').click({ position: { x: 130, y: 75 } });
  await page.keyboard.press("Control+z");
  await page.waitForFunction(() => window.excelStore.getState().getContentVersion("库存.xlsx", "id:multi-ws") === "v3");
  assert.equal(writes.length, 4);
  assert.equal(writes[3].path.replace(/^\.\//, ""), "库存.xlsx");
  await page.keyboard.press("Control+y");
  await page.waitForFunction(() => window.excelStore.getState().getContentVersion("库存.xlsx", "id:multi-ws") === "v4");
  assert.equal(writes.length, 5);
  await page.getByRole("button", { name: "视图", exact: true }).click();
  await page.getByRole("menuitemcheckbox", { name: /同步定位/ }).click();
  await page.keyboard.press("Escape");
  await page.evaluate(() => {
    const api = window.multiWorkbookAPIs.find((api) => api.getActiveWorkbook()?.getActiveSheet()?.getRange("A1").getValue() === "库存.xlsx");
    const sheet = api.getActiveWorkbook().getActiveSheet(); sheet.setActiveRange(sheet.getRange("C4:D6"));
  });
  await page.waitForFunction(() => Object.values(window.workbookConversations.getState().views).filter((view) => view.range === "C4:D6").length === 3);
  assert.equal(writes.length, 5, "linked navigation must not save any file");
  assert.equal(await page.evaluate(() => window.excelStore.getState().activeFilePath), "库存.xlsx");
  await page.locator('[data-workbook-workspace] button[title="更多"]').click();
  await page.getByRole("menuitem", { name: /与主表对比/ }).click();
  await page.waitForFunction(() => document.querySelector('[data-workspace-surface="compare"]'));
  await page.getByRole("button", { name: "返回表格", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[data-workspace-surface="excel"]'));
  assert.equal(await page.evaluate(() => window.excelStore.getState().fullViewPath), "销售.xlsx");
  await page.getByRole("button", { name: "视图", exact: true }).click();
  await page.getByRole("menuitem", { name: "并排对话", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[data-workbook-layout="split"]'));
  await page.getByRole("button", { name: "视图", exact: true }).click();
  await page.getByRole("menuitem", { name: "展开表格", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('[data-workbook-layout="embedded"]'));
  await page.locator('[role="tab"]').filter({ hasText: "库存.xlsx" }).click();
  await page.locator('[data-workbook-workspace] button[title="更多"]').click();
  await page.getByRole("menuitem", { name: "设为主表", exact: true }).click();
  assert.equal(await page.evaluate(() => window.excelStore.getState().fullViewPath), "库存.xlsx");
  await page.evaluate(() => window.excelStore.getState().openFullView("第四表.xlsx", "明细"));
  await ready(4);
  assert.equal(await page.locator("[data-workbook-pane]").count(), 3);
  assert.equal(await page.evaluate(() => window.excelStore.getState().fullViewPath), "库存.xlsx");
  await page.setViewportSize({ width: 1100, height: 900 });
  await painted();
  assert.equal(await page.evaluate(() => [...document.querySelectorAll("[data-workbook-pane]")].every((pane) => {
    const footer = pane.querySelector("footer");
    return footer && footer.getBoundingClientRect().bottom <= pane.getBoundingClientRect().bottom + 1;
  })), true, "stacked panes must retain their worksheet tabs");
  await page.screenshot({ path: path.join(output, "stacked.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.querySelectorAll("[data-workbook-pane]").length === 1);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= 390), true);
  await painted();
  await page.screenshot({ path: path.join(output, "mobile.png") });
  await page.locator('[role="tab"]').filter({ hasText: "库存.xlsx" }).click();
  await page.waitForFunction(() => document.querySelector("[data-workbook-pane]")?.getAttribute("data-workbook-pane") === "库存.xlsx");
  await page.locator('[data-workbook-workspace] button[title="更多"]').click();
  await page.getByRole("menuitem", { name: /生成合并方案/ }).click();
  await page.getByRole("dialog").waitFor();
  assert.equal(await page.getByRole("dialog").getByText("主表：库存.xlsx", { exact: true }).count(), 1);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.locator('[data-workbook-workspace] button[title="更多"]').click();
  await page.getByRole("menuitem", { name: /引用同屏表格/ }).click();
  await page.waitForFunction(() => document.querySelector('[data-workspace-surface="chat"]'));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ result: "passed", saves: writes.length, scenarios: ["three independent panes", "primary/focus context", "isolated saves", "keyboard undo/redo isolation", "linked selection", "compare return", "chat layout", "promote", "fourth workbook", "responsive layout", "mobile merge/reference"], screenshots: output }));
} finally { await browser?.close(); await server.close(); }
