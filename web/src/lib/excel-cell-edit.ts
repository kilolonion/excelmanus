import {
  invalidateSnapshotCache,
  normalizeExcelPath,
  writeExcelCells,
  type ExcelWriteResponse,
} from "@/lib/api";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";

export const SET_RANGE_VALUES_MUTATION_ID = "sheet.mutation.set-range-values";

export interface ExcelCellChange {
  sheet: string;
  cell: string;
  value: unknown;
}

export type PersistExcelCellEditsResult =
  | { kind: "ok"; contentVersion?: string }
  | { kind: "conflict"; code: string }
  | { kind: "error"; message: string }
  | { kind: "skipped" };

export interface PersistExcelCellEditsDeps {
  writeExcelCells: typeof writeExcelCells;
  getSessionId: () => string | null | undefined;
  getExpectedVersion: (path: string) => string | null;
  setContentVersion: (path: string, version: string | null | undefined) => void;
  invalidateSnapshotCache: (path?: string) => void;
}

type CellDataLike = {
  v?: unknown;
  f?: string;
} | null | undefined;

type RangeLike = {
  getSheetName?: () => string;
  getRange?: () => {
    startRow: number;
    startColumn: number;
    endRow: number;
    endColumn: number;
  };
  getValues?: () => unknown[][] | null;
  getCellDatas?: () => CellDataLike[][] | null;
  getCellDataGrid?: () => CellDataLike[][] | null;
};

export type SheetValueChangedLike = {
  payload?: {
    id?: string;
    params?: {
      cellValue?: Record<string, Record<string, CellDataLike>>;
      subUnitId?: string;
    };
  };
  effectedRanges?: RangeLike[];
};

const FLUSH_MS = 80;

type PersistFn = (
  opts: { path: string; sheet?: string; changes: { cell: string; value: unknown }[] },
  deps?: Partial<PersistExcelCellEditsDeps>,
) => Promise<PersistExcelCellEditsResult>;

let persistImpl: PersistFn | null = null;

export function setPersistExcelCellEditsForTests(fn: PersistFn | null): void {
  persistImpl = fn;
}

interface PendingBatch {
  path: string;
  sheet?: string;
  changes: { cell: string; value: unknown; sheet?: string }[];
  onConflict?: () => void;
  onError?: (message: string) => void;
  timer: ReturnType<typeof setTimeout> | null;
}

const pendingByPath = new Map<string, PendingBatch>();
const inFlightByPath = new Map<string, Promise<void>>();
const pausedByPath = new Set<string>();

/** 将 0-based 列索引转换为 Excel 列字母（0→A, 25→Z, 26→AA）。 */
export function colIndexToLetter(index: number): string {
  let result = "";
  let n = index;
  while (n >= 0) {
    result = String.fromCharCode((n % 26) + 65) + result;
    n = Math.floor(n / 26) - 1;
  }
  return result;
}

export function cellRefFromIndex(row: number, col: number): string {
  return `${colIndexToLetter(col)}${row + 1}`;
}

export function isDemoExcelPath(path: string): boolean {
  return path.startsWith("__demo__") || path.startsWith("./__demo__");
}

export function isExcelWriteConflict(result: ExcelWriteResponse): boolean {
  return (
    result.code === "VERSION_CONFLICT" ||
    result.status === "conflict" ||
    result.status === "VERSION_CONFLICT"
  );
}

function cellDataToWriteValue(cell: CellDataLike): { skip: true } | { skip: false; value: unknown } {
  if (cell == null) return { skip: false, value: null };
  if (typeof cell !== "object") return { skip: false, value: cell };
  if (cell.f) {
    const formula = String(cell.f);
    return { skip: false, value: formula.startsWith("=") ? formula : `=${formula}` };
  }
  if ("v" in cell) return { skip: false, value: cell.v ?? null };
  return { skip: true };
}

function collectFormulaCells(ranges: RangeLike[]): Set<string> {
  const formulaCells = new Set<string>();
  for (const range of ranges) {
    const datas = range.getCellDatas?.() ?? range.getCellDataGrid?.();
    const r = range.getRange?.();
    if (!datas || !r) continue;
    datas.forEach((row, ri) => {
      row.forEach((cell, ci) => {
        if (cell?.f) formulaCells.add(cellRefFromIndex(r.startRow + ri, r.startColumn + ci));
      });
    });
  }
  return formulaCells;
}

