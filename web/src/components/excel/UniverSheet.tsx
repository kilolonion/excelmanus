"use client";

import { isSingleCellSelection, readActiveRange } from "@/lib/excel-selection";

import { useEffect, useRef, useCallback, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { ICellData, IDisposable, IRange } from "@univerjs/core";
import type { FUniver, IEventParamConfig } from "@univerjs/core/facade";
import type { FWorksheet } from "@univerjs/sheets/facade";
import { useExcelStore } from "@/stores/excel-store";
import { useIsMobile } from "@/hooks/use-mobile";
import { useTouchGesture } from "@/hooks/use-touch-gesture";
import { fetchWorkbookView } from "@/lib/api";
import { fileBaseName } from "@/lib/revision-display";
import { ExcelRibbonCommands } from "@/components/excel/ExcelRibbonCommands";
import {
  enqueueWorkbookCommand,
  hasPendingWorkbookEdits,
  extractWorkbookOpsFromMutation,
  isDemoExcelPath,
  isUnsupportedWorkbookMutation,
  flushWorkbookEdits,
  subscribeWorkbookEdits,
  isWorkbookEditPaused,
  discardWorkbookEdits,
  workbookEditDraft,
  acknowledgedSelectionVersion,
} from "@/lib/excel-cell-edit";
import { useWorkbookFocusStore } from "@/stores/workbook-focus-store";
import { highlightWorkbookRanges, type WorkbookHighlightRegistry } from "@/lib/workbook-focus";
import { saveBlob } from "@/lib/save-blob";
import { letterToColIndex, windowCellPatch, demoWorkbookView, viewMatchesLease, viewSnapshotToUniver, type WorkbookViewSnapshot } from "@/lib/workbook-view";
import { pageForCell, pagesForViewport, rangeIsLoaded, mergeViewWindows, firstUnloadedCell } from "@/lib/workbook-window";
import { normalizeRelativePath, versionStoreKey, type WorkspaceFileRef } from "@/lib/workspace-file-ref";
import { activateWorkbookSheet } from "@/lib/excel-univer-lifecycle";
import {
  ensureHistoryRibbonStyle,
  ensureRibbonCommandHost,
  findRibbonToolbar,
  readNativeRibbonTab,
  removeRibbonCommandHost,
  setHistoryRibbonMode,
  setRibbonToolbarHidden,
  buildGridAskPrompt,
  type NativeRibbonTab,
} from "@/lib/excel-ribbon-actions";
import { getUniverModules } from "@/lib/univer-modules";
import type { WorkbookViewState } from "@/stores/workbook-conversation-store";
import { registerAgentContextMenu, type AgentMenuSelection } from "@/lib/excel-agent-menu";
import { useWorkbookWorkflowStore } from "@/stores/workbook-workflow-store";
import { captureWorkbookParameters, workbookCommandRange, workbookOperationKind } from "@/lib/workbook-handoff";

export { prefetchUniverModules, warmUniverModules } from "@/lib/univer-modules";

type CommandEvent = IEventParamConfig["CommandExecuted"];

interface UniverSheetProps {
  fileUrl: string;
  fileRef?: WorkspaceFileRef | null;
  sessionId?: string | null;
  viewGeneration?: number;
  highlightCells?: string[];
  onCellEdit?: (cell: string, value: unknown, sheet?: string) => void;
  initialSheet?: string;
  selectionMode?: boolean;
  onRangeSelected?: (range: string, sheet: string, cellValue?: string, contentVersion?: string) => void;
  withStyles?: boolean;
  /** 对比视图等只读场景：禁止编辑且不写回 */
  readOnly?: boolean;
  /** 注入 Univer 功能区 tablist（历史 / 操作 / 关闭） */
  ribbonSlot?: ReactNode;
  /** 与 开始 / 公式 / 数据 互斥：选中时隐藏原生命令栏 */
  historyActive?: boolean;
  /** 点到 Univer 自带的 开始 / 公式 / 数据 时回调 */
  onNativeRibbonTab?: () => void;
  active?: boolean;
  focused?: boolean;
  onViewState?: (view: WorkbookViewState) => void;
  linkedSelection?: { sequence: number; sheet: string; range: string };
  /** Let short split panes retain their sheet tabs inside the assigned height. */
  fitContainer?: boolean;
}

function createPreviewWorkbookId(): string {
  return `workbook-preview-${Math.random().toString(36).slice(2, 10)}`;
}

const LOADING_SHEET_ID = "__excelmanus_loading__";
// The first request only needs enough cells to paint the opening viewport.
// The actual visible window is fetched after Univer knows the container size;
// asking for the legacy 10k-cell default here made large workbooks feel frozen.
const INITIAL_VIEW_RECT = "A1:Z80";

function loadingFileKey(file: WorkspaceFileRef | null | undefined, path: string) {
  return `${file?.workspaceKey || "_"}|${path.replace(/^\.\//, "")}`;
}

function isDuplicateUnitIdError(err: unknown): boolean {
  const message = err instanceof Error ? err.message : String(err);
  return message.includes("cannot create a unit with the same unit id");
}

/**
 * Parse a path query param from a URL like
 * `http://host/api/v1/files/excel?path=./uploads/foo.xlsx`
 */
function extractPathFromUrl(url: string): string {
  try {
    const u = new URL(url, window.location.origin);
    return u.searchParams.get("path") || "";
  } catch {
    return "";
  }
}

/** Check whether a path refers to the onboarding demo file. */
function isDemoPath(path: string): boolean {
  return isDemoExcelPath(path);
}

function stringifyCellValue(value: unknown): string | undefined {
  if (value == null) return undefined;
  if (typeof value === "string") {
    const text = value.replace(/\s+/g, " ").trim();
    return text || undefined;
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? String(value) : undefined;
  }
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  if (typeof value === "object") {
    const rec = value as { toPlainText?: () => string; v?: unknown };
    if (typeof rec.toPlainText === "function") {
      return stringifyCellValue(rec.toPlainText());
    }
    if ("v" in rec) return stringifyCellValue(rec.v);
  }
  return undefined;
}

type RangeValueReader = {
  getDisplayValue?: () => unknown;
  getValue?: () => unknown;
};

function readCellValueFromRange(range: RangeValueReader): string | undefined {
  try {
    if (typeof range.getDisplayValue === "function") {
      const display = stringifyCellValue(range.getDisplayValue());
      if (display) return display;
    }
  } catch {
    /* Facade 无 getDisplayValue 或调用失败 */
  }
  try {
    if (typeof range.getValue === "function") {
      return stringifyCellValue(range.getValue());
    }
  } catch {
    /* getValue 不可用 */
  }
  return undefined;
}

/** 仅单格：优先 FRange.getDisplayValue / getValue，失败再试 worksheet.getRange(row, col)。 */
function readSingleCellValue(
  range: RangeValueReader,
  sheet?: { getRange?: (row: number, column: number) => unknown },
  row?: number,
  col?: number,
): string | undefined {
  const fromRange = readCellValueFromRange(range);
  if (fromRange) return fromRange;
  try {
    if (sheet && typeof sheet.getRange === "function" && row != null && col != null) {
      const cell = sheet.getRange(row, col);
      if (cell && typeof cell === "object") {
        return readCellValueFromRange(cell as RangeValueReader);
      }
    }
  } catch {
    /* worksheet cell 读取不可用 */
  }
  return undefined;
}

function rememberSnapshotVersion(filePath: string, version: unknown, workspaceKey?: string | null) {
  if (!filePath || isDemoPath(filePath)) return;
  if (typeof version === "string" && version) {
    useExcelStore.getState().setContentVersion(filePath, version, workspaceKey);
  }
}

function applyWorkbookEditable(api: FUniver, readOnly: boolean) {
  try {
    api.getActiveWorkbook?.()?.setEditable?.(!readOnly);
  } catch {
    // 忽略权限 API 差异
  }
}

export function UniverSheet({ fileUrl, fileRef, sessionId, viewGeneration, highlightCells, onCellEdit, initialSheet, selectionMode, onRangeSelected, withStyles = true, readOnly = false, ribbonSlot, historyActive = false, onNativeRibbonTab, active = true, focused = true, onViewState, linkedSelection, fitContainer = false }: UniverSheetProps) {
  const focusRequest = useWorkbookFocusStore((state) => state.request);
  const onViewStateRef = useRef(onViewState);
  onViewStateRef.current = onViewState;
  const containerRef = useRef<HTMLDivElement>(null);
  const univerRef = useRef<FUniver | null>(null);
  const highlightRegistryRef = useRef<WorkbookHighlightRegistry | null>(null);
  const workbookIdRef = useRef<string>(createPreviewWorkbookId());
  const loadingShellFileRef = useRef<string | null>(null);
  const [loadingShellKey, setLoadingShellKey] = useState<string | null>(null);
  const loadVersionRef = useRef(0);
  const onCellEditRef = useRef(onCellEdit);
  const readOnlyRef = useRef(readOnly);
  const suppressEditsRef = useRef(false);
  const viewRef = useRef<WorkbookViewSnapshot | null>(null);
  const [viewContentVersion, setViewContentVersion] = useState<string | null>(null);
  const identityRef = useRef({ fileRef, sessionId, viewGeneration });
  identityRef.current = { fileRef, sessionId, viewGeneration };
  const requestRef = useRef<AbortController | null>(null);
  const windowRequestRef = useRef<AbortController | null>(null);
  const windowKeyRef = useRef("");
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const enginePromiseRef = useRef<Promise<FUniver | undefined> | null>(null);
  const normalizeSheetRef = useRef<typeof import("@univerjs/core").mergeWorksheetSnapshotWithDefault | null>(null);
  const editingRef = useRef(false);
  const styledPagesRef = useRef(new Set<string>());
  const prefetchedPagesRef = useRef(new Set<string>());
  const needsRefreshRef = useRef(false);
  const externalRefreshRef = useRef(false);
  const activeRef = useRef(active);
  activeRef.current = active;
  const focusedRef = useRef(focused);
  focusedRef.current = focused;
  const withStylesRef = useRef(withStyles);
  withStylesRef.current = withStyles;
  const sheetNamesRef = useRef(new Map<string, string>());
  const filePathRef = useRef("");
  const initialSheetRef = useRef(initialSheet);
  const change = useExcelStore((s) => fileRef ? s.workbookChanges[versionStoreKey(fileRef.relative, fileRef.workspaceKey)] : undefined);
  onCellEditRef.current = onCellEdit;
  readOnlyRef.current = readOnly;
  initialSheetRef.current = initialSheet;
  const [engineReady, setEngineReady] = useState(false);
  const [engineAttempt, setEngineAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [windowStatus, setWindowStatus] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [ribbonTablist, setRibbonTablist] = useState<HTMLElement | null>(null);
  const [nativeRibbonTab, setNativeRibbonTab] = useState<NativeRibbonTab | null>(null);
  const [ribbonCommandHost, setRibbonCommandHost] = useState<HTMLElement | null>(null);
  const onNativeRibbonTabRef = useRef(onNativeRibbonTab);
  onNativeRibbonTabRef.current = onNativeRibbonTab;

  const isMobile = useIsMobile();
  // 4 秒后自动隐藏提示条
  const [hintVisible, setHintVisible] = useState(true);
  useEffect(() => {
    if (!isMobile) return;
    const t = setTimeout(() => setHintVisible(false), 4000);
    return () => clearTimeout(t);
  }, [isMobile]);

  // ── 移动端 pointer 转 wheel 适配 ────────────────────────────────────
  // Univer 通过 WheelEvent 滚动；触摸拖拽在 canvas 上会被当作选区。
  // 移动端且非选区模式下，在捕获阶段拦截触摸指针事件，
  // 将移动转换为合成 wheel 事件并阻止指针继续传播。
  useEffect(() => {
    if (!isMobile || selectionMode) return;
    const container = containerRef.current;
    if (!container) return;

    let activeTouchPointerId: number | null = null;
    let lastX = 0;
    let lastY = 0;

    const getCanvas = () =>
      (container.querySelector('canvas[data-u-comp="render-canvas"]') as HTMLCanvasElement | null) ??
      (container.querySelector("canvas") as HTMLCanvasElement | null);

    const dispatchWheel = (
      deltaX: number,
      deltaY: number,
      clientX: number,
      clientY: number,
      sourceTarget?: EventTarget | null
    ) => {
      if (Math.abs(deltaX) <= 0.5 && Math.abs(deltaY) <= 0.5) return;
      const canvas = getCanvas();
      const wheelTarget = sourceTarget instanceof HTMLCanvasElement ? sourceTarget : canvas;
      if (!wheelTarget) return;
      wheelTarget.dispatchEvent(
        new WheelEvent("wheel", {
          deltaX,
          deltaY,
          deltaMode: WheelEvent.DOM_DELTA_PIXEL,
          clientX,
          clientY,
          bubbles: true,
          cancelable: true,
        })
      );
    };

    const supportsPointer = typeof window !== "undefined" && "PointerEvent" in window;

    if (supportsPointer) {
      const onPointerDownCapture = (e: PointerEvent) => {
        if (e.pointerType !== "touch") return;
        activeTouchPointerId = e.pointerId;
        lastX = e.clientX;
        lastY = e.clientY;
        e.preventDefault();
        e.stopPropagation();
      };

      const onPointerMoveCapture = (e: PointerEvent) => {
        if (e.pointerType !== "touch" || activeTouchPointerId !== e.pointerId) return;

        const deltaX = lastX - e.clientX;
        const deltaY = lastY - e.clientY;
        lastX = e.clientX;
        lastY = e.clientY;

        dispatchWheel(deltaX, deltaY, e.clientX, e.clientY, e.target);

        e.preventDefault();
        e.stopPropagation();
      };

      const onPointerEndCapture = (e: PointerEvent) => {
        if (e.pointerType !== "touch" || activeTouchPointerId !== e.pointerId) return;
        activeTouchPointerId = null;
        e.preventDefault();
        e.stopPropagation();
      };

      container.addEventListener("pointerdown", onPointerDownCapture, { capture: true, passive: false });
      container.addEventListener("pointermove", onPointerMoveCapture, { capture: true, passive: false });
      container.addEventListener("pointerup", onPointerEndCapture, { capture: true, passive: false });
      container.addEventListener("pointercancel", onPointerEndCapture, { capture: true, passive: false });

      return () => {
        container.removeEventListener("pointerdown", onPointerDownCapture, true);
        container.removeEventListener("pointermove", onPointerMoveCapture, true);
        container.removeEventListener("pointerup", onPointerEndCapture, true);
        container.removeEventListener("pointercancel", onPointerEndCapture, true);
      };
    }

    // 兼容旧版 iOS/WebView 无 PointerEvent 时的回退
    let trackingTouch = false;

    const onTouchStartCapture = (e: TouchEvent) => {
      if (e.touches.length !== 1) return;
      trackingTouch = true;
      lastX = e.touches[0].clientX;
      lastY = e.touches[0].clientY;
      e.preventDefault();
      e.stopPropagation();
    };

    const onTouchMoveCapture = (e: TouchEvent) => {
      if (!trackingTouch || e.touches.length !== 1) return;
      const touch = e.touches[0];
      const deltaX = lastX - touch.clientX;
      const deltaY = lastY - touch.clientY;
      lastX = touch.clientX;
      lastY = touch.clientY;

      dispatchWheel(deltaX, deltaY, touch.clientX, touch.clientY, e.target);

      e.preventDefault();
      e.stopPropagation();
    };

    const onTouchEndCapture = (e: TouchEvent) => {
      trackingTouch = false;
      e.preventDefault();
      e.stopPropagation();
    };

    container.addEventListener("touchstart", onTouchStartCapture, { capture: true, passive: false });
    container.addEventListener("touchmove", onTouchMoveCapture, { capture: true, passive: false });
    container.addEventListener("touchend", onTouchEndCapture, { capture: true, passive: false });
    container.addEventListener("touchcancel", onTouchEndCapture, { capture: true, passive: false });

    return () => {
      container.removeEventListener("touchstart", onTouchStartCapture, true);
      container.removeEventListener("touchmove", onTouchMoveCapture, true);
      container.removeEventListener("touchend", onTouchEndCapture, true);
      container.removeEventListener("touchcancel", onTouchEndCapture, true);
    };
  }, [isMobile, selectionMode]);

  const filePath = extractPathFromUrl(fileUrl);
  filePathRef.current = filePath;

  useEffect(() => {
    const path = filePathRef.current;
    const store = useExcelStore.getState();
    if (focusedRef.current && store.liveSelection && normalizeRelativePath(store.liveSelection.path) !== normalizeRelativePath(path)) {
      store.setLiveSelection(null);
    }
    return () => {
      const current = useExcelStore.getState();
      if (current.liveSelection && normalizeRelativePath(current.liveSelection.path) === normalizeRelativePath(path)) {
        current.setLiveSelection(null);
      }
    };
  }, [fileUrl]);

  /**
   * 供原生右键「交给 Agent」子菜单读取的当前选区；
   * 条件与原自建菜单一致：视图未就绪或无选区时返回 null。
   */
  const readAgentSelection = (api: FUniver): AgentMenuSelection | null => {
    if (loadingShellFileRef.current || !viewRef.current) return null;
    const selection = readActiveRange(api);
    if (!selection.sheet || !selection.range) return null;
    let formula: string | undefined;
    try {
      const ws = api.getActiveWorkbook()?.getActiveSheet();
      const r = ws?.getSelection()?.getActiveRange();
      if (r && isSingleCellSelection(selection.range)) {
        let raw: unknown = r.getFormula?.();
        if (typeof raw !== "string" || !raw) raw = r.getCellData?.()?.f;
        if (typeof raw === "string" && raw.trim()) {
          const text = raw.trim();
          formula = text.startsWith("=") ? text : `=${text}`;
        }
      }
    } catch {
      /* Facade 公式读取不可用 */
    }
    const path = filePathRef.current;
    return {
      path,
      sheet: selection.sheet,
      range: selection.range,
      version: viewRef.current?.content_version ?? useExcelStore.getState().getContentVersion(path) ?? undefined,
      formula,
    };
  };
  const readAgentSelectionRef = useRef(readAgentSelection);
  readAgentSelectionRef.current = readAgentSelection;

  const beginSuppressEdits = () => {
    suppressEditsRef.current = true;
  };
  const endSuppressEdits = () => {
    suppressEditsRef.current = false;
  };

  const prepareLoadingShell = (api: FUniver) => {
    const key = loadingFileKey(identityRef.current.fileRef, filePathRef.current);
    if (loadingShellFileRef.current === key && api.getWorkbook?.(workbookIdRef.current)) return;
    beginSuppressEdits();
    try {
      if (api.getWorkbook?.(workbookIdRef.current)) api.disposeUnit(workbookIdRef.current);
      const id = createPreviewWorkbookId();
      workbookIdRef.current = id;
      api.createWorkbook({
        id,
        sheetOrder: [LOADING_SHEET_ID],
        sheets: { [LOADING_SHEET_ID]: {
          id: LOADING_SHEET_ID, name: "正在读取…", rowCount: 200, columnCount: 50, cellData: {},
        } },
      });
      applyWorkbookEditable(api, true);
      loadingShellFileRef.current = key;
      setLoadingShellKey(key);
    } finally { endSuppressEdits(); }
  };
  const prepareLoadingShellRef = useRef(prepareLoadingShell);
  prepareLoadingShellRef.current = prepareLoadingShell;

  const applyWindow = (next: WorkbookViewSnapshot) => {
    const api = univerRef.current;
    if (!api) return;
    const wb = api?.getActiveWorkbook?.();
    if (!wb) return;
    beginSuppressEdits();
    try {
      for (const win of next.windows) {
        const sheet = wb.getSheetByName(win.sheet);
        if (!sheet) continue;
        const identity = { unitId: workbookIdRef.current, subUnitId: sheet.getSheetId() };
        const command = (id: string, params: Record<string, unknown>) => {
          // onlyLocal value mutations bypass Univer's formula dependency updates.
          // The surrounding suppressEdits guard keeps hydration out of persistence;
          // allow value patches to recalculate after refresh or version restore.
          if (api.syncExecuteCommand(id, { ...identity, ...params }, { onlyLocal: id !== "sheet.mutation.set-range-values" }) === false) {
            throw new Error("更新表格视图失败，请重试");
          }
        };
        const snapshot = sheet.getSheet().getSnapshot();
        const meta = next.sheets.find((item) => item.name === win.sheet)!;
        if (sheet.getMaxRows() < Math.max(meta.used.rows, win.rect.r1)) command("sheet.mutation.set-worksheet-row-count", { rowCount: Math.max(meta.used.rows, win.rect.r1, 200) });
        if (sheet.getMaxColumns() < Math.max(meta.used.cols, win.rect.c1)) command("sheet.mutation.set-worksheet-column-count", { columnCount: Math.max(meta.used.cols, win.rect.c1, 50) });
        command("sheet.mutation.set-range-values", { cellValue: windowCellPatch(win, snapshot.cellData, wb.getWorkbook().getStyles().toJSON()) });
        if (next.with_styles !== false) {
          const intersects = (r: IRange) => r.endRow >= win.rect.r0 - 1 && r.startRow <= win.rect.r1 - 1 && r.endColumn >= win.rect.c0 - 1 && r.startColumn <= win.rect.c1 - 1;
          const oldMerges = (snapshot.mergeData || []).filter(intersects);
          if (oldMerges.length) command("sheet.mutation.remove-worksheet-merge", { ranges: oldMerges });
          const merges = (win.merges || []).map((m) => ({ startRow: m.min_row - 1, endRow: m.max_row - 1, startColumn: m.min_col - 1, endColumn: m.max_col - 1 }));
          if (merges.length) command("sheet.mutation.add-worksheet-merge", { ranges: merges });
          const colWidth: Record<number, number | null> = {};
          const rowHeight: Record<number, number | null> = {};
          for (const c of Object.keys(snapshot.columnData || {})) if (+c >= win.rect.c0 - 1 && +c < win.rect.c1) colWidth[+c] = null;
          for (const r of Object.keys(snapshot.rowData || {})) if (+r >= win.rect.r0 - 1 && +r < win.rect.r1) rowHeight[+r] = null;
          for (const [c, width] of Object.entries(win.col_widths || {})) colWidth[letterToColIndex(c)] = width * 7.5;
          for (const [r, height] of Object.entries(win.row_heights || {})) rowHeight[+r - 1] = height / 0.75;
          const ranges = [{ startRow: win.rect.r0 - 1, endRow: win.rect.r1 - 1, startColumn: win.rect.c0 - 1, endColumn: win.rect.c1 - 1 }];
          if (Object.keys(colWidth).length) command("sheet.mutation.set-worksheet-col-width", { ranges, colWidth });
          if (Object.keys(rowHeight).length) command("sheet.mutation.set-worksheet-row-height", { ranges, rowHeight });
        }
      }
    } finally { endSuppressEdits(); }
  };

  const loadVisibleWindow = async (sheetOverride?: FWorksheet, cell?: { row: number; col: number }, force = false) => {
    const api = univerRef.current;
    const identity = identityRef.current;
    const file = identity.fileRef;
    const view = viewRef.current;
    if (!activeRef.current || !api || !file || !view || suppressEditsRef.current || isDemoPath(file.relative)) return;
    if (editingRef.current || hasPendingWorkbookEdits(file) || isWorkbookEditPaused(file)) return;
    const sheet = sheetOverride || api.getActiveWorkbook?.()?.getActiveSheet?.();
    const visible = sheet?.getVisibleRange?.();
    const name = sheet?.getSheetName?.();
    if (!name) return;
    if (externalRefreshRef.current && needsRefreshRef.current) {
      // A restored file can shrink or remove sheets. Rebuild all loaded state;
      // patching only the viewport leaves cells/merges from the previous version.
      await loadDataRef.current(api, name);
      return;
    }
    const pages = cell ? [pageForCell(cell.row, cell.col)] : pagesForViewport(visible || { startRow: 0, endRow: 30, startColumn: 0, endColumn: 10 });
    const version = useExcelStore.getState().getContentVersion(file.relative, file.workspaceKey) || undefined;
    const fileKey = versionStoreKey(file.relative, file.workspaceKey);
    const changeSequence = useExcelStore.getState().workbookChanges[fileKey]?.sequence;
    const refresh = force || needsRefreshRef.current || version !== view.content_version;
    const requestKey = `${name}|${pages.map((p) => p.address).join(";")}|${version}|${withStylesRef.current}|${refresh}`;
    if (windowKeyRef.current === requestKey) return;
    windowRequestRef.current?.abort();
    const controller = new AbortController();
    windowRequestRef.current = controller;
    windowKeyRef.current = requestKey;
    const loadVersion = loadVersionRef.current;
    const valid = () => !controller.signal.aborted && loadVersion === loadVersionRef.current
      && useExcelStore.getState().workbookChanges[fileKey]?.sequence === changeSequence
      && (!version || useExcelStore.getState().getContentVersion(file.relative, file.workspaceKey) === version)
      && !editingRef.current && !hasPendingWorkbookEdits(file) && !isWorkbookEditPaused(file);
    try {
      let expected = version;
      for (const page of pages) {
        if (!valid()) return;
        const pageKey = `${expected}|${name}|${page.address}`;
        const loaded = rangeIsLoaded(viewRef.current!, name, page.rect);
        if (!refresh && loaded && (!withStylesRef.current || styledPagesRef.current.has(pageKey))) continue;
        setWindowStatus(loaded ? "正在同步格式…" : `正在加载 ${name} · ${page.address}…`);
        const next = await fetchWorkbookView({ path: file.relative, workspaceKey: file.workspaceKey,
          workspaceId: file.workspaceId, sessionId: identity.sessionId || undefined,
          expectedVersion: expected, sheet: name, rect: page.address, withStyles: withStylesRef.current,
          signal: controller.signal });
        if (!valid()) return;
        const current = viewRef.current!;
        if (current.sheets.map((s) => s.name).join("\0") !== next.sheets.map((s) => s.name).join("\0")) {
          await loadDataRef.current(api, name);
          return;
        }
        applyWindow(next);
        viewRef.current = current.content_version === next.content_version && !needsRefreshRef.current
          ? mergeViewWindows(current, next) : next;
        setViewContentVersion(viewRef.current.content_version);
        needsRefreshRef.current = false;
        expected = next.content_version;
        rememberSnapshotVersion(file.relative, next.content_version, file.workspaceKey);
        onViewStateRef.current?.({ status: "ready", sheet: name, version: next.content_version });
        if (next.with_styles !== false) styledPagesRef.current.add(`${expected}|${name}|${page.address}`);
        setError(null);
        externalRefreshRef.current = false;
      }
      setSyncing(false);
      setWindowStatus(null);
      // One adjacent page, after visible work. It shares this request's abort
      // signal, so a new viewport always takes priority over speculation.
      const last = pages.at(-1);
      const used = viewRef.current?.sheets.find((s) => s.name === name)?.used;
      if (last && used && last.rect.r1 < used.rows && valid()) {
        const neighbour = pageForCell(last.rect.r1, last.rect.c0 - 1);
        const key = `${expected}|${name}|${neighbour.address}|${withStylesRef.current}`;
        if (!prefetchedPagesRef.current.has(key)) {
          prefetchedPagesRef.current.add(key);
          if (prefetchedPagesRef.current.size > 16) prefetchedPagesRef.current.delete(prefetchedPagesRef.current.values().next().value!);
          await fetchWorkbookView({ path: file.relative, workspaceKey: file.workspaceKey,
            workspaceId: file.workspaceId, sessionId: identity.sessionId || undefined,
            expectedVersion: expected, sheet: name, rect: neighbour.address, withStyles: withStylesRef.current,
            signal: controller.signal }).catch(() => { prefetchedPagesRef.current.delete(key); });
        }
      }
    } catch (err) {
      if (!valid() || (err instanceof Error && err.name === "AbortError")) return;
      if ((err as { code?: string }).code === "STALE_VIEW" && version) {
        // Drop only the stale read token. The next guarded window request reads
        // the live version and still refuses to replace pending user edits.
        useExcelStore.getState().notifyWorkbookChanged(file.relative, file.workspaceKey, undefined, "refresh");
        return;
      }
      if ((err as { code?: string }).code === "SHEET_NOT_FOUND") {
        await loadDataRef.current(api, null);
        return;
      }
      setError(err instanceof Error ? err.message : "加载范围失败");
    } finally {
      if (windowRequestRef.current === controller) {
        windowKeyRef.current = "";
        setWindowStatus(null);
      }
    }
  };
  const loadWindowRef = useRef(loadVisibleWindow);
  loadWindowRef.current = loadVisibleWindow;

  const emitCellEdits = (event: CommandEvent) => {
    if (suppressEditsRef.current || readOnlyRef.current || event.options?.onlyLocal) return;
    if (!event.id?.startsWith("sheet.mutation.")) return;
    const api = univerRef.current;
    const wb = api?.getActiveWorkbook?.();
    const payload = { ...(event.params || {}) };
    if (payload.unitId && payload.unitId !== workbookIdRef.current) return;
    const sheet = wb?.getSheetBySheetId?.(payload.subUnitId);
    const name = sheet?.getSheetName?.() || sheetNamesRef.current.get(payload.subUnitId);
    if (sheet && name) sheetNamesRef.current.set(payload.subUnitId, name);
    if (payload.cellValue) {
      const styles = wb?.getSnapshot?.()?.styles || {};
      payload.cellValue = Object.fromEntries(Object.entries(payload.cellValue).map(([r, row]) => [r,
        Object.fromEntries(Object.entries((row || {}) as Record<string, ICellData | null>).map(([c, raw]): [string, ICellData | null] => {
          if (!raw) return [c, raw];
          const cell = { ...raw };
          const existing = sheet?.getRange?.(Number(r), Number(c))?.getCellData?.();
          if (existing?.f && !("f" in cell) && "v" in cell) delete cell.v;
          if (typeof cell.s === "string") cell.s = styles[cell.s];
          return [c, cell];
        })),
      ]));
    }
    const ops = extractWorkbookOpsFromMutation({ id: event.id, params: payload, sheet: name });
    const identity = identityRef.current;
    if (!ops.length || !identity.fileRef) return;
    const reportError = (message: string) => {
      const current = identityRef.current.fileRef;
      if (current?.workspaceKey === identity.fileRef?.workspaceKey && current?.relative === identity.fileRef?.relative) setError(message);
    };
    enqueueWorkbookCommand({ path: identity.fileRef.relative, operations: ops, file: identity.fileRef,
      sessionId: identity.sessionId, viewGeneration: identity.viewGeneration ?? useExcelStore.getState().viewGeneration,
      expectedVersion: viewRef.current?.content_version ?? null,
      onConflict: () => reportError("文件已被修改，本次编辑尚未保存。请核对双方修改并合并，或让 Agent 重新规划。"),
      onError: reportError,
    });
  };
  const emitMutationRef = useRef(emitCellEdits);
  emitMutationRef.current = emitCellEdits;

  const loadData = useCallback(
    async (providedApi?: FUniver, preferredSheet?: string | null) => {
      const { fileRef, sessionId, viewGeneration } = identityRef.current;
      if (!filePath) {
        setError("无法解析文件路径");
        setLoading(false);
        return;
      }
      if (!isDemoPath(filePath) && (!fileRef?.workspaceKey || (!sessionId && !fileRef.workspaceId))) {
        setError("无法确定工作区，请从会话重新打开文件");
        setLoading(false);
        return;
      }

      const loadVersion = ++loadVersionRef.current;
      const generation = viewGeneration ?? useExcelStore.getState().viewGeneration;
      const fileKey = fileRef ? versionStoreKey(fileRef.relative, fileRef.workspaceKey) : "";
      const changeSequence = useExcelStore.getState().workbookChanges[fileKey]?.sequence;
      requestRef.current?.abort();
      windowRequestRef.current?.abort();
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
      const controller = new AbortController();
      requestRef.current = controller;
      const previousView = viewRef.current;
      const sameFile = Boolean(previousView && fileRef && viewMatchesLease(previousView, fileRef));
      const previousSheet = providedApi?.getActiveWorkbook?.()?.getActiveSheet?.();
      const position = sameFile ? previousSheet?.getVisibleRange?.() : null;
      const selection = sameFile ? previousSheet?.getSelection?.()?.getActiveRange?.()?.getRange?.() : null;
      const reportView = onViewStateRef.current;
      try {
        reportView?.({ status: "loading" });
        setLoading(!sameFile);
        setSyncing(sameFile);
        setError(null);
        if (!sameFile && univerRef.current) prepareLoadingShellRef.current(univerRef.current);

        const view = isDemoPath(filePath)
          ? demoWorkbookView(filePath)
          : await fetchWorkbookView({
              path: filePath,
              workspaceKey: fileRef!.workspaceKey,
              sessionId: sessionId ?? undefined,
              workspaceId: fileRef?.workspaceId,
              sheet: preferredSheet === null ? undefined : preferredSheet || initialSheetRef.current,
              rect: position ? pageForCell(position.startRow, position.startColumn).address : INITIAL_VIEW_RECT,
              withStyles: false,
              viewGeneration: generation,
              signal: controller.signal,
            });
        const api = providedApi || await enginePromiseRef.current;
        if (!api || controller.signal.aborted || loadVersion !== loadVersionRef.current) return;
        const latestChange = useExcelStore.getState().workbookChanges[fileKey];
        if (latestChange?.sequence !== changeSequence && latestChange?.version !== view.content_version) {
          await loadDataRef.current(api, preferredSheet);
          return;
        }
        if (
          (identityRef.current.viewGeneration ?? useExcelStore.getState().viewGeneration) !== generation
        ) {
          return;
        }
        if (!isDemoPath(filePath) && fileRef && !viewMatchesLease(view, fileRef)) {
          return;
        }

        viewRef.current = view;
        setViewContentVersion(view.content_version);
        styledPagesRef.current.clear();
        prefetchedPagesRef.current.clear();
        needsRefreshRef.current = false;
        externalRefreshRef.current = false;
        rememberSnapshotVersion(filePath, view.content_version, fileRef?.workspaceKey);

        if (!view.sheets.length) {
          setError("文件无工作表");
          reportView?.({ status: "error", error: "文件无工作表" });
          setLoading(false);
          setSyncing(false);
          return;
        }

        beginSuppressEdits();
        const previousWorkbookId = workbookIdRef.current;
        const shellMatches = loadingShellFileRef.current === loadingFileKey(fileRef, filePath)
          && api.getWorkbook?.(previousWorkbookId)?.getSheetBySheetId?.(LOADING_SHEET_ID);
        if (shellMatches) {
          // Keep the native ribbon, formula bar, grid and footer mounted while
          // replacing only the explicitly empty loading worksheet.
          const data = viewSnapshotToUniver(view, previousWorkbookId) as {
            sheetOrder: string[]; sheets: Record<string, Record<string, unknown>>;
          };
          for (const [index, id] of data.sheetOrder.entries()) {
            if (!api.syncExecuteCommand("sheet.mutation.insert-sheet", {
              unitId: previousWorkbookId, index, sheet: normalizeSheetRef.current!(data.sheets[id]),
            }, { onlyLocal: true })) throw new Error("载入工作表失败");
          }
          activateWorkbookSheet(api, view.windows[0]?.sheet || view.sheets[0].name);
          if (!api.syncExecuteCommand("sheet.mutation.remove-sheet", {
            unitId: previousWorkbookId, subUnitId: LOADING_SHEET_ID, subUnitName: "正在读取…",
          }, { onlyLocal: true })) throw new Error("载入工作表失败");
        } else {
          try {
            if (api.getWorkbook?.(previousWorkbookId)) api.disposeUnit?.(previousWorkbookId);
          } catch {
            // 忽略过期的 workbook 清理错误
          }
          let workbookId = createPreviewWorkbookId();
          workbookIdRef.current = workbookId;
          let workbookData = viewSnapshotToUniver(view, workbookId);
          try {
            api.createWorkbook(workbookData);
          } catch (createErr) {
            if (!isDuplicateUnitIdError(createErr)) throw createErr;
            workbookId = createPreviewWorkbookId();
            workbookIdRef.current = workbookId;
            workbookData = viewSnapshotToUniver(view, workbookId);
            api.createWorkbook(workbookData);
          }
        }
        loadingShellFileRef.current = null;
        setLoadingShellKey(null);
        if (loadVersion !== loadVersionRef.current) return;

        sheetNamesRef.current.clear();
        for (const meta of view.sheets) sheetNamesRef.current.set(`sheet-${meta.name}`, meta.name);
        applyWorkbookEditable(api, readOnlyRef.current);
        activateWorkbookSheet(api, (preferredSheet === null ? undefined : preferredSheet || initialSheetRef.current) || view.active_sheet || view.windows[0]?.sheet);
        const nextSheet = api.getActiveWorkbook()?.getActiveSheet?.();
        if (position) nextSheet?.scrollToCell?.(position.startRow, position.startColumn);
        if (selection) nextSheet?.setActiveRange?.(nextSheet.getRange(selection.startRow, selection.startColumn,
          selection.endRow - selection.startRow + 1, selection.endColumn - selection.startColumn + 1));

        if (loadVersion !== loadVersionRef.current) return;

        setLoading(false);
        setSyncing(false);
        reportView?.({ status: "ready", sheet: nextSheet?.getSheetName?.() || view.active_sheet || view.windows[0]?.sheet, version: view.content_version });
        if (fileRef && isWorkbookEditPaused(fileRef)) {
          setError("此文件仍有未保存的编辑草稿，可先导出草稿，再重新加载核对。");
        }
        endSuppressEdits();
        // Yield a paint with real values before the style pass starts.
        requestAnimationFrame(() => {
          if (loadVersion === loadVersionRef.current) void loadWindowRef.current();
        });
      } catch (err) {
        if (controller.signal.aborted || loadVersion !== loadVersionRef.current) return;
        if ((err as { code?: string }).code === "SHEET_NOT_FOUND" && preferredSheet !== null) {
          await loadDataRef.current(providedApi, null);
          return;
        }
        console.error("Error loading Excel data:", err);
        reportView?.({ status: "error", error: err instanceof Error ? err.message : "加载失败" });
        setError(err instanceof Error ? err.message : "加载失败");
        setLoading(false);
        setSyncing(false);
      } finally {
        if (loadVersion === loadVersionRef.current) endSuppressEdits();
        if (requestRef.current === controller) requestRef.current = null;
      }
    },
    [filePath]
  );

  const loadDataRef = useRef(loadData);
  loadDataRef.current = loadData;

  useEffect(() => {
    if (!containerRef.current) return;

    let disposed = false;
    let api: FUniver | null = null;
    let unsubscribeValueChanged: (() => void) | null = null;

    const init = async () => {
      try {
        const { createUniver, LocaleType, UniverSheetsCorePreset, sheetsCoreZhCN, mergeWorksheetSnapshotWithDefault, IMarkSelectionService } =
          await getUniverModules();
        normalizeSheetRef.current = mergeWorksheetSnapshotWithDefault;

        if (disposed || !containerRef.current) return;

        const { univer, univerAPI } = createUniver({
          locale: LocaleType.ZH_CN,
          locales: {
            [LocaleType.ZH_CN]: sheetsCoreZhCN,
          },
          presets: [
            UniverSheetsCorePreset({
              container: containerRef.current,
              ribbonType: "classic",
              footer: {
                sheetBar: true,
                statisticBar: false,
                menus: false,
                zoomSlider: false,
              },
            }),
          ],
        });

        if (disposed) {
          univerAPI.dispose();
          return;
        }

        api = univerAPI;
        univerRef.current = univerAPI;
        // The mark service is registered when the first workbook starts rendering.
        highlightRegistryRef.current = { getShapeMap: () => univer.__getInjector().get(IMarkSelectionService).getShapeMap() };
        try {
          registerAgentContextMenu(univerAPI, {
            readSelection: () => readAgentSelectionRef.current(univerAPI),
            onReference: (sel) => useExcelStore.getState().confirmSelection({
              filePath: sel.path, sheet: sel.sheet, range: sel.range,
              contentVersion: sel.version ?? undefined,
            }),
            onAsk: (kind, sel) => {
              const { fileRef: file, sessionId } = identityRef.current;
              if (kind === "clean-selection" && file && sessionId) {
                useWorkbookWorkflowStore.getState().openHandoff({ file, sessionId, operation: "clean-selection", sheet: sel.sheet, range: sel.range, version: sel.version ?? undefined });
              } else useExcelStore.getState().setPendingTemplateMessage(buildGridAskPrompt(kind, sel));
            },
          });
        } catch (error) {
          console.warn("Agent context menu registration skipped:", error);
        }
        const subscriptions: IDisposable[] = [];
        subscriptions.push(univerAPI.addEvent(univerAPI.Event.CommandExecuted, (event) => {
          emitMutationRef.current(event);
          const view = viewRef.current;
          const file = identityRef.current.fileRef;
          if (view && file && viewMatchesLease(view, file) && !requestRef.current && !suppressEditsRef.current) onViewStateRef.current?.({
            status: "ready", version: view.content_version,
            sheet: univerAPI.getActiveWorkbook()?.getActiveSheet?.()?.getSheetName?.(),
          });
        }));
        let windowTimer: ReturnType<typeof setTimeout> | undefined;
        const requestWindow = (event: { worksheet?: FWorksheet; activeSheet?: FWorksheet }) => {
          if (windowTimer) clearTimeout(windowTimer);
          windowTimer = setTimeout(() => { void loadWindowRef.current(event?.worksheet || event?.activeSheet); }, 100);
        };
        subscriptions.push(univerAPI.addEvent(univerAPI.Event.Scroll, requestWindow));
        subscriptions.push(univerAPI.addEvent(univerAPI.Event.SelectionChanged, (event) => {
          requestWindow(event);
          const view = viewRef.current;
          if (!view || !identityRef.current.fileRef || !viewMatchesLease(view, identityRef.current.fileRef)
            || requestRef.current || loadingShellFileRef.current || suppressEditsRef.current) return;
          const selection = readActiveRange(univerAPI);
          if (activeRef.current && focusedRef.current && selection.sheet && selection.range) {
            useExcelStore.getState().setLiveSelection({
              path: filePathRef.current,
              sheet: selection.sheet,
              range: selection.range,
              contentVersion: view.content_version,
            });
          }
          onViewStateRef.current?.({
            status: "ready", version: view.content_version,
            sheet: selection.sheet, range: selection.range,
          });
        }));
        subscriptions.push(univerAPI.addEvent(univerAPI.Event.ActiveSheetChanged, requestWindow));
        subscriptions.push(univerAPI.addEvent(univerAPI.Event.SheetEditStarted, () => { editingRef.current = true; }));
        subscriptions.push(univerAPI.addEvent(univerAPI.Event.SheetEditEnded, (event) => {
          editingRef.current = false;
          requestWindow(event);
        }));
        subscriptions.push(univerAPI.addEvent(univerAPI.Event.BeforeSheetEditStart, (event) => {
          if (!activeRef.current || !focusedRef.current || loadingShellFileRef.current || !viewRef.current) event.cancel = true;
        }));
        // Univer emits undo/redo through separate events, not BeforeCommandExecute.
        for (const name of [univerAPI.Event.BeforeUndo, univerAPI.Event.BeforeRedo]) {
          subscriptions.push(univerAPI.addEvent(name, (event) => {
            if (!activeRef.current || !focusedRef.current || readOnlyRef.current || loadingShellFileRef.current || !viewRef.current) event.cancel = true;
          }));
        }
        unsubscribeValueChanged = () => {
          if (windowTimer) clearTimeout(windowTimer);
          for (const sub of subscriptions) sub?.dispose?.();
        };
        try {
          const before = univerAPI.Event?.BeforeCommandExecute;
          if (before && typeof univerAPI.addEvent === "function") {
            subscriptions.push(univerAPI.addEvent(before, (evt) => {
              if (suppressEditsRef.current || evt.options?.onlyLocal) return;
              // Each pane owns an engine. Global keyboard shortcuts must never
              // execute a user command in a background/hidden pane.
              const id = String(evt?.id || "");
              if (id.startsWith("sheet.command.") && (!activeRef.current || !focusedRef.current)) {
                evt.cancel = true;
                return;
              }
              if (id.startsWith("sheet.mutation.") && (loadingShellFileRef.current || !viewRef.current)) {
                evt.cancel = true;
                return;
              }
              const file = identityRef.current.fileRef;
              if (id.startsWith("sheet.mutation.") && file && isWorkbookEditPaused(file)) {
                evt.cancel = true;
                setError("更改尚未保存，请重新加载后继续编辑");
                return;
              }
              if (id.startsWith("sheet.mutation.") && id !== "sheet.mutation.set-range-values" && externalRefreshRef.current) {
                evt.cancel = true;
                setWindowStatus("正在同步最新版本，请稍后编辑");
                return;
              }
              if (isUnsupportedWorkbookMutation(id)) {
                evt.cancel = true;
                const { sessionId } = identityRef.current;
                if (!file || !sessionId || !viewRef.current || readOnlyRef.current) return;
                try {
                  const selection = readActiveRange(univerAPI);
                  const commandSheet = univerAPI.getActiveWorkbook()?.getSheetBySheetId(evt.params?.subUnitId)?.getSheetName();
                  useWorkbookWorkflowStore.getState().openHandoff({ file, sessionId,
                    operation: workbookOperationKind(id), sheet: commandSheet ?? selection.sheet,
                    range: workbookCommandRange(evt.params, selection.range), version: viewRef.current.content_version,
                    parameters: { command: id, captured: captureWorkbookParameters(evt.params) } });
                } catch (error) { setError(error instanceof Error ? error.message : "无法收集操作参数，请重试"); }
                return;
              }
              if (!id.startsWith("sheet.mutation.")) return;
              if (id !== "sheet.mutation.set-range-values" && !id.includes("worksheet-merge")) return;
              const wb = univerAPI.getActiveWorkbook?.();
              const sheet = wb?.getSheetBySheetId?.(evt.params?.subUnitId);
              const name = sheet?.getSheetName?.();
              const view = viewRef.current;
              if (!view || !sheet || !name) return;
              const matrix = evt.params?.cellValue || {};
              const range = evt.params?.range;
              const ranges: IRange[] = evt.params?.ranges || (range && typeof range === "object" ? [range] : []);
              const positions = ranges.map((r) => ({ r0: r.startRow + 1, c0: r.startColumn + 1, r1: r.endRow + 1, c1: r.endColumn + 1 }));
              for (const [r, row] of Object.entries(matrix)) for (const c of Object.keys((row || {}) as object)) positions.push({ r0: Number(r) + 1, c0: Number(c) + 1, r1: Number(r) + 1, c1: Number(c) + 1 });
              const missing = positions.map((r) => firstUnloadedCell(view, name, r)).find(Boolean);
              if (missing) {
                evt.cancel = true;
                setWindowStatus("该区域正在加载，请加载完成后重新执行操作");
                void loadWindowRef.current(sheet, missing);
                return;
              }
              if (withStylesRef.current && view.with_styles === false && !isDemoPath(filePathRef.current)) {
                evt.cancel = true;
                setWindowStatus("正在补齐格式和合并区域，请稍后编辑");
                void loadWindowRef.current(sheet);
              }
            }));
          }
        } catch {
          /* 该版本无 BeforeCommandExecute */
        }
        if (activeRef.current) prepareLoadingShellRef.current(univerAPI);
        setEngineReady(true);
        return univerAPI;
      } catch (err) {
        console.error("Univer initialization error:", err);
        setError("Univer 引擎初始化失败");
        onViewStateRef.current?.({ status: "error", error: "表格引擎加载失败，请重新打开" });
        setLoading(false);
      }
    };

    enginePromiseRef.current = init();

    return () => {
      disposed = true;
      loadVersionRef.current += 1;
      requestRef.current?.abort();
      requestRef.current = null;
      windowRequestRef.current?.abort();
      unsubscribeValueChanged?.();
      if (api) {
        try {
          api.dispose();
        } catch {
          // 忽略 dispose 错误
        }
      }
      univerRef.current = null;
      highlightRegistryRef.current = null;
      loadingShellFileRef.current = null;
      setLoadingShellKey(null);
      setEngineReady(false);
    };
  }, [engineAttempt]);

  useEffect(() => {
    const boundFile = identityRef.current.fileRef;
    viewRef.current = null;
    setViewContentVersion(null);
    needsRefreshRef.current = false;
    if (activeRef.current) void loadData();
    return () => {
      requestRef.current?.abort();
      windowRequestRef.current?.abort();
      loadVersionRef.current += 1;
      if (boundFile) void flushWorkbookEdits(boundFile);
    };
  }, [filePath, fileRef?.workspaceKey, fileRef?.workspaceId, sessionId, viewGeneration, engineAttempt, loadData]);

  useEffect(() => {
    if (!engineReady || loading || !univerRef.current) return;
    activateWorkbookSheet(univerRef.current, initialSheet);
  }, [engineReady, loading, initialSheet]);

  // Optional cross-pane navigation. It is deliberately view-only: linked panes
  // never write or change the focused conversation target.
  useEffect(() => {
    if (!linkedSelection || !active || loading || !univerRef.current) return;
    const workbook = univerRef.current.getActiveWorkbook();
    const sheet = workbook?.getSheetByName(linkedSelection.sheet);
    if (!sheet) return;
    try {
      activateWorkbookSheet(univerRef.current, linkedSelection.sheet);
      const range = sheet.getRange(linkedSelection.range);
      sheet.scrollToCell(Math.max(0, range.getRow()), Math.max(0, range.getColumn()));
      sheet.setActiveRange(range);
    } catch {
      // A linked range may have been deleted; the source pane remains usable.
    }
  }, [linkedSelection, active, loading, engineReady]);

  useEffect(() => {
    if (!change || !viewRef.current) return;
    windowRequestRef.current?.abort();
    windowKeyRef.current = "";
    styledPagesRef.current.clear();
    prefetchedPagesRef.current.clear();
    needsRefreshRef.current = true;
    externalRefreshRef.current = change.source !== "local";
    if (change.source !== "local") setSyncing(true);
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    const timer = setTimeout(() => { void loadWindowRef.current(); }, 200);
    refreshTimerRef.current = timer;
    return () => clearTimeout(timer);
  }, [change]);

  useEffect(() => {
    if (!active) {
      requestRef.current?.abort();
      requestRef.current = null;
      windowRequestRef.current?.abort();
      windowKeyRef.current = "";
      const file = identityRef.current.fileRef;
      if (file) void flushWorkbookEdits(file);
      return;
    }
    if (!viewRef.current && !requestRef.current) void loadDataRef.current();
    else if (viewRef.current) void loadWindowRef.current();
  }, [active]);

  useEffect(() => {
    if (!viewRef.current) return;
    styledPagesRef.current.clear();
    needsRefreshRef.current = true;
    void loadWindowRef.current(undefined, undefined, true);
  }, [withStyles]);

  useEffect(() => {
    const update = () => {
      const file = identityRef.current.fileRef;
      const pending = Boolean(file && hasPendingWorkbookEdits(file));
      setSaving(pending);
      if (!pending) {
        if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = setTimeout(() => { void loadWindowRef.current(); }, 200);
      }
    };
    const flush = () => {
      const file = identityRef.current.fileRef;
      if (file) void flushWorkbookEdits(file);
    };
    const unsubscribe = subscribeWorkbookEdits(update);
    const beforeUnload = (event: BeforeUnloadEvent) => {
      const file = identityRef.current.fileRef;
      if (file && (hasPendingWorkbookEdits(file) || isWorkbookEditPaused(file))) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("blur", flush);
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("visibilitychange", flush);
    return () => {
      unsubscribe();
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
      window.removeEventListener("blur", flush);
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("visibilitychange", flush);
    };
  }, []);

  useEffect(() => {
    if (loading || !univerRef.current) return;
    applyWorkbookEditable(univerRef.current, readOnly);
  }, [readOnly, loading]);

  // Highlights are overlays and are disposed on navigation; they never change saved formatting.
  useEffect(() => {
    if (!highlightCells?.length || !univerRef.current || !active || loading) return;
    const sheet = univerRef.current.getActiveWorkbook()?.getActiveSheet();
    if (!sheet) return;
    const overlay = highlightWorkbookRanges(sheet, highlightCells.slice(0, 64), "changed", highlightRegistryRef.current ? {
      registry: highlightRegistryRef.current,
      isActive: () => univerRef.current?.getActiveWorkbook()?.getActiveSheet()?.getSheetId() === sheet.getSheetId(),
    } : undefined);
    return () => overlay.dispose();
  }, [highlightCells, active, loading, engineReady]);

  useEffect(() => {
    const api = univerRef.current;
    const file = identityRef.current.fileRef;
    if (!focusRequest || !api || !file || !active || loading || !viewRef.current
      || !viewMatchesLease(viewRef.current, file)
      || (!focusRequest.persistent && useWorkbookFocusStore.getState().completedId === focusRequest.id)
      || focusRequest.file.workspaceKey !== file.workspaceKey
      || normalizeRelativePath(focusRequest.file.relative) !== normalizeRelativePath(file.relative)) return;
    let cancelled = false;
    let overlay: { dispose: () => void } | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const focusVersion = () => focusRequest.persistent ? focusRequest.version : acknowledgedSelectionVersion(file, focusRequest.version);
    const unsubscribe = useExcelStore.subscribe((next, previous) => {
      const key = versionStoreKey(file.relative, file.workspaceKey);
      if (next.workbookChanges[key]?.sequence !== previous.workbookChanges[key]?.sequence && overlay) {
        overlay.dispose();
        overlay = undefined;
        cancelled = true;
        setWindowStatus("表格已更新，区域高亮已移除，请核对当前版本");
      }
    });
    const focus = async () => {
      try {
        if (focusRequest.version && focusVersion() !== viewRef.current?.content_version) {
          setWindowStatus("引用来自旧版本，请核对当前表格后重新选择区域");
          useWorkbookFocusStore.getState().complete(focusRequest.id);
          return;
        }
        const workbook = api.getActiveWorkbook();
        const sheet = focusRequest.sheet ? workbook?.getSheetByName(focusRequest.sheet) : workbook?.getActiveSheet();
        if (!sheet) { setWindowStatus("引用的工作表已不存在，请核对当前版本"); return; }
        activateWorkbookSheet(api, sheet.getSheetName());
        const range = sheet.getRange(focusRequest.ranges[0]);
        const row = Math.max(0, range.getRow());
        const col = Math.max(0, range.getColumn());
        sheet.scrollToCell(row, col);
        await loadWindowRef.current(sheet, { row, col });
        if (cancelled || (!focusRequest.persistent && useWorkbookFocusStore.getState().completedId === focusRequest.id)
          || api.getActiveWorkbook()?.getActiveSheet()?.getSheetName() !== sheet.getSheetName()) return;
        if (focusRequest.version && focusVersion() !== viewRef.current?.content_version) {
          setWindowStatus("表格版本已变化，请核对后重新选择区域");
          return;
        }
        if (focusRequest.select !== false) sheet.setActiveRange(range);
        overlay = highlightWorkbookRanges(sheet, focusRequest.ranges, focusRequest.stage, highlightRegistryRef.current ? {
          registry: highlightRegistryRef.current,
          isActive: () => univerRef.current === api && api.getActiveWorkbook()?.getActiveSheet()?.getSheetId() === sheet.getSheetId(),
        } : undefined);
        useWorkbookFocusStore.getState().complete(focusRequest.id);
        if (!focusRequest.persistent) timer = setTimeout(() => overlay?.dispose(), 8000);
      } catch (error) {
        if (!cancelled) setWindowStatus(error instanceof Error ? error.message : "无法定位此区域，请核对工作表和版本");
      }
    };
    void focus();
    return () => { cancelled = true; unsubscribe(); if (timer) clearTimeout(timer); overlay?.dispose(); };
  }, [focusRequest, active, loading, engineReady, viewContentVersion, fileRef?.workspaceKey, fileRef?.relative]);

    // 同步 Univer 选区状态
  useEffect(() => {
    const api = univerRef.current;
    if (!api) return;
    try {
      const wb = api.getActiveWorkbook();
      if (!wb) return;
      // 保持启用；移动端非选区模式由上方 pointer 适配器控制。
      wb.enableSelection();
    } catch { /* 忽略 */ }
  }, [selectionMode, loading]);

  // 选区模式：监听 Univer 选区变化并回传
  useEffect(() => {
    if (!selectionMode || !univerRef.current) return;
    const api = univerRef.current;
    let disposed = false;

    const extractSelection = () => {
      if (disposed || !onRangeSelected) return;
      if (loadingShellFileRef.current || !viewRef.current) return;
      try {
        const wb = api.getActiveWorkbook();
        if (!wb) return;
        const sheet = wb.getActiveSheet();
        if (!sheet) return;
        const sel = sheet.getSelection();
        if (!sel) return;
        const range = sel.getActiveRange();
        if (!range) return;

        const startRow = range.getRow();        // 0-based
        const startCol = range.getColumn();      // 0-based
        const selection = readActiveRange(api);
        if (!selection.sheet || !selection.range) {
          onRangeSelected("", "");
          return;
        }
        const isSingleCell = isSingleCellSelection(selection.range);
        const cellValue = isSingleCell
          ? readSingleCellValue(range, sheet, startRow, startCol)
          : undefined;
        onRangeSelected(selection.range, selection.sheet, cellValue, viewRef.current?.content_version);
      } catch {
        // 忽略选区读取错误
      }
    };

    // 通过 Univer 回调 API 订阅选区变化
    let unsubscribe: (() => void) | null = null;
    try {
      const sub = api.addEvent(api.Event.SelectionChanged, extractSelection);
      unsubscribe = () => sub.dispose();
    } catch {
      // The pointer handler below also covers touch selection timing.
    }

    extractSelection();

    // 回退：在容器上通过 pointerup 也捕获
    const container = containerRef.current;
    const handlePointerUp = () => {
      // 短暂延迟以便 Univer 更新内部选区状态
      setTimeout(extractSelection, 50);
    };
    container?.addEventListener("pointerup", handlePointerUp);

    return () => {
      disposed = true;
      container?.removeEventListener("pointerup", handlePointerUp);
      unsubscribe?.();
    };
  }, [selectionMode, onRangeSelected, engineReady]);

  useEffect(() => {
    const root = containerRef.current;
    if (!root) return;
    ensureHistoryRibbonStyle();
    const pick = () => {
      const el = root.querySelector('[role="tablist"]') as HTMLElement | null;
      setRibbonTablist((prev) => (prev === el ? prev : el));
      setHistoryRibbonMode(el, historyActive);
      const tab = readNativeRibbonTab(el, historyActive);
      setNativeRibbonTab((prev) => (prev === tab ? prev : tab));
      const toolbar = findRibbonToolbar(root);
      setRibbonToolbarHidden(toolbar, historyActive);
      if (!historyActive && (tab === "formula" || tab === "data")) {
        const host = ensureRibbonCommandHost(toolbar);
        setRibbonCommandHost((prev) => (prev === host ? prev : host));
      } else {
        removeRibbonCommandHost(toolbar);
        setRibbonCommandHost((prev) => (prev == null ? prev : null));
      }
    };
    pick();
    const mo = new MutationObserver(pick);
    mo.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["aria-selected"],
    });
    return () => {
      mo.disconnect();
      const toolbar = findRibbonToolbar(root);
      setRibbonToolbarHidden(toolbar, false);
      removeRibbonCommandHost(toolbar);
    };
  }, [fileUrl, loading, historyActive]);

  useEffect(() => {
    if (!ribbonTablist) return;
    const onClick = (event: Event) => {
      const tab = (event.target as HTMLElement | null)?.closest?.("[role='tab']");
      if (tab && !tab.hasAttribute("data-em-ribbon")) {
        onNativeRibbonTabRef.current?.();
      }
    };
    ribbonTablist.addEventListener("click", onClick);
    return () => ribbonTablist.removeEventListener("click", onClick);
  }, [ribbonTablist]);

  // ── 移动端选区模式：长按进入选区 ──
  const { handlers: touchGestureHandlers } = useTouchGesture({
    onLongPress: useCallback(() => {
      if (selectionMode && isMobile) {
        // 长按时 Univer 原生选区已通过 enableSelection 启用
      }
    }, [selectionMode, isMobile]),
    onSelectionEnd: useCallback(() => {
      // 选区拖拽结束后的回调（可用于未来扩展）
    }, []),
  });

  return (
    <div className={`relative w-full h-full ${fitContainer ? "min-h-0" : "min-h-[400px]"} bg-white dark:bg-gray-800`} data-workbook-loading={loading || undefined} aria-busy={loading}>
      <div
        ref={containerRef}
        className="w-full h-full bg-white dark:bg-gray-800"
        data-univer-container
        style={{ position: "relative", visibility: (viewRef.current && (isDemoPath(filePath) || !fileRef || viewMatchesLease(viewRef.current, fileRef))) || loadingShellKey === loadingFileKey(fileRef, filePath) ? "visible" : "hidden" }}
        {...(isMobile && selectionMode ? touchGestureHandlers : {})}
      />
      {/* 移动端提示：首次加载显示，4 秒后自动淡出 */}
      {isMobile && hintVisible && !selectionMode && !loading && !error && (
        <div
          className="absolute bottom-2 left-1/2 -translate-x-1/2 z-20 px-3 py-1 rounded-full bg-black/60 text-white text-[10px] pointer-events-none"
          style={{ animation: "mobile-hint-fade 4s ease-in-out forwards" }}
        >
          滑动浏览 · 点击「选区引用」按钮选取
        </div>
      )}
      {loading && (
        <div className={engineReady
          ? "absolute bottom-10 right-3 max-w-[90%] rounded border bg-background/95 px-3 py-2 text-sm text-muted-foreground shadow-sm pointer-events-none z-10"
          : "absolute inset-0 flex items-center justify-center gap-2 bg-background text-sm text-muted-foreground z-10"
        } role="status" aria-live="polite">
          <span className="truncate">{engineReady ? `正在读取 ${fileBaseName(filePath)}…` : "正在准备表格…"}</span>
        </div>
      )}
      {!loading && (windowStatus || saving || syncing) && !error && (
        <div role="status" aria-live="polite" className="absolute bottom-8 right-3 max-w-[90%] rounded border bg-background/95 px-3 py-1.5 text-xs text-muted-foreground shadow-sm pointer-events-none">
          {saving ? "正在保存…" : windowStatus || "正在同步最新版本…"}
        </div>
      )}
      {error && (
        <div role="alert" className={`absolute z-20 bg-background/95 border p-3 ${viewRef.current ? "bottom-8 left-3 right-3 rounded shadow-sm" : "inset-x-3 top-24 rounded"}`}>
          <div className="flex items-center gap-3"><span className="text-sm text-destructive flex-1">{error}</span>
            {identityRef.current.fileRef && isWorkbookEditPaused(identityRef.current.fileRef) && <button className="text-sm underline shrink-0" type="button" onClick={() => {
              const { fileRef: file, sessionId } = identityRef.current;
              if (file && sessionId) useWorkbookWorkflowStore.getState().openConflict({ file, sessionId });
            }}>核对并合并</button>}
            {identityRef.current.fileRef && isWorkbookEditPaused(identityRef.current.fileRef) && (
              <button className="text-sm underline shrink-0" type="button" onClick={() => {
                const file = identityRef.current.fileRef!;
                saveBlob(new Blob([workbookEditDraft(file)], { type: "application/json" }), `${file.relative}.edits.json`);
              }}>导出编辑草稿</button>
            )}
            <button className="text-sm underline shrink-0" type="button" onClick={async () => {
              const file = identityRef.current.fileRef;
              if (file && isWorkbookEditPaused(file)) {
                if (!window.confirm("重新加载会放弃未保存的更改，是否继续？")) return;
                await flushWorkbookEdits(file);
                discardWorkbookEdits(file);
              }
              setError(null);
              if (!univerRef.current) setEngineAttempt((v) => v + 1);
              else if (file && viewRef.current) useExcelStore.getState().notifyWorkbookChanged(file.relative, file.workspaceKey, undefined, "refresh");
              else void loadDataRef.current(univerRef.current);
            }}>重新加载</button></div>
        </div>
      )}
      {ribbonSlot && ribbonTablist ? createPortal(ribbonSlot, ribbonTablist) : null}
      {nativeRibbonTab && ribbonCommandHost && filePath && !loadingShellKey
        ? createPortal(
            <ExcelRibbonCommands
              tab={nativeRibbonTab}
              filePath={filePath}
              fallbackSheet={initialSheet}
              getSelection={() => ({ ...readActiveRange(univerRef.current), version: viewRef.current?.content_version })}
            />,
            ribbonCommandHost,
          )
        : null}
      {/* 水波纹 CSS 动画 */}
      <style jsx>{`
        @keyframes mobile-hint-fade {
          0% { opacity: 0; }
          10% { opacity: 0.7; }
          75% { opacity: 0.7; }
          100% { opacity: 0; }
        }
      `}</style>
    </div>
  );
}
