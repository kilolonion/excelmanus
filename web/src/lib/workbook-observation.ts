import type { WorkbookObservationWire } from "./workbook-contracts.generated";
import type { WorkspaceFileRef } from "@/lib/workspace-file-ref";

export type CellType = "n" | "s" | "b" | "d" | "e" | "z";
export type CachedState = "yes" | "no" | "unknown";

export interface ViewCell {
  t: CellType;
  v: unknown;
  serial_value?: number;
  f?: string;
  cached: CachedState;
  e?: string;
  s?: Record<string, unknown>;
}

export interface ViewRect {
  r0: number;
  c0: number;
  r1: number;
  c1: number;
  sheet?: string;
}

export interface AxisSize { index: number; native: number; pixels: number; hidden: boolean; source?: string; }

export interface WorkbookRegion {
  sheet: string;
  rect: ViewRect;
  cells: Record<string, ViewCell>;
  coverage?: Record<string, { status: string }>;
  merge_anchors?: Record<string, { value: unknown; formula?: string | null; s?: Record<string, unknown> }>;
  merges?: { min_row: number; min_col: number; max_row: number; max_col: number }[];
  objects?: WorkbookDrawingObject[];
  cell_images?: WorkbookDrawingObject[];
  geometry?: {
    columns: AxisSize[];
    rows: AxisSize[];
    width_px: number;
    height_px: number;
    defaults?: { font?: { name?: string; size_pt?: number } };
  };
}

export interface WorkbookDrawingObject {
  id?: string;
  kind: "image" | "chart" | string;
  index?: number;
  target_cell?: string | null;
  bounds?: { x?: number; y?: number; width?: number; height?: number; unit?: string };
  source_size_px?: { width?: number; height?: number };
  format?: string;
  chart_type?: string;
  chart_data?: {
    title?: string | null;
    series?: Array<{ index?: number; name?: string; categories?: unknown[]; values?: unknown[] }>;
  };
  asset?: { kind?: string; media_type?: string; index?: number; url?: string };
}

export interface WorkbookObservation {
  file: {
    workspaceKey: string;
    relative: string;
    observedVersion?: string;
  };
  content_version: string;
  snapshot_id?: string;
  active_sheet?: string;
  request?: { facets: string[] };
  sheets: { name: string; sheet_id: string; used: { rows: number; cols: number }; available?: {charts?: number; images?: number; conditional_rules?: number}; state?: string; freeze_panes?: string; defaults?: { column_width?: { pixels?: number }; row_height?: { pixels?: number }; font?: { name?: string; size_pt?: number } } }[];
  schema_version?: WorkbookObservationWire["schema_version"];
  observation_id?: string;
  regions: WorkbookRegion[];
  coverage: {
    loaded: ViewRect[];
    unloaded: ViewRect[];
    truncated_reason?: string | null;
  };
}

function colIndexToLetter(index: number): string {
  let result = "";
  let n = index;
  while (n >= 0) {
    result = String.fromCharCode((n % 26) + 65) + result;
    n = Math.floor(n / 26) - 1;
  }
  return result;
}

export function letterToColIndex(letter: string): number {
  let n = 0;
  for (const ch of letter.toUpperCase()) {
    n = n * 26 + (ch.charCodeAt(0) - 64);
  }
  return n - 1;
}

function cellsWithAnchors(win: WorkbookRegion): Record<string, ViewCell> {
  const cells = { ...win.cells };
  for (const [address, anchor] of Object.entries(win.merge_anchors || {})) {
    const m = /^([A-Z]+)([0-9]+)$/i.exec(address);
    if (m) {
      const key = `${m[2]},${letterToColIndex(m[1]) + 1}`;
      cells[key] ??= { t: "s", v: anchor.formula ? null : anchor.value, f: anchor.formula || undefined, s: anchor.s, cached: "unknown" };
    }
  }
  return cells;
}

export function cellToUniver(cell: ViewCell): Record<string, unknown> {
  const data: Record<string, unknown> = {};
  if (cell.f) {
    data.f = cell.f.startsWith("=") ? cell.f : `=${cell.f}`;
  }
  if (cell.v !== null && cell.v !== undefined) {
    data.v = cell.serial_value ?? cell.v;
  } else if (!cell.f) {
    data.v = null;
  }
  if (cell.f && cell.cached !== "yes" && cell.v == null) data.v = "⟨未计算⟩";
  if (cell.s) data.s = cell.s;
  if (cell.t === "e" && cell.e) {
    data.v = cell.e;
  }
  return data;
}

/** Sparse replacement, including tombstones for cells deleted since the last view. */
export function windowCellPatch(
  win: WorkbookRegion,
  previous: Record<number, Record<number, unknown> | undefined> = {},
  previousStyles: Record<string, unknown> = {},
): Record<number, Record<number, unknown>> {
  const patch: Record<number, Record<number, unknown>> = {};
  for (const [r, row] of Object.entries(previous)) {
    if (+r + 1 < win.rect.r0 || +r + 1 > win.rect.r1) continue;
    for (const c of Object.keys(row || {})) {
      if (+c + 1 < win.rect.c0 || +c + 1 > win.rect.c1) continue;
      (patch[+r] ??= {})[+c] = null;
    }
  }
  for (const [key, cell] of Object.entries(cellsWithAnchors(win))) {
    const [r, c] = key.split(",").map(Number);
    const data: Record<string, unknown> = { v: null, f: null, s: null, p: null, t: null, custom: null, ...cellToUniver(cell) };
    const oldCell = previous[r - 1]?.[c - 1] as { s?: unknown } | null | undefined;
    const oldStyle = typeof oldCell?.s === "string" ? previousStyles[oldCell.s] : oldCell?.s;
    if (cell.s && oldStyle && typeof oldStyle === "object") {
      // Univer merges style objects (including border sides). A snapshot is a
      // replacement: explicitly remove properties absent from the restored file.
      const style: Record<string, unknown> = Object.fromEntries(Object.keys(oldStyle).map((key) => [key, null]));
      Object.assign(style, cell.s);
      const oldBorder = (oldStyle as Record<string, unknown>).bd;
      if (cell.s.bd && typeof cell.s.bd === "object" && oldBorder && typeof oldBorder === "object") {
        style.bd = { ...Object.fromEntries(Object.keys(oldBorder).map((key) => [key, null])), ...cell.s.bd };
      }
      data.s = style;
    }
    (patch[r - 1] ??= {})[c - 1] = data;
  }
  return patch;
}

