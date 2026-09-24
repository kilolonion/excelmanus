import { workbookStylePatch } from "@/lib/workbook-style";
import {
  invalidateWorkbookCaches,
  normalizeExcelPath,
  applyWorkbookChanges,
  type ExcelWriteResponse,
} from "@/lib/api";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import {
  activeSession,
  fileRefKey,
  versionStoreKey,
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
  applyWorkbookChanges: typeof applyWorkbookChanges;
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
  discarded?: boolean;
}

const pendingByPath = new Map<string, PendingBatch>();
const inFlightByPath = new Map<string, Promise<void>>();
const pausedByPath = new Set<string>();
const acknowledgedVersions = new Map<string, Map<string, string>>();
const structurallyChangedVersions = new Map<string, Set<string>>();
// Includes failed and serialized batches until saved or explicitly discarded.
const unsavedByPath = new Map<string, Set<PendingBatch>>();
const editListeners = new Set<() => void>();

/** Includes failed/conflicting batches, not just writes currently in flight. */
export function hasUnsavedWorkbookEdits(): boolean {
  return unsavedByPath.size > 0 || pendingByPath.size > 0 || inFlightByPath.size > 0;
}

export function subscribeWorkbookEdits(listener: () => void): () => void {
  editListeners.add(listener);
  return () => { editListeners.delete(listener); };
}

function emitEditState() { for (const listener of editListeners) listener(); }

export function isWorkbookEditPaused(file: Pick<WorkspaceFileRef, "workspaceKey" | "relative">): boolean {
  return isPaused(fileRefKey(file), file.relative);
}

/** Advance a viewed version only through acknowledged edits made in this client. */
export function acknowledgedWorkbookVersion(
  file: Pick<WorkspaceFileRef, "workspaceKey" | "relative">,
  viewedVersion?: string,
): string | undefined {
  return viewedVersion ? acknowledgedVersions.get(fileRefKey(file))?.get(viewedVersion) ?? viewedVersion : undefined;
}

/** A version advance may preserve values while invalidating old row/column coordinates. */
export function acknowledgedSelectionVersion(file: Pick<WorkspaceFileRef, "workspaceKey" | "relative">, version?: string): string | undefined {
  if (version && structurallyChangedVersions.get(fileRefKey(file))?.has(version)) {
    throw new Error("表格行列或工作表结构已改变，请重新选择区域后发送");
  }
  return acknowledgedWorkbookVersion(file, version);
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
    return ranges.map((range) => ({ kind: id.includes("add-") ? "merge" : "unmerge", sheet, range: rangeA1(range) }));
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
        if (col) columns[String(i + 1)] = size;
        else rows[String(i + 1)] = size;
      }
    }
    return [{ kind: "geometry.resize", sheet, axis: id.includes("col-width") ? "column" : "row", sizes: id.includes("col-width") ? columns : rows, unit: "css_px" }];
  }
  if (id === SET_RANGE_VALUES_MUTATION_ID) {
    const values: { cell: string; value: unknown; style?: unknown }[] = [];
    const styles: { cell: string; style: unknown }[] = [];
    const matrix = payload.cellValue as Record<string, Record<string, CellDataLike & { s?: unknown }>> | undefined;
    for (const [rowKey, row] of Object.entries(matrix || {})) {
      for (const [colKey, cell] of Object.entries(row || {})) {
        const address = cellRefFromIndex(Number(rowKey), Number(colKey));
        const parsed = cellDataToWriteValue(cell);
        const style = cell && typeof cell === "object" && "s" in cell ? { style: workbookStylePatch(cell.s) } : {};
        if (!parsed.skip) values.push({ cell: address, value: parsed.value, ...style });
        else if ("style" in style) styles.push({ cell: address, style: style.style });
      }
    }
    return [...(values.length ? [{ kind: "cells.patch", sheet, cells: values }] : []),
      ...(styles.length ? [{ kind: "cells.patch", sheet, cells: styles }] : [])];
  }
  if (["sheet.mutation.insert-row", "sheet.mutation.insert-col", "sheet.mutation.remove-rows", "sheet.mutation.remove-col"].includes(id)) {
    return ranges.map((range) => {
      const col = id.includes("col");
      const start = col ? range.startColumn : range.startRow;
      const end = col ? range.endColumn : range.endRow;
      return id.includes("remove") ? { kind: col ? "delete_columns" : "delete_rows", sheet, at: start + 1, count: end - start + 1 } : { kind: "insert", sheet, axis: col ? "column" : "row", at: start + 1, count: end - start + 1 };
    });
  }
  if (id === "sheet.mutation.insert-sheet") {
    const inserted = payload.sheet as { name?: string } | undefined;
    return [{ kind: "sheet", action: "create", new_name: inserted?.name }];
  }
  if (id === "sheet.mutation.remove-sheet") return [{ kind: "sheet", action: "delete", sheet }];
  if (id === "sheet.mutation.set-worksheet-name") return [{ kind: "sheet", action: "rename", sheet, new_name: payload.name }];
  return [];
}

