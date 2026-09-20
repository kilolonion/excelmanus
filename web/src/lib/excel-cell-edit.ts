import {
  invalidateWorkbookCaches,
  normalizeExcelPath,
  writeExcelCells,
  type ExcelWriteResponse,
} from "@/lib/api";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import {
  activeSession,
  fileRefKey,
  workspaceKeyFromSession,
  type WorkspaceFileRef,
} from "@/lib/workspace-file-ref";

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
  getExpectedVersion: (path: string, workspaceKey?: string | null) => string | null;
  setContentVersion: (path: string, version: string | null | undefined, workspaceKey?: string | null) => void;
  invalidateCaches: (identity: { workspaceKey: string; relative: string }) => void;
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

const FLUSH_MS = 200;
const MAX_FLUSH_MS = 1000;

type PersistFn = (
  opts: {
    path: string;
    sheet?: string;
    changes: { cell: string; value: unknown; sheet?: string }[];
    operations?: WorkbookOp[];
    sessionId?: string | null;
    workspaceId?: string | null;
    workspaceKey?: string | null;
    expectedVersion?: string | null;
    viewGeneration?: number;
  },
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
  operations: WorkbookOp[];
  sessionId?: string | null;
  workspaceId?: string | null;
  workspaceKey: string;
  expectedVersion?: string | null;
  viewGeneration: number;
  onConflict?: () => void;
  onError?: (message: string) => void;
  timer: ReturnType<typeof setTimeout> | null;
  startedAt: number;
}

const pendingByPath = new Map<string, PendingBatch>();
const inFlightByPath = new Map<string, Promise<void>>();
const pausedByPath = new Set<string>();
const acknowledgedVersions = new Map<string, { before: string; after: string }>();
const editListeners = new Set<() => void>();

export function subscribeWorkbookEdits(listener: () => void): () => void {
  editListeners.add(listener);
  return () => { editListeners.delete(listener); };
}

function emitEditState() { for (const listener of editListeners) listener(); }

export function isWorkbookEditPaused(file: Pick<WorkspaceFileRef, "workspaceKey" | "relative">): boolean {
  return isPaused(fileRefKey(file), file.relative);
}

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

const UNSUPPORTED_COMMANDS = [
  "filter", "chart", "pivot", "conditional-format", "data-validation", "comment", "image",
  "drawing", "move-range", "reorder-range", "move-rows", "move-columns", "copy-sheet",
];

export function isUnsupportedWorkbookMutation(id?: string): boolean {
  const text = (id || "").toLowerCase();
  return UNSUPPORTED_COMMANDS.some((item) => text.includes(item));
}

type MutationRect = { startRow: number; endRow: number; startColumn: number; endColumn: number };

function rangeA1(range: MutationRect): string {
  return `${cellRefFromIndex(range.startRow, range.startColumn)}:${cellRefFromIndex(range.endRow, range.endColumn)}`;
}

