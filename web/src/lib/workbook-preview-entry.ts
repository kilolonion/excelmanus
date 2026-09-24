import { createUniver, LocaleType } from "@univerjs/presets";
import { UniverSheetsCorePreset } from "@univerjs/preset-sheets-core";
import zhCN from "@univerjs/preset-sheets-core/locales/zh-CN";
import { mergeWorksheetSnapshotWithDefault } from "@univerjs/core";
import "@univerjs/preset-sheets-core/lib/index.css";
import { observationToUniver, type WorkbookObservation } from "./workbook-observation";

const frame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

async function renderObservation(view: WorkbookObservation) {
  const region = view.regions[0];
  const container = document.getElementById("workbook")!;
  const { univerAPI } = createUniver({ locale: LocaleType.ZH_CN, locales: { [LocaleType.ZH_CN]: zhCN },
    presets: [UniverSheetsCorePreset({ container, header: false, toolbar: false, formulaBar: false,
      footer: false, contextMenu: false, disableAutoFocus: true,
      formula: { initialFormulaComputing: 2 } })] });
  const data = observationToUniver({ ...view, sheets: view.sheets.filter((s) => s.name === region.sheet) }, "preview");
  const sheets = data.sheets as Record<string, Parameters<typeof mergeWorksheetSnapshotWithDefault>[0]>;
  for (const [id, sheet] of Object.entries(sheets)) sheets[id] = mergeWorksheetSnapshotWithDefault(sheet);
  const rendered = new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Workbook render did not become ready")), 15000);
    const subscription = univerAPI.addEvent(univerAPI.Event.LifeCycleChanged, ({ stage }) => {
      if (stage >= 3) { clearTimeout(timer); subscription.dispose(); resolve(); }
    });
  });
  univerAPI.createWorkbook(data);
  univerAPI.getActiveWorkbook()?.setEditable(false);
  await rendered;
  await document.fonts.ready;
  for (let i = 0; i < 4; i++) await frame();
  const sheet = univerAPI.getActiveWorkbook()!.getActiveSheet();
  sheet.getRange(region.rect.r1 + 1, region.rect.c1 + 1).activate();
  sheet.scrollToCell(region.rect.r0 - 1, region.rect.c0 - 1, 0);
  for (let i = 0; i < 4; i++) await frame();
  const rect = (row: number, col: number) => {
    const b = sheet.getRange(row - 1, col - 1).getCellRect();
    return { x: b.x, y: b.y, width: b.width, height: b.height };
  };
  const first = rect(region.rect.r0, region.rect.c0);
  const last = rect(region.rect.r1, region.rect.c1);
  return { ready: true, observation_id: view.observation_id, content_version: view.content_version,
    locale: "zh-CN", dpi: 96, zoom: 1, device_scale: 1, coordinate_space: "viewport_css_px",
    calculation: "saved_cache_only; initial_formula_computing_disabled",
    clip: { x: Math.max(0, first.x), y: Math.max(0, first.y), width: Math.max(1, last.x + last.width - first.x), height: Math.max(1, last.y + last.height - first.y) },
    displayed_cells: Object.fromEntries(Object.entries(region.cells).map(([key, cell]) => {
      const [row, col] = key.split(",").map(Number);
      return [key, { text: sheet.getRange(row-1, col-1).getDisplayValue(),
        status: cell.f && cell.cached === "no" ? "uncalculated" : "rendered", locale: "zh-CN" }];
    })),
    columns: (region.geometry?.columns || []).map((d) => ({ index: d.index, ...rect(region.rect.r0, d.index) })),
    rows: (region.geometry?.rows || []).map((d) => ({ index: d.index, ...rect(d.index, region.rect.c0) })),
    fonts: [...new Set(Object.values(region.cells).map((c) => String(c.s?.ff || "Calibri")))].map((name) => ({ requested: name, loaded_check: document.fonts.check(`12px "${name}"`), substitution_measured: false })),
  };
}

Object.assign(window, { renderObservation });
