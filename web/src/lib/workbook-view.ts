import type { WorkspaceFileRef } from "@/lib/workspace-file-ref";

export type CellType = "n" | "s" | "b" | "d" | "e" | "z";
export type CachedState = "yes" | "no" | "unknown";

export interface ViewCell {
  t: CellType;
  v: unknown;
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

export interface WorkbookViewWindow {
  sheet: string;
  rect: ViewRect;
  cells: Record<string, ViewCell>;
  merges?: { min_row: number; min_col: number; max_row: number; max_col: number }[];
  col_widths?: Record<string, number>;
  row_heights?: Record<string, number>;
}

export interface WorkbookViewSnapshot {
  file: {
    workspaceKey: string;
    relative: string;
    observedVersion?: string;
  };
  content_version: string;
  snapshot_id?: string;
  sheets: { name: string; sheet_id: string; used: { rows: number; cols: number } }[];
  windows: WorkbookViewWindow[];
  coverage: {
    loaded: ViewRect[];
    unloaded: ViewRect[];
    truncated_reason?: string | null;
  };
}

const UNLOADED_STYLE = { bg: { rgb: "EEEEEE" }, cl: { rgb: "888888" } };

function colIndexToLetter(index: number): string {
  let result = "";
  let n = index;
  while (n >= 0) {
    result = String.fromCharCode((n % 26) + 65) + result;
    n = Math.floor(n / 26) - 1;
  }
  return result;
}

function letterToColIndex(letter: string): number {
  let n = 0;
  for (const ch of letter.toUpperCase()) {
    n = n * 26 + (ch.charCodeAt(0) - 64);
  }
  return n - 1;
}

export function cellToUniver(cell: ViewCell): Record<string, unknown> {
  const data: Record<string, unknown> = {};
  if (cell.f) {
    data.f = cell.f.startsWith("=") ? cell.f : `=${cell.f}`;
  }
  if (cell.v !== null && cell.v !== undefined) {
    data.v = cell.v;
  } else if (!cell.f) {
    data.v = null;
  }
  if (cell.s) data.s = cell.s;
  if (cell.t === "e" && cell.e) {
    data.v = cell.e;
  }
  return data;
}

const PLACEHOLDER_CELL_CAP = 20_000;

export function viewSnapshotToUniver(
  view: WorkbookViewSnapshot,
  workbookId: string,
): Record<string, unknown> {
  const sheetMap: Record<string, unknown> = {};
  const sheetOrder: string[] = [];

  for (const meta of view.sheets) {
    const sheetId = `sheet-${meta.name}`;
    sheetOrder.push(sheetId);
    const windows = view.windows.filter((item) => item.sheet === meta.name);
    const cellData: Record<number, Record<number, unknown>> = {};
    let maxRow = Math.max(meta.used.rows, 1);
    let maxCol = Math.max(meta.used.cols, 1);

    for (const win of windows) {
      maxRow = Math.max(maxRow, win.rect.r1);
      maxCol = Math.max(maxCol, win.rect.c1);
      for (const [key, cell] of Object.entries(win.cells)) {
        const [rowText, colText] = key.split(",");
        const row = Number(rowText) - 1;
        const col = Number(colText) - 1;
        if (!Number.isFinite(row) || !Number.isFinite(col) || row < 0 || col < 0) continue;
        if (!cellData[row]) cellData[row] = {};
        cellData[row][col] = cellToUniver(cell);
      }
    }

    let placeholders = 0;
    for (const rect of view.coverage.unloaded) {
      if (rect.sheet && rect.sheet !== meta.name) continue;
      for (let row = rect.r0; row <= rect.r1 && placeholders < PLACEHOLDER_CELL_CAP; row += 1) {
        for (let col = rect.c0; col <= rect.c1 && placeholders < PLACEHOLDER_CELL_CAP; col += 1) {
          const r = row - 1;
          const c = col - 1;
          if (cellData[r]?.[c]) continue;
          if (!cellData[r]) cellData[r] = {};
          cellData[r][c] = { v: "", s: UNLOADED_STYLE, custom: { unloaded: true } };
          placeholders += 1;
        }
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

    const columnData: Record<number, { w: number }> = {};
    const rowData: Record<number, { h: number }> = {};
    for (const win of windows) {
      for (const [letter, width] of Object.entries(win.col_widths || {})) {
        columnData[letterToColIndex(letter)] = { w: width * 7.5 };
      }
      for (const [rowKey, height] of Object.entries(win.row_heights || {})) {
        rowData[Number(rowKey) - 1] = { h: height / 0.75 };
      }
    }

    sheetMap[sheetId] = {
      id: sheetId,
      name: meta.name,
      cellData,
      rowCount: Math.max(maxRow, meta.used.rows, 1),
      columnCount: Math.max(maxCol, meta.used.cols, 26),
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

export function demoWorkbookView(relative: string): WorkbookViewSnapshot {
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
    windows: [{
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
  view: WorkbookViewSnapshot,
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