/**
 * 从 Univer `SheetValueChanged` 事件中提取需要写回的单元格。
 * 仅处理 `sheet.mutation.set-range-values`；公式计算结果（格子已有公式、mutation 只改 v）会跳过。
 */
export function extractCellEditsFromSheetValueChanged(
  params: SheetValueChangedLike | null | undefined,
): ExcelCellChange[] {
  if (!params) return [];
  const mutationId = params.payload?.id;
  if (mutationId && mutationId !== SET_RANGE_VALUES_MUTATION_ID) return [];

  const ranges = params.effectedRanges ?? [];
  const formulaCells = collectFormulaCells(ranges);
  const defaultSheet = ranges[0]?.getSheetName?.() || "";
  const changes: ExcelCellChange[] = [];

  const cellValue = params.payload?.params?.cellValue;
  if (cellValue && typeof cellValue === "object") {
    for (const [rowKey, row] of Object.entries(cellValue)) {
      if (!row || typeof row !== "object") continue;
      const r = Number(rowKey);
      if (!Number.isFinite(r)) continue;
      for (const [colKey, cell] of Object.entries(row)) {
        const c = Number(colKey);
        if (!Number.isFinite(c)) continue;
        const a1 = cellRefFromIndex(r, c);
        const parsed = cellDataToWriteValue(cell);
        if (parsed.skip) continue;
        const mutationHasFormula = Boolean(cell && typeof cell === "object" && cell.f);
        if (formulaCells.has(a1) && !mutationHasFormula) continue;
        const sheet =
          ranges.find((range) => {
            const box = range.getRange?.();
            if (!box) return false;
            return r >= box.startRow && r <= box.endRow && c >= box.startColumn && c <= box.endColumn;
          })?.getSheetName?.() || defaultSheet;
        changes.push({ sheet, cell: a1, value: parsed.value });
      }
    }
    return changes;
  }

  for (const range of ranges) {
    const box = range.getRange?.();
    if (!box) continue;
    const sheet = range.getSheetName?.() || defaultSheet;
    const values = range.getValues?.();
    const datas = range.getCellDatas?.() ?? range.getCellDataGrid?.();
    const rowCount = box.endRow - box.startRow + 1;
    const colCount = box.endColumn - box.startColumn + 1;
    for (let ri = 0; ri < rowCount; ri++) {
      for (let ci = 0; ci < colCount; ci++) {
        const a1 = cellRefFromIndex(box.startRow + ri, box.startColumn + ci);
        const data = datas?.[ri]?.[ci];
        if (data?.f && formulaCells.has(a1)) continue;
        const parsed = cellDataToWriteValue(data);
        if (!parsed.skip) {
          changes.push({ sheet, cell: a1, value: parsed.value });
          continue;
        }
        if (values) {
          changes.push({ sheet, cell: a1, value: values[ri]?.[ci] ?? null });
        }
      }
    }
  }
  return changes;
}

function defaultDeps(): PersistExcelCellEditsDeps {
  return {
    writeExcelCells,
    getSessionId: () => useSessionStore.getState().activeSessionId,
    getExpectedVersion: (path) => useExcelStore.getState().getContentVersion(path),
    setContentVersion: (path, version) => useExcelStore.getState().setContentVersion(path, version),
    invalidateSnapshotCache,
  };
}

export async function persistExcelCellEdits(
  opts: {
    path: string;
    sheet?: string;
    changes: { cell: string; value: unknown; sheet?: string }[];
  },
  deps?: Partial<PersistExcelCellEditsDeps>,
): Promise<PersistExcelCellEditsResult> {
  if (!opts.path || opts.changes.length === 0) return { kind: "skipped" };
  if (isDemoExcelPath(opts.path)) return { kind: "skipped" };

  const resolved = { ...defaultDeps(), ...deps };
  const sessionId = resolved.getSessionId() ?? undefined;
  const expectedVersion = resolved.getExpectedVersion(opts.path);
  if (!expectedVersion) {
    return { kind: "conflict", code: "VERSION_CONFLICT" };
  }

  try {
    const result = await resolved.writeExcelCells({
      path: opts.path,
      sheet: opts.sheet,
      changes: opts.changes.map((change) => ({
        cell: change.cell,
        value: change.value,
        sheet: change.sheet ?? opts.sheet,
      })),
      sessionId,
      expectedVersion,
    });
    if (isExcelWriteConflict(result)) {
      return { kind: "conflict", code: result.code || "VERSION_CONFLICT" };
    }
    if (result.content_version) {
      resolved.setContentVersion(opts.path, result.content_version);
    }
    resolved.invalidateSnapshotCache(opts.path);
    return { kind: "ok", contentVersion: result.content_version };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    if (message.includes("VERSION_CONFLICT") || message.includes("409")) {
      return { kind: "conflict", code: "VERSION_CONFLICT" };
    }
    return { kind: "error", message };
  }
}