export function hasPendingWorkbookEdits(file: Pick<WorkspaceFileRef, "workspaceKey" | "relative">): boolean {
  const key = fileRefKey(file);
  return pendingByPath.has(key) || inFlightByPath.has(key);
}

function defaultDeps(): PersistExcelCellEditsDeps {
  return {
    applyWorkbookChanges,
    getSessionId: () => useSessionStore.getState().activeSessionId,
    getExpectedVersion: (path, workspaceKey) =>
      useExcelStore.getState().getContentVersion(path, workspaceKey),
    setContentVersion: (path, version, workspaceKey) =>
      useExcelStore.getState().setContentVersion(path, version, workspaceKey),
    invalidateCaches: invalidateWorkbookCaches,
  };
}

export type WorkbookOp = Record<string, unknown>;

export function cellChangesToOperations(
  changes: { cell: string; value: unknown; sheet?: string; style?: unknown }[],
  defaultSheet?: string,
): WorkbookOp[] {
  const grouped = new Map<string, { cell: string; value: unknown; style?: unknown }[]>();
  for (const change of changes) {
    const sheet = String(change.sheet || defaultSheet || "");
    const list = grouped.get(sheet) ?? [];
    list.push({ cell: change.cell, value: change.value, ...(change.style !== undefined ? { style: workbookStylePatch(change.style) } : {}) });
    grouped.set(sheet, list);
  }
  return [...grouped.entries()].map(([sheet, cells]) => ({
    kind: "cells.patch",
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
  const operations = [...(opts.operations || []), ...cellChangesToOperations(changes, opts.sheet)];
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
    opts.expectedVersion !== undefined ? opts.expectedVersion : resolved.getExpectedVersion(opts.path, workspaceKey);
  if (!expectedVersion) {
    return { kind: "conflict", code: "VERSION_CONFLICT" };
  }
  if (!sessionId && !opts.workspaceId) {
    return { kind: "error", message: "无法确定工作区，请从会话重新打开文件" };
  }

  const changeKey = versionStoreKey(opts.path, workspaceKey);
  const changeSequence = useExcelStore.getState().workbookChanges[changeKey]?.sequence;
  try {
    const result = await resolved.applyWorkbookChanges({
      path: opts.path,
      operations,
      sessionId,
      workspaceId: opts.workspaceKey ? opts.workspaceId ?? null : opts.workspaceId ?? session?.workspaceId ?? null,
      expectedVersion,
    });
    if (isExcelWriteConflict(result)) {
      return { kind: "conflict", code: result.code || "VERSION_CONFLICT" };
    }
    const latestChange = useExcelStore.getState().workbookChanges[changeKey];
    const changedDuringSave = latestChange?.sequence !== changeSequence
      && latestChange?.version !== result.content_version && latestChange?.version !== expectedVersion;
    if (result.content_version && !changedDuringSave) {
      resolved.setContentVersion(opts.path, result.content_version, workspaceKey);
    }
    resolved.invalidateCaches({ workspaceKey, relative: opts.path });
    // A later remote mutation can arrive before this HTTP acknowledgement.
    // Keep its refresh/version; publishing this receipt would rewind the view.
    if (!changedDuringSave) {
      useExcelStore.getState().notifyWorkbookChanged(opts.path, workspaceKey, result.content_version, "local");
    }
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
  structurallyChangedVersions.delete(key);
  for (const batch of unsavedByPath.get(key) || []) batch.discarded = true;
  unsavedByPath.delete(key);
  emitEditState();
}

/** Recovery artifact retains operation order, scope and original snapshot version. */
export function workbookEditDraft(file: Pick<WorkspaceFileRef, "workspaceKey" | "relative">): string {
  return JSON.stringify({ file, batches: [...(unsavedByPath.get(fileRefKey(file)) || [])].map((batch) => ({
    expected_version: acknowledgedWorkbookVersion(file, batch.expectedVersion ?? undefined),
    operations: [...batch.operations, ...cellChangesToOperations(batch.changes, batch.sheet)],
  })) }, null, 2);
}

function trackUnsaved(key: string, batch: PendingBatch): void {
  const batches = unsavedByPath.get(key) || new Set<PendingBatch>();
  batches.add(batch);
  unsavedByPath.set(key, batches);
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
        opts.expectedVersion !== undefined ? opts.expectedVersion :
        useExcelStore.getState().getContentVersion(opts.path, workspaceKey),
      viewGeneration: opts.viewGeneration ?? useExcelStore.getState().viewGeneration,
      onConflict: opts.onConflict,
      onError: opts.onError,
      timer: null,
      startedAt: Date.now(),
    };
    pendingByPath.set(key, batch);
    trackUnsaved(key, batch);
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
        opts.expectedVersion !== undefined ? opts.expectedVersion :
        useExcelStore.getState().getContentVersion(opts.path, workspaceKey),
      viewGeneration: opts.viewGeneration ?? useExcelStore.getState().viewGeneration,
      onConflict: opts.onConflict,
      onError: opts.onError,
      timer: null,
      startedAt: Date.now(),
    };
    pendingByPath.set(key, batch);
    trackUnsaved(key, batch);
  }
  if (opts.onConflict) batch.onConflict = opts.onConflict;
  if (opts.onError) batch.onError = opts.onError;
  batch.operations.push(...cellChangesToOperations(batch.changes, batch.sheet), ...opts.operations);
  batch.changes = [];
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
  if (isPaused(key, batch.path) || (batch.changes.length === 0 && batch.operations.length === 0)) {
    emitEditState();
    return;
  }

  const run = async () => {
    if (batch.discarded || isPaused(key, batch.path)) return;
    let expected = batch.expectedVersion;
    const ack = acknowledgedVersions.get(key);
    if (expected && ack?.has(expected)) expected = ack.get(expected)!;
    batch.expectedVersion = expected;
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
    if (batch.discarded) return;
    if (result.kind === "ok" || result.kind === "skipped") {
      const unsaved = unsavedByPath.get(key);
      unsaved?.delete(batch);
      if (!unsaved?.size) unsavedByPath.delete(key);
    }
    if (result.kind === "ok" && result.contentVersion && expected) {
      const versions = acknowledgedVersions.get(key) || new Map<string, string>();
      const structural = batch.operations.some((op) => !["cells.patch", "size", "format"].includes(String(op.kind)));
      const changed = structurallyChangedVersions.get(key) ?? new Set<string>();
      if (structural) {
        changed.add(expected);
        for (const [before, after] of versions) if (after === expected) changed.add(before);
      }
      while (changed.size > 128) changed.delete(changed.values().next().value!);
      structurallyChangedVersions.set(key, changed);
      for (const [before, after] of versions) {
        if (after === expected) versions.set(before, result.contentVersion);
      }
      versions.set(expected, result.contentVersion);
      // Old entries may safely conflict; never replace them with a remote version.
      while (versions.size > 128) versions.delete(versions.keys().next().value!);
      acknowledgedVersions.set(key, versions);
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
  structurallyChangedVersions.clear();
  unsavedByPath.clear();
}