export function extractWorkbookOpsFromMutation(params: {
  id?: string;
  params?: Record<string, unknown>;
  sheet?: string;
} | null | undefined): WorkbookOp[] {
  if (!params?.id || !params.id.startsWith("sheet.mutation.")) return [];
  const id = params.id;
  const payload = params.params || {};
  const ranges = (payload.ranges || (payload.range && typeof payload.range === "object" ? [payload.range] : [])) as MutationRect[];
  const sheet = params.sheet;
  if (id === "sheet.mutation.add-worksheet-merge" || id === "sheet.mutation.remove-worksheet-merge") {
    return ranges.map((range) => ({ op: id.includes("add-") ? "merge" : "unmerge", sheet, range: rangeA1(range) }));
  }
  if (id === "sheet.mutation.set-worksheet-col-width" || id === "sheet.mutation.set-worksheet-row-height") {
    const columns: Record<string, number> = {};
    const rows: Record<string, number> = {};
    for (const range of ranges) {
      const col = id.includes("col-width");
      const value = col ? payload.colWidth : payload.rowHeight;
      for (let i = col ? range.startColumn : range.startRow; i <= (col ? range.endColumn : range.endRow); i++) {
        const size = typeof value === "number" ? value : (value as Record<number, number>)?.[i];
        if (typeof size !== "number") continue;
        if (col) columns[colIndexToLetter(i)] = size / 7.5;
        else rows[String(i + 1)] = size * 0.75;
      }
    }
    return [{ op: "set_dims", sheet, columns, rows }];
  }
  if (id === SET_RANGE_VALUES_MUTATION_ID) {
    const values: { cell: string; value: unknown; style?: unknown }[] = [];
    const styles: { cell: string; style: unknown }[] = [];
    const matrix = payload.cellValue as Record<string, Record<string, CellDataLike & { s?: unknown }>> | undefined;
    for (const [rowKey, row] of Object.entries(matrix || {})) {
      for (const [colKey, cell] of Object.entries(row || {})) {
        const address = cellRefFromIndex(Number(rowKey), Number(colKey));
        const parsed = cellDataToWriteValue(cell);
        const style = cell && typeof cell === "object" && "s" in cell ? { style: cell.s } : {};
        if (!parsed.skip) values.push({ cell: address, value: parsed.value, ...style });
        else if ("style" in style) styles.push({ cell: address, style: style.style });
      }
    }
    return [...(values.length ? [{ op: "set_values", sheet, cells: values }] : []),
      ...(styles.length ? [{ op: "set_styles", sheet, cells: styles }] : [])];
  }
  if (["sheet.mutation.insert-row", "sheet.mutation.insert-col", "sheet.mutation.remove-rows", "sheet.mutation.remove-col"].includes(id)) {
    return ranges.map((range) => {
      const col = id.includes("col");
      const start = col ? range.startColumn : range.startRow;
      const end = col ? range.endColumn : range.endRow;
      return { op: id.includes("remove") ? "delete_axis" : "insert_axis", sheet, axis: col ? "col" : "row", index: start + 1, count: end - start + 1 };
    });
  }
  if (id === "sheet.mutation.insert-sheet") {
    const inserted = payload.sheet as { name?: string } | undefined;
    return [{ op: "sheet_add", name: inserted?.name }];
  }
  if (id === "sheet.mutation.remove-sheet") return [{ op: "sheet_delete", name: sheet }];
  if (id === "sheet.mutation.set-worksheet-name") return [{ op: "sheet_rename", from: sheet, to: payload.name }];
  return [];
}

export function hasPendingWorkbookEdits(file: Pick<WorkspaceFileRef, "workspaceKey" | "relative">): boolean {
  const key = fileRefKey(file);
  return pendingByPath.has(key) || inFlightByPath.has(key);
}

function defaultDeps(): PersistExcelCellEditsDeps {
  return {
    writeExcelCells,
    getSessionId: () => useSessionStore.getState().activeSessionId,
    getExpectedVersion: (path, workspaceKey) =>
      useExcelStore.getState().getContentVersion(path, workspaceKey),
    setContentVersion: (path, version, workspaceKey) =>
      useExcelStore.getState().setContentVersion(path, version, workspaceKey),
    invalidateCaches: invalidateWorkbookCaches,
  };
}

export type WorkbookOp = Record<string, unknown>;

export function changesToSetValuesOps(
  changes: { cell: string; value: unknown; sheet?: string; style?: unknown }[],
  defaultSheet?: string,
): WorkbookOp[] {
  const grouped = new Map<string, { cell: string; value: unknown; style?: unknown }[]>();
  for (const change of changes) {
    const sheet = String(change.sheet || defaultSheet || "");
    const list = grouped.get(sheet) ?? [];
    list.push({ cell: change.cell, value: change.value, style: change.style });
    grouped.set(sheet, list);
  }
  return [...grouped.entries()].map(([sheet, cells]) => ({
    op: "set_values",
    sheet: sheet || undefined,
    cells,
  }));
}