function runPersist(
  opts: { path: string; sheet?: string; changes: { cell: string; value: unknown }[] },
  deps?: Partial<PersistExcelCellEditsDeps>,
): Promise<PersistExcelCellEditsResult> {
  return (persistImpl ?? persistExcelCellEdits)(opts, deps);
}

export function pauseExcelCellEdits(path: string): void {
  pausedByPath.add(normalizeExcelPath(path));
}

export function resumeExcelCellEdits(path: string): void {
  pausedByPath.delete(normalizeExcelPath(path));
}

export function enqueueExcelCellEdit(opts: {
  path: string;
  sheet?: string;
  cell: string;
  value: unknown;
  onConflict?: () => void;
  onError?: (message: string) => void;
}): void {
  if (!opts.path || !opts.cell) return;
  if (isDemoExcelPath(opts.path)) return;
  const key = normalizeExcelPath(opts.path);
  if (pausedByPath.has(key)) return;

  let batch = pendingByPath.get(key);
  if (!batch) {
    batch = {
      path: opts.path,
      sheet: opts.sheet,
      changes: [],
      onConflict: opts.onConflict,
      onError: opts.onError,
      timer: null,
    };
    pendingByPath.set(key, batch);
  }
  if (opts.sheet && !batch.sheet) batch.sheet = opts.sheet;
  if (opts.onConflict) batch.onConflict = opts.onConflict;
  if (opts.onError) batch.onError = opts.onError;
  const existing = batch.changes.findIndex(
    (c) => c.cell === opts.cell && (c.sheet || "") === (opts.sheet || ""),
  );
  if (existing >= 0) {
    batch.changes[existing] = { cell: opts.cell, value: opts.value, sheet: opts.sheet };
  } else {
    batch.changes.push({ cell: opts.cell, value: opts.value, sheet: opts.sheet });
  }

  if (batch.timer) clearTimeout(batch.timer);
  batch.timer = setTimeout(() => {
    void flushPath(key);
  }, FLUSH_MS);
}

async function flushPath(key: string): Promise<void> {
  const batch = pendingByPath.get(key);
  if (!batch) return;
  pendingByPath.delete(key);
  if (batch.timer) {
    clearTimeout(batch.timer);
    batch.timer = null;
  }
  if (pausedByPath.has(key) || batch.changes.length === 0) return;

  const run = async () => {
    const result = await runPersist({
      path: batch.path,
      sheet: batch.sheet,
      changes: batch.changes,
    });
    if (result.kind === "conflict") {
      pauseExcelCellEdits(batch.path);
      batch.onConflict?.();
    } else if (result.kind === "error") {
      batch.onError?.(result.message);
    }
  };

  let releaseInFlight!: () => void;
  const held = new Promise<void>((resolve) => {
    releaseInFlight = resolve;
  });
  const prev = inFlightByPath.get(key) ?? Promise.resolve();
  inFlightByPath.set(key, held);
  const next = prev.then(run, run).finally(releaseInFlight);
  try {
    await next;
  } finally {
    if (inFlightByPath.get(key) === held) inFlightByPath.delete(key);
  }
}

/** 测试用：立刻冲刷队列并等待在途写入。 */
export async function flushExcelCellEditsForTests(path?: string): Promise<void> {
  const keys = path ? [normalizeExcelPath(path)] : [...pendingByPath.keys()];
  await Promise.all(keys.map((key) => flushPath(key)));
  await Promise.all([...inFlightByPath.values()]);
}

/** 测试用：清空 debounce / 冲突暂停状态。 */
export function resetExcelCellEditStateForTests(): void {
  persistImpl = null;
  for (const batch of pendingByPath.values()) {
    if (batch.timer) clearTimeout(batch.timer);
  }
  pendingByPath.clear();
  inFlightByPath.clear();
  pausedByPath.clear();
}
