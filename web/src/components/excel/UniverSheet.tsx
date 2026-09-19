"use client";

import { readActiveRange } from "@/lib/excel-selection";

import { useEffect, useRef, useCallback, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useExcelStore } from "@/stores/excel-store";
import { useIsMobile } from "@/hooks/use-mobile";
import { useTouchGesture } from "@/hooks/use-touch-gesture";
import { fetchWorkbookView } from "@/lib/api";
import { ExcelRibbonCommands } from "@/components/excel/ExcelRibbonCommands";
import {
  enqueueWorkbookCommand,
  hasPendingWorkbookEdits,
  extractWorkbookOpsFromMutation,
  isDemoExcelPath,
  isUnsupportedWorkbookMutation,
} from "@/lib/excel-cell-edit";
import { cellToUniver, demoWorkbookView, viewMatchesLease, viewSnapshotToUniver, type WorkbookViewSnapshot } from "@/lib/workbook-view";
import { pageForCell, rangeIsLoaded, mergeViewWindows, firstUnloadedCell } from "@/lib/workbook-window";
import type { WorkspaceFileRef } from "@/lib/workspace-file-ref";
import { activateWorkbookSheet } from "@/lib/excel-univer-lifecycle";
import {
  ensureHistoryRibbonStyle,
  ensureRibbonCommandHost,
  findRibbonToolbar,
  readNativeRibbonTab,
  removeRibbonCommandHost,
  setHistoryRibbonMode,
  setRibbonToolbarHidden,
  type NativeRibbonTab,
} from "@/lib/excel-ribbon-actions";
import { getUniverModules } from "@/lib/univer-modules";

export { prefetchUniverModules, warmUniverModules } from "@/lib/univer-modules";

interface UniverSheetProps {
  fileUrl: string;
  fileRef?: WorkspaceFileRef | null;
  sessionId?: string | null;
  viewGeneration?: number;
  highlightCells?: string[];
  onCellEdit?: (cell: string, value: unknown, sheet?: string) => void;
  initialSheet?: string;
  selectionMode?: boolean;
  onRangeSelected?: (range: string, sheet: string, cellValue?: string) => void;
  withStyles?: boolean;
  /** 对比视图等只读场景：禁止编辑且不写回 */
  readOnly?: boolean;
  /** 注入 Univer 功能区 tablist（历史 / 操作 / 关闭） */
  ribbonSlot?: ReactNode;
  /** 与 开始 / 公式 / 数据 互斥：选中时隐藏原生命令栏 */
  historyActive?: boolean;
  /** 点到 Univer 自带的 开始 / 公式 / 数据 时回调 */
  onNativeRibbonTab?: () => void;
}