export async function persistExcelCellEdits(
  opts: {
    path: string;
    sheet?: string;
    changes?: { cell: string; value: unknown; sheet?: string; style?: unknown }[];
    operations?: WorkbookOp[];
    sessionId?: string | null;
    workspaceId?: string | null;
    workspaceKey?: string | null;
    expectedVersion?: string | null;
    viewGeneration?: number;
  },
  deps?: Partial<PersistExcelCellEditsDeps>,
): Promise<PersistExcelCellEditsResult> {
  const changes = opts.changes ?? [];
  const operations = [...changesToSetValuesOps(changes, opts.sheet), ...(opts.operations || [])];
  if (!opts.path || (changes.length === 0 && operations.length === 0)) return { kind: "skipped" };
  if (isDemoExcelPath(opts.path)) return { kind: "skipped" };

  const currentGen = useExcelStore.getState().viewGeneration;
  const capturedScope = Boolean(opts.workspaceKey && opts.workspaceKey !== "_"
    && (opts.workspaceId || opts.sessionId) && opts.expectedVersion);
  if (opts.viewGeneration != null && opts.viewGeneration !== currentGen && !capturedScope) {
    return { kind: "error", message: "工作区已切换，无法确定未保存更改的来源" };
  }

  const resolved = { ...defaultDeps(), ...deps };
  const session = activeSession();
  const workspaceKey = opts.workspaceKey ?? workspaceKeyFromSession(session);
  const sessionId = opts.sessionId !== undefined ? opts.sessionId ?? undefined : resolved.getSessionId() ?? undefined;
  const expectedVersion =
    opts.expectedVersion ?? resolved.getExpectedVersion(opts.path, workspaceKey);
  if (!expectedVersion) {
    return { kind: "conflict", code: "VERSION_CONFLICT" };
  }
  if (!sessionId && !opts.workspaceId) {
    return { kind: "error", message: "无法确定工作区，请从会话重新打开文件" };
  }

  try {
    const result = await resolved.writeExcelCells({
      path: opts.path,
      sheet: opts.sheet,
      changes: [],
      operations,
      sessionId,
      workspaceId: opts.workspaceKey ? opts.workspaceId ?? null : opts.workspaceId ?? session?.workspaceId ?? null,
      expectedVersion,
    });
    if (isExcelWriteConflict(result)) {
      return { kind: "conflict", code: result.code || "VERSION_CONFLICT" };
    }
    if (result.content_version) {
      resolved.setContentVersion(opts.path, result.content_version, workspaceKey);
    }
    resolved.invalidateCaches({ workspaceKey, relative: opts.path });
    useExcelStore.getState().notifyWorkbookChanged(opts.path, workspaceKey, result.content_version, "local");
    useExcelStore.getState().bumpWorkspaceFilesVersion();
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
  opts: Parameters<PersistFn>[0],
  deps?: Partial<PersistExcelCellEditsDeps>,
): Promise<PersistExcelCellEditsResult> {
  return (persistImpl ?? persistExcelCellEdits)(opts, deps);
}

function queueKey(path: string, workspaceKey?: string | null): string {
  return fileRefKey({
    workspaceKey: workspaceKey || workspaceKeyFromSession(activeSession()),
    relative: path,
  });
}

function isPaused(key: string, path: string): boolean {
  const norm = normalizeExcelPath(path);
  return pausedByPath.has(key) || pausedByPath.has(norm);
}

export function pauseExcelCellEdits(path: string): void {
  const norm = normalizeExcelPath(path);
  pausedByPath.add(norm);
  pausedByPath.add(queueKey(path));
}

export function resumeExcelCellEdits(path: string): void {
  const norm = normalizeExcelPath(path);
  pausedByPath.delete(norm);
  pausedByPath.delete(queueKey(path));
  for (const key of [...pausedByPath]) {
    if (key.endsWith(`|${norm}`)) pausedByPath.delete(key);
  }
}

/** Explicit reload discards this file's uncommitted queue before rebasing. */
export function discardWorkbookEdits(file: Pick<WorkspaceFileRef, "workspaceKey" | "relative">): void {
  const key = fileRefKey(file);
  const pending = pendingByPath.get(key);
  if (pending?.timer) clearTimeout(pending.timer);
  pendingByPath.delete(key);
  pausedByPath.delete(key);
  acknowledgedVersions.delete(key);
  emitEditState();
}

export function enqueueExcelCellEdit(opts: {
  path: string;
  sheet?: string;
  cell: string;
  value: unknown;
  file?: WorkspaceFileRef;
  sessionId?: string | null;
  viewGeneration?: number;
  expectedVersion?: string | null;
  onConflict?: () => void;
  onError?: (message: string) => void;
}): void {
  if (!opts.path || !opts.cell) return;
  if (isDemoExcelPath(opts.path)) return;
  const session = activeSession();
  const workspaceKey = opts.file?.workspaceKey ?? workspaceKeyFromSession(session);
  const key = queueKey(opts.path, workspaceKey);
  if (isPaused(key, opts.path)) return;

  let batch = pendingByPath.get(key);
  if (!batch) {
    batch = {
      path: opts.path,
      sheet: opts.sheet,
      changes: [],
      operations: [],
      sessionId: opts.sessionId ?? useSessionStore.getState().activeSessionId,
      workspaceId: opts.file?.workspaceId ?? session?.workspaceId ?? null,
      workspaceKey,
      expectedVersion:
        opts.expectedVersion ??
        useExcelStore.getState().getContentVersion(opts.path, workspaceKey),
      viewGeneration: opts.viewGeneration ?? useExcelStore.getState().viewGeneration,
      onConflict: opts.onConflict,
      onError: opts.onError,
      timer: null,
      startedAt: Date.now(),
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
  }, Math.max(0, Math.min(FLUSH_MS, MAX_FLUSH_MS - (Date.now() - batch.startedAt))));
  emitEditState();
}

export function enqueueWorkbookCommand(opts: {
  path: string;
  operations: WorkbookOp[];
  file?: WorkspaceFileRef;
  sessionId?: string | null;
  viewGeneration?: number;
  expectedVersion?: string | null;
  onConflict?: () => void;
  onError?: (message: string) => void;
}): void {
  if (!opts.path || opts.operations.length === 0) return;
  if (isDemoExcelPath(opts.path)) return;
  const session = activeSession();
  const workspaceKey = opts.file?.workspaceKey ?? workspaceKeyFromSession(session);
  const key = queueKey(opts.path, workspaceKey);
  if (isPaused(key, opts.path)) return;
  let batch = pendingByPath.get(key);
  if (!batch) {
    batch = {
      path: opts.path,
      changes: [],
      operations: [],
      sessionId: opts.sessionId ?? useSessionStore.getState().activeSessionId,
      workspaceId: opts.file?.workspaceId ?? session?.workspaceId ?? null,
      workspaceKey,
      expectedVersion:
        opts.expectedVersion ??
        useExcelStore.getState().getContentVersion(opts.path, workspaceKey),
      viewGeneration: opts.viewGeneration ?? useExcelStore.getState().viewGeneration,
      onConflict: opts.onConflict,
      onError: opts.onError,
      timer: null,
      startedAt: Date.now(),
    };
    pendingByPath.set(key, batch);
  }
  if (opts.onConflict) batch.onConflict = opts.onConflict;
  if (opts.onError) batch.onError = opts.onError;
  batch.operations.push(...opts.operations);
  if (batch.timer) clearTimeout(batch.timer);
  batch.timer = setTimeout(() => {
    void flushPath(key);
  }, Math.max(0, Math.min(FLUSH_MS, MAX_FLUSH_MS - (Date.now() - batch.startedAt))));
  emitEditState();
}

async function flushPath(key: string): Promise<void> {
  const batch = pendingByPath.get(key);
  if (!batch) return;
  pendingByPath.delete(key);
  if (batch.timer) {
    clearTimeout(batch.timer);
    batch.timer = null;
  }
  if (isPaused(key, batch.path) || (batch.changes.length === 0 && batch.operations.length === 0)) return;

  const run = async () => {
    if (isPaused(key, batch.path)) return;
    let expected = batch.expectedVersion;
    const ack = acknowledgedVersions.get(key);
    if (ack && expected === ack.before) expected = ack.after;
    const result = await runPersist({
      path: batch.path,
      sheet: batch.sheet,
      changes: batch.changes,
      operations: batch.operations.length ? batch.operations : undefined,
      sessionId: batch.sessionId,
      workspaceId: batch.workspaceId,
      workspaceKey: batch.workspaceKey,
      expectedVersion: expected,
      viewGeneration: batch.viewGeneration,
    });
    if (result.kind === "ok" && result.contentVersion && expected) {
      acknowledgedVersions.set(key, { before: batch.expectedVersion || expected, after: result.contentVersion });
    } else if (result.kind === "conflict") {
      pausedByPath.add(key);
      batch.onConflict?.();
    } else if (result.kind === "error") {
      pausedByPath.add(key);
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
    emitEditState();
  }
}

/** Drain only this file; switching surfaces must not discard debounced edits. */
export async function flushWorkbookEdits(file: Pick<WorkspaceFileRef, "workspaceKey" | "relative">): Promise<void> {
  const key = fileRefKey(file);
  await flushPath(key);
  await inFlightByPath.get(key);
}

/** 测试用：立刻冲刷队列并等待在途写入。 */
export async function flushExcelCellEditsForTests(path?: string): Promise<void> {
  const norm = path ? normalizeExcelPath(path) : "";
  const keys = path
    ? [...pendingByPath.keys()].filter((key) => key === norm || key.endsWith(`|${norm}`))
    : [...pendingByPath.keys()];
  if (path && keys.length === 0) keys.push(queueKey(path));
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
  acknowledgedVersions.clear();
}