export function observationToUniver(
  view: WorkbookObservation,
  workbookId: string,
): Record<string, unknown> {
  const sheetMap: Record<string, unknown> = {};
  const sheetOrder: string[] = [];

  for (const meta of view.sheets) {
    const sheetId = `sheet-${meta.name}`;
    sheetOrder.push(sheetId);
    const windows = view.regions.filter((item) => item.sheet === meta.name);
    const cellData: Record<number, Record<number, unknown>> = {};
    let maxRow = Math.max(meta.used.rows, 1);
    let maxCol = Math.max(meta.used.cols, 1);

    for (const win of windows) {
      maxRow = Math.max(maxRow, win.rect.r1);
      maxCol = Math.max(maxCol, win.rect.c1);
      for (const [key, cell] of Object.entries(cellsWithAnchors(win))) {
        const [rowText, colText] = key.split(",");
        const row = Number(rowText) - 1;
        const col = Number(colText) - 1;
        if (!Number.isFinite(row) || !Number.isFinite(col) || row < 0 || col < 0) continue;
        if (!cellData[row]) cellData[row] = {};
        cellData[row][col] = cellToUniver(cell);
      }
    }

    const mergeData = windows.flatMap((win) =>
      (win.merges || []).map((merge) => ({
        startRow: merge.min_row - 1,
        startColumn: merge.min_col - 1,
        endRow: merge.max_row - 1,
        endColumn: merge.max_col - 1,
      })),
    );

    const columnData: Record<number, { w: number; hd?: number }> = {};
    const rowData: Record<number, { h: number; hd?: number }> = {};
    for (const win of windows) {
      for (const dimension of win.geometry?.columns || []) {
        columnData[dimension.index - 1] = { w: dimension.pixels, hd: dimension.hidden ? 1 : 0 };
      }
      for (const dimension of win.geometry?.rows || []) {
        rowData[dimension.index - 1] = { h: dimension.pixels, hd: dimension.hidden ? 1 : 0 };
      }
    }

    sheetMap[sheetId] = {
      id: sheetId,
      name: meta.name,
      hidden: meta.state && meta.state !== "visible" ? 1 : 0,
      ...(meta.defaults?.column_width?.pixels != null ? { defaultColumnWidth: meta.defaults.column_width.pixels } : {}),
      ...(meta.defaults?.row_height?.pixels != null ? { defaultRowHeight: meta.defaults.row_height.pixels } : {}),
      cellData,
      rowCount: Math.max(maxRow, meta.used.rows, 200),
      columnCount: Math.max(maxCol, meta.used.cols, 50),
      mergeData,
      columnData,
      rowData,
    };
  }

  return {
    id: workbookId,
    sheets: sheetMap,
    sheetOrder,
  };
}

export function demoWorkbookObservation(relative: string): WorkbookObservation {
  const cells: Record<string, ViewCell> = {
    "1,1": { t: "s", v: "月份", cached: "yes" },
    "1,2": { t: "s", v: "产品", cached: "yes" },
    "1,3": { t: "s", v: "销售额", cached: "yes" },
  };
  const months = ["2024-01", "2024-02", "2024-03"];
  months.forEach((month, index) => {
    cells[`${index + 2},1`] = { t: "s", v: month, cached: "yes" };
    cells[`${index + 2},2`] = { t: "s", v: "产品A", cached: "yes" };
    cells[`${index + 2},3`] = { t: "n", v: 12000 + index * 800, cached: "yes" };
  });
  return {
    file: { workspaceKey: "_", relative, observedVersion: "sha256:demo" },
    content_version: "sha256:demo",
    sheets: [{ name: "Sheet1", sheet_id: "Sheet1", used: { rows: 4, cols: 3 } }],
    regions: [{
      sheet: "Sheet1",
      rect: { r0: 1, c0: 1, r1: 4, c1: 3 },
      cells,
    }],
    coverage: {
      loaded: [{ r0: 1, c0: 1, r1: 4, c1: 3, sheet: "Sheet1" }],
      unloaded: [],
    },
  };
}

export function viewMatchesLease(
  view: WorkbookObservation,
  file: WorkspaceFileRef,
): boolean {
  return (
    view.file.workspaceKey === file.workspaceKey &&
    view.file.relative.replace(/^\.\//, "") === file.relative.replace(/^\.\//, "")
  );
}

export function defaultViewRect(usedCols = 50): string {
  return `A1:${colIndexToLetter(Math.max(usedCols, 1) - 1)}200`;
}

export function hasPresentation(view: WorkbookObservation): boolean {
  return view.request?.facets.includes("presentation") === true;
}


export function presentationNotice(view: WorkbookObservation | null): string | null {
  if (!view) return null;
  if (view.sheets.some((s) => (s.available?.conditional_rules || 0) > 0)) {
    return "条件格式规则已保留；动态颜色与图标将在打印预览中计算。";
  }
  return null;
}