function createPreviewWorkbookId(): string {
  return `workbook-preview-${Math.random().toString(36).slice(2, 10)}`;
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

function applyWorkbookEditable(api: any, readOnly: boolean) {
  try {
    api.getActiveWorkbook?.()?.setEditable?.(!readOnly);
  } catch {
    // 忽略权限 API 差异
  }
}

export function UniverSheet({ fileUrl, fileRef, sessionId, viewGeneration, highlightCells, onCellEdit, initialSheet, selectionMode, onRangeSelected, withStyles = true, readOnly = false, ribbonSlot, historyActive = false, onNativeRibbonTab }: UniverSheetProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const univerRef = useRef<any>(null);
  const workbookIdRef = useRef<string>(createPreviewWorkbookId());
  const loadVersionRef = useRef(0);
  const onCellEditRef = useRef(onCellEdit);
  const readOnlyRef = useRef(readOnly);
  const suppressEditsRef = useRef(false);
  const viewRef = useRef<WorkbookViewSnapshot | null>(null);
  const identityRef = useRef({ fileRef, sessionId, viewGeneration });
  identityRef.current = { fileRef, sessionId, viewGeneration };
  const loadedPagesRef = useRef(new Set<string>());
  const sheetNamesRef = useRef(new Map<string, string>());
  const filePathRef = useRef("");
  const initialSheetRef = useRef(initialSheet);
  const refreshCounter = useExcelStore((s) => s.refreshCounter);
  onCellEditRef.current = onCellEdit;
  readOnlyRef.current = readOnly;
  initialSheetRef.current = initialSheet;
  const [engineReady, setEngineReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
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

  const beginSuppressEdits = () => {
    suppressEditsRef.current = true;
  };
  const endSuppressEdits = () => {
    const release = () => { suppressEditsRef.current = false; };
    if (typeof window !== "undefined") {
      window.setTimeout(release, 50);
    } else {
      release();
    }
  };

  const loadVisibleWindow = async (sheetOverride?: any, cell?: { row: number; col: number }) => {
    const api = univerRef.current;
    const identity = identityRef.current;
    const file = identity.fileRef;
    const view = viewRef.current;
    if (!api || !file || !view || suppressEditsRef.current || hasPendingWorkbookEdits(file)) return;
    const sheet = sheetOverride || api.getActiveWorkbook?.()?.getActiveSheet?.();
    const visible = sheet?.getVisibleRange?.();
    const row = cell?.row ?? visible?.startRow;
    const col = cell?.col ?? visible?.startColumn;
    if (row == null || col == null) return;
    const name = sheet.getSheetName?.() || sheet.getName?.();
    const page = pageForCell(row, col);
    if (rangeIsLoaded(view, name, page.rect)) return;
    const requestKey = `${file.workspaceKey}|${file.relative}|${name}|${page.address}`;
    if (loadedPagesRef.current.has(requestKey)) return;
    loadedPagesRef.current.add(requestKey);
    const loadVersion = loadVersionRef.current;
    const version = useExcelStore.getState().getContentVersion(file.relative, file.workspaceKey) || view.content_version;
    try {
      const next = await fetchWorkbookView({ path: file.relative, workspaceKey: file.workspaceKey,
        workspaceId: file.workspaceId, sessionId: identity.sessionId || undefined,
        expectedVersion: version, sheet: name, rect: page.address, withStyles: true,
        viewGeneration: identity.viewGeneration });
      if (loadVersion !== loadVersionRef.current || identityRef.current.fileRef?.workspaceKey !== file.workspaceKey || identityRef.current.fileRef?.relative !== file.relative || hasPendingWorkbookEdits(file)) return;
      if (useExcelStore.getState().getContentVersion(file.relative, file.workspaceKey) !== version) return;
      const current = viewRef.current;
      if (!current) return;
      suppressEditsRef.current = true;
      for (const win of next.windows) {
        const target = api.getActiveWorkbook()?.getSheetByName?.(win.sheet);
        if (!target) continue;
        const cellValue: Record<number, Record<number, unknown>> = {};
        for (const [key, fact] of Object.entries(win.cells)) {
          const [r, c] = key.split(",").map(Number);
          cellValue[r - 1] ??= {};
          cellValue[r - 1][c - 1] = { ...cellToUniver(fact), custom: null };
        }
        await api.executeCommand("sheet.mutation.set-range-values", {
          unitId: workbookIdRef.current, subUnitId: target.getSheetId(), cellValue,
        }, { onlyLocal: true });
      }
      viewRef.current = mergeViewWindows({ ...current, content_version: version }, next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载范围失败");
    } finally {
      loadedPagesRef.current.delete(requestKey);
      if (loadVersion === loadVersionRef.current) endSuppressEdits();
    }
  };
  const loadWindowRef = useRef(loadVisibleWindow);
  loadWindowRef.current = loadVisibleWindow;

  const emitCellEdits = (event: any) => {
    if (suppressEditsRef.current || readOnlyRef.current || event.options?.onlyLocal) return;
    if (!event.id?.startsWith("sheet.mutation.")) return;
    const api = univerRef.current;
    const wb = api?.getActiveWorkbook?.();
    const payload = { ...(event.params || {}) };
    if (payload.unitId && payload.unitId !== workbookIdRef.current) return;
    const sheet = wb?.getSheetBySheetId?.(payload.subUnitId);
    const name = sheet?.getSheetName?.() || sheet?.getName?.() || sheetNamesRef.current.get(payload.subUnitId);
    if (sheet && name) sheetNamesRef.current.set(payload.subUnitId, name);
    if (payload.cellValue) {
      const styles = wb?.getSnapshot?.()?.styles || {};
      payload.cellValue = Object.fromEntries(Object.entries(payload.cellValue).map(([r, row]) => [r,
        Object.fromEntries(Object.entries((row || {}) as Record<string, any>).flatMap(([c, raw]) => {
          if (!raw) return [[c, raw]];
          const cell = { ...raw };
          const existing = sheet?.getRange?.(Number(r), Number(c))?.getCellData?.();
          if (existing?.f && !("f" in cell) && "v" in cell) delete cell.v;
          if (typeof cell.s === "string") cell.s = styles[cell.s];
          return [[c, cell]];
        })),
      ]));
    }
    const ops = extractWorkbookOpsFromMutation({ id: event.id, params: payload, sheet: name });
    const identity = identityRef.current;
    if (!ops.length || !identity.fileRef) return;
    enqueueWorkbookCommand({ path: identity.fileRef.relative, operations: ops, file: identity.fileRef,
      sessionId: identity.sessionId, viewGeneration: identity.viewGeneration ?? useExcelStore.getState().viewGeneration,
      onConflict: () => setError("文件版本已变化，请重新加载后编辑"),
      onError: (message) => setError(message),
    });
  };
  const emitMutationRef = useRef(emitCellEdits);
  emitMutationRef.current = emitCellEdits;

  const loadData = useCallback(
    async (api: any) => {
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
      beginSuppressEdits();
      try {
        setLoading(true);
        setError(null);

        const view = isDemoPath(filePath)
          ? demoWorkbookView(filePath)
          : await fetchWorkbookView({
              path: filePath,
              workspaceKey: fileRef!.workspaceKey,
              sessionId: sessionId ?? undefined,
              workspaceId: fileRef?.workspaceId,
              withStyles,
              viewGeneration: generation,
            });
        if (loadVersion !== loadVersionRef.current) return;
        if (
          (viewGeneration ?? useExcelStore.getState().viewGeneration) !== generation
        ) {
          return;
        }
        if (!isDemoPath(filePath) && fileRef && !viewMatchesLease(view, fileRef)) {
          return;
        }

        viewRef.current = view;
        loadedPagesRef.current.clear();
        rememberSnapshotVersion(filePath, view.content_version, fileRef?.workspaceKey);

        if (!view.sheets.length) {
          setError("文件无工作表");
          setLoading(false);
          return;
        }

        const previousWorkbookId = workbookIdRef.current;
        try {
          if (api.getWorkbook?.(previousWorkbookId)) {
            api.disposeUnit?.(previousWorkbookId);
          }
        } catch {
          // 忽略过期的 workbook 清理错误
        }

        let workbookId = createPreviewWorkbookId();
        workbookIdRef.current = workbookId;
        let workbookData = viewSnapshotToUniver(view, workbookId);
        try {
          api.createWorkbook(workbookData);
        } catch (createErr) {
          if (!isDuplicateUnitIdError(createErr)) {
            throw createErr;
          }
          workbookId = createPreviewWorkbookId();
          workbookIdRef.current = workbookId;
          workbookData = viewSnapshotToUniver(view, workbookId);
          api.createWorkbook(workbookData);
        }
        if (loadVersion !== loadVersionRef.current) return;

        sheetNamesRef.current.clear();
        for (const meta of view.sheets) sheetNamesRef.current.set(`sheet-${meta.name}`, meta.name);
        applyWorkbookEditable(api, readOnlyRef.current);
        activateWorkbookSheet(api, initialSheetRef.current);

        if (loadVersion !== loadVersionRef.current) return;

        setLoading(false);
      } catch (err: any) {
        if (loadVersion !== loadVersionRef.current) return;
        console.error("Error loading Excel data:", err);
        setError(err.message || "加载失败");
        setLoading(false);
      } finally {
        if (loadVersion === loadVersionRef.current) endSuppressEdits();
      }
    },
    [filePath, fileRef, sessionId, viewGeneration, withStyles]
  );

  const loadDataRef = useRef(loadData);
  loadDataRef.current = loadData;

  useEffect(() => {
    if (!filePath || isDemoPath(filePath) || !fileRef?.workspaceKey) return;
    if (!sessionId && !fileRef.workspaceId) return;
    void fetchWorkbookView({
      path: filePath,
      workspaceKey: fileRef.workspaceKey,
      sessionId: sessionId ?? undefined,
      workspaceId: fileRef.workspaceId,
      withStyles,
      viewGeneration: viewGeneration ?? useExcelStore.getState().viewGeneration,
    }).catch(() => null);
  }, [filePath, fileRef, sessionId, viewGeneration, withStyles]);

  useEffect(() => {
    if (!containerRef.current) return;

    let disposed = false;
    let api: any = null;
    let unsubscribeValueChanged: (() => void) | null = null;

    const init = async () => {
      try {
        const { createUniver, LocaleType, UniverSheetsCorePreset, sheetsCoreZhCN } =
          await getUniverModules();

        if (disposed || !containerRef.current) return;

        const { univerAPI } = createUniver({
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
        const subscriptions: any[] = [];
        subscriptions.push(univerAPI.addEvent(univerAPI.Event.CommandExecuted, (event: any) => emitMutationRef.current(event)));
        let windowTimer: ReturnType<typeof setTimeout> | undefined;
        const requestWindow = (event: any) => {
          if (windowTimer) clearTimeout(windowTimer);
          windowTimer = setTimeout(() => { void loadWindowRef.current(event.worksheet); }, 100);
        };
        subscriptions.push(univerAPI.addEvent(univerAPI.Event.Scroll, requestWindow));
        subscriptions.push(univerAPI.addEvent(univerAPI.Event.SelectionChanged, requestWindow));
        unsubscribeValueChanged = () => {
          if (windowTimer) clearTimeout(windowTimer);
          for (const sub of subscriptions) sub?.dispose?.();
        };
        try {
          const before = univerAPI.Event?.BeforeCommandExecute;
          if (before && typeof univerAPI.addEvent === "function") {
            subscriptions.push(univerAPI.addEvent(before, (evt: any) => {
              if (suppressEditsRef.current || evt.options?.onlyLocal) return;
              const id = String(evt?.id || "");
              if (isUnsupportedWorkbookMutation(id)) { evt.cancel = true; setError("此操作尚不能保存，请通过聊天完成"); return; }
              if (!id.startsWith("sheet.mutation.")) return;
              if (id !== "sheet.mutation.set-range-values" && !id.includes("worksheet-merge")) return;
              const wb = univerAPI.getActiveWorkbook?.();
              const sheet = wb?.getSheetBySheetId?.(evt.params?.subUnitId);
              const name = sheet?.getSheetName?.() || sheet?.getName?.();
              const view = viewRef.current;
              if (!view || !sheet) return;
              const matrix = evt.params?.cellValue || {};
              const range = evt.params?.range;
              const ranges = evt.params?.ranges || (range && typeof range === "object" ? [range] : []);
              const positions = ranges.map((r: any) => ({ r0: r.startRow + 1, c0: r.startColumn + 1, r1: r.endRow + 1, c1: r.endColumn + 1 }));
              for (const [r, row] of Object.entries(matrix)) for (const c of Object.keys((row || {}) as object)) positions.push({ r0: Number(r) + 1, c0: Number(c) + 1, r1: Number(r) + 1, c1: Number(c) + 1 });
              const missing = positions.map((r: any) => firstUnloadedCell(view, name, r)).find(Boolean);
              if (missing) { evt.cancel = true; void loadWindowRef.current(sheet, missing); }
            }));
          }
        } catch {
          /* 该版本无 BeforeCommandExecute */
        }
        setEngineReady(true);
      } catch (err) {
        console.error("Univer initialization error:", err);
        setError("Univer 引擎初始化失败");
        setLoading(false);
      }
    };

    init();

    return () => {
      disposed = true;
      loadVersionRef.current += 1;
      unsubscribeValueChanged?.();
      if (api) {
        try {
          api.dispose();
        } catch {
          // 忽略 dispose 错误
        }
      }
      univerRef.current = null;
      setEngineReady(false);
    };
  }, []);

  useEffect(() => {
    if (!engineReady || !univerRef.current) return;
    void loadData(univerRef.current);
  }, [engineReady, filePath, fileRef?.workspaceKey, withStyles, loadData]);

  useEffect(() => {
    if (!engineReady || loading || !univerRef.current) return;
    activateWorkbookSheet(univerRef.current, initialSheet);
  }, [engineReady, loading, initialSheet]);

  // refreshCounter 变化时重新加载（写操作之后）
  useEffect(() => {
    if (refreshCounter > 0 && univerRef.current) {
      void loadDataRef.current(univerRef.current);
    }
  }, [refreshCounter]);

  useEffect(() => {
    if (loading || !univerRef.current) return;
    applyWorkbookEditable(univerRef.current, readOnly);
  }, [readOnly, loading]);

  // highlightCells 变化时高亮单元格
  useEffect(() => {
    if (!highlightCells?.length || !univerRef.current) return;
    // 通过 Univer API 的高亮逻辑在此实现
  }, [highlightCells]);

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
        const numRows = range.getNumRows?.() ?? 1;
        const numCols = range.getNumColumns?.() ?? 1;

        const selection = readActiveRange(api);
        if (!selection.sheet || !selection.range) {
          onRangeSelected("", "");
          return;
        }
        const isSingleCell = numRows === 1 && numCols === 1;
        const cellValue = isSingleCell
          ? readSingleCellValue(range, sheet, startRow, startCol)
          : undefined;
        onRangeSelected(selection.range, selection.sheet, cellValue);
      } catch {
        // 忽略选区读取错误
      }
    };

    // 通过 Univer 回调 API 订阅选区变化
    let unsubscribe: (() => void) | null = null;
    try {
      const callback = api.getActiveWorkbook()?.getActiveSheet()?.onSelectionChange;
      if (typeof callback === "function") {
        const sub = callback(extractSelection);
        if (sub && typeof sub.dispose === "function") {
          unsubscribe = () => sub.dispose();
        }
      }
    } catch {
      // 回退：若无 onSelectionChange 则用 pointerup
    }

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
  }, [selectionMode, onRangeSelected]);

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
    <div className="relative w-full h-full min-h-[400px] bg-white dark:bg-gray-800">
      <div
        ref={containerRef}
        className="w-full h-full bg-white dark:bg-gray-800"
        data-univer-container
        style={{ position: "relative" }}
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
        <div className="absolute inset-0 flex items-center justify-center bg-background/60 z-10">
          <span className="text-sm text-muted-foreground animate-pulse">加载表格数据...</span>
        </div>
      )}
      {error && (
        <div className="absolute inset-0 flex items-center justify-center bg-background/80 z-10">
          <div className="flex flex-col items-center gap-3"><span className="text-sm text-destructive">{error}</span>
            <button type="button" onClick={() => { setError(null); if (univerRef.current) void loadDataRef.current(univerRef.current); }}>重新加载</button></div>
        </div>
      )}
      {ribbonSlot && ribbonTablist ? createPortal(ribbonSlot, ribbonTablist) : null}
      {nativeRibbonTab && ribbonCommandHost && filePath
        ? createPortal(
            <ExcelRibbonCommands
              tab={nativeRibbonTab}
              filePath={filePath}
              fallbackSheet={initialSheet}
              getSelection={() => readActiveRange(univerRef.current)}
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
