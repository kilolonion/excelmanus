"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import { useExcelStore } from "@/stores/excel-store";
import { useIsMobile } from "@/hooks/use-mobile";
import { fetchAllSheetsSnapshot, type ExcelSnapshot } from "@/lib/api";

interface UniverSheetProps {
  fileUrl: string;
  highlightCells?: string[];
  onCellEdit?: (cell: string, value: unknown) => void;
  initialSheet?: string;
  selectionMode?: boolean;
  onRangeSelected?: (range: string, sheet: string) => void;
  withStyles?: boolean;
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

/**
 * Convert an ExcelSnapshot into Univer IWorkbookData.
 */
function snapshotToWorkbookData(
  snapshots: ExcelSnapshot[],
  workbookId: string
): Record<string, any> {
  const sheetMap: Record<string, any> = {};
  const sheetOrder: string[] = [];

  for (const snap of snapshots) {
    const sheetId = `sheet-${snap.sheet}`;
    sheetOrder.push(sheetId);

    const cellData: Record<number, Record<number, any>> = {};
    // 表头行（第 0 行）
    const headerRow: Record<number, any> = {};
    snap.headers.forEach((h, ci) => {
      const cell: any = { v: h };
      // 若有样式则应用
      const styleKey = `0,${ci}`;
      if ((snap as any).cell_styles?.[styleKey]) {
        cell.s = (snap as any).cell_styles[styleKey];
      }
      headerRow[ci] = cell;
    });
    cellData[0] = headerRow;

    // 数据行（第 1 行起）
    snap.rows.forEach((row, ri) => {
      const rowData: Record<number, any> = {};
      row.forEach((val, ci) => {
        if (val !== null && val !== undefined) {
          const cell: any = { v: val };
          const styleKey = `${ri + 1},${ci}`;
          if ((snap as any).cell_styles?.[styleKey]) {
            cell.s = (snap as any).cell_styles[styleKey];
          }
          rowData[ci] = cell;
        } else {
          // 即使空单元格也可能有样式（如背景色）
          const styleKey = `${ri + 1},${ci}`;
          if ((snap as any).cell_styles?.[styleKey]) {
            rowData[ci] = { v: null, s: (snap as any).cell_styles[styleKey] };
          }
        }
      });
      cellData[ri + 1] = rowData;
    });

    const colCount = Math.max(snap.headers.length, snap.column_letters.length, 26);
    const rowCount = snap.rows.length + 1 + 50; // 额外行数供编辑

    const sheetData: any = {
      id: sheetId,
      name: snap.sheet,
      cellData,
      rowCount: Math.max(rowCount, 100),
      columnCount: Math.max(colCount, 26),
    };

    // 应用合并单元格
    if ((snap as any).merged_cells?.length) {
      sheetData.mergeData = (snap as any).merged_cells.map((m: any) => ({
        startRow: m.startRow,
        startColumn: m.startColumn,
        endRow: m.endRow,
        endColumn: m.endColumn,
      }));
    }

    // 应用列宽
    if ((snap as any).column_widths) {
      const colInfo: Record<number, any> = {};
      for (const [colIdx, width] of Object.entries((snap as any).column_widths)) {
        colInfo[Number(colIdx)] = { w: (width as number) * 7.5 }; // Excel 列宽单位近似换算为像素
      }
      sheetData.columnData = colInfo;
    }

    // 应用行高
    if ((snap as any).row_heights) {
      const rowInfo: Record<number, any> = {};
      for (const [rowIdx, height] of Object.entries((snap as any).row_heights)) {
        rowInfo[Number(rowIdx)] = { h: height as number };
      }
      sheetData.rowData = rowInfo;
    }

    sheetMap[sheetId] = sheetData;
  }

  return {
    id: workbookId,
    sheets: sheetMap,
    sheetOrder,
  };
}

/**
 * 将 0-based 列索引转换为 Excel 列字母（0→A, 25→Z, 26→AA）。
 */
function colIndexToLetter(index: number): string {
  let result = "";
  let n = index;
  while (n >= 0) {
    result = String.fromCharCode((n % 26) + 65) + result;
    n = Math.floor(n / 26) - 1;
  }
  return result;
}

export function UniverSheet({ fileUrl, highlightCells, onCellEdit, initialSheet, selectionMode, onRangeSelected, withStyles = true }: UniverSheetProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const univerRef = useRef<any>(null);
  const workbookIdRef = useRef<string>(createPreviewWorkbookId());
  const loadVersionRef = useRef(0);
  const refreshCounter = useExcelStore((s) => s.refreshCounter);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  const loadData = useCallback(
    async (api: any) => {
      if (!filePath) {
        setError("无法解析文件路径");
        setLoading(false);
        return;
      }

      const loadVersion = ++loadVersionRef.current;
      try {
        setLoading(true);
        setError(null);

        // 单次请求拉取所有 sheet，避免 N 次串行 HTTP 往返
        const resp = await fetchAllSheetsSnapshot(filePath, { maxRows: 500, withStyles });
        if (loadVersion !== loadVersionRef.current) return;

        const allSnapshots: ExcelSnapshot[] = resp.all_snapshots;
        if (!allSnapshots.length) {
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

        // 每次重新加载时轮换 workbook id，避免单元冲突。
        let workbookId = createPreviewWorkbookId();
        workbookIdRef.current = workbookId;
        let workbookData = snapshotToWorkbookData(allSnapshots, workbookId);
        try {
          api.createWorkbook(workbookData);
        } catch (createErr) {
          if (!isDuplicateUnitIdError(createErr)) {
            throw createErr;
          }
          // 使用新 id 重试一次以从过期运行时状态恢复
          workbookId = createPreviewWorkbookId();
          workbookIdRef.current = workbookId;
          workbookData = snapshotToWorkbookData(allSnapshots, workbookId);
          api.createWorkbook(workbookData);
        }
        if (loadVersion !== loadVersionRef.current) return;

        // 若指定了初始 sheet 则切换过去
        if (initialSheet) {
          try {
            const wb = api.getActiveWorkbook();
            if (wb) {
              const sheets = wb.getSheets();
              const target = sheets?.find((s: any) => s.getName?.() === initialSheet);
              if (target) {
                target.activate();
              }
            }
          } catch {
            // 忽略切换 sheet 错误
          }
        }

        if (loadVersion !== loadVersionRef.current) return;

        setLoading(false);
      } catch (err: any) {
        if (loadVersion !== loadVersionRef.current) return;
        console.error("Error loading Excel data:", err);
        setError(err.message || "加载失败");
        setLoading(false);
      }
    },
    [filePath, initialSheet, withStyles]
  );

  useEffect(() => {
    if (!containerRef.current) return;

    let disposed = false;
    let api: any = null;

    const init = async () => {
      try {
        // 与 Univer 引擎加载并行预取数据
        const dataPromise = filePath
          ? fetchAllSheetsSnapshot(filePath, { maxRows: 500, withStyles }).catch(() => null)
          : Promise.resolve(null);

        const [{ createUniver, LocaleType }, { UniverSheetsCorePreset }, sheetsCoreZhCNMod] =
          await Promise.all([
            import("@univerjs/presets"),
            import("@univerjs/preset-sheets-core"),
            import("@univerjs/preset-sheets-core/locales/zh-CN"),
          ]);
        await import("@univerjs/preset-sheets-core/lib/index.css");

        if (disposed) return;

        const { univerAPI } = createUniver({
          locale: LocaleType.ZH_CN,
          locales: {
            [LocaleType.ZH_CN]: sheetsCoreZhCNMod.default,
          },
          presets: [
            UniverSheetsCorePreset({
              container: containerRef.current!,
            }),
          ],
        });

        if (disposed) {
          univerAPI.dispose();
          return;
        }

        api = univerAPI;
        univerRef.current = univerAPI;

        // 注意：此处只初始化 Univer 实例；工作簿创建与交互策略在后续流程处理。

        // 若有预取数据则使用，否则 loadData 会再次请求
        const prefetchedData = await dataPromise;
        if (prefetchedData && prefetchedData.all_snapshots?.length) {
          // 直接注入预取数据
          const loadVersion = ++loadVersionRef.current;
          try {
            const allSnapshots = prefetchedData.all_snapshots;
            let workbookId = createPreviewWorkbookId();
            workbookIdRef.current = workbookId;
            let workbookData = snapshotToWorkbookData(allSnapshots, workbookId);
            try {
              univerAPI.createWorkbook(workbookData);
            } catch (createErr) {
              if (!isDuplicateUnitIdError(createErr)) throw createErr;
              workbookId = createPreviewWorkbookId();
              workbookIdRef.current = workbookId;
              workbookData = snapshotToWorkbookData(allSnapshots, workbookId);
              univerAPI.createWorkbook(workbookData);
            }
            if (initialSheet) {
              try {
                const wb = univerAPI.getActiveWorkbook();
                if (wb) {
                  const sheets = wb.getSheets();
                  const target = sheets?.find((s: any) => s.getName?.() === initialSheet);
                  if (target) target.activate();
                }
              } catch { /* 忽略 */ }
            }
            if (loadVersion === loadVersionRef.current) setLoading(false);
          } catch {
            // 预取失败，回退到正常 loadData
            await loadData(univerAPI);
          }
        } else {
          await loadData(univerAPI);
        }
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
      if (api) {
        try {
          api.dispose();
        } catch {
          // 忽略 dispose 错误
        }
      }
      univerRef.current = null;
    };
  }, [loadData]);

  // refreshCounter 变化时重新加载（写操作之后）
  useEffect(() => {
    if (refreshCounter > 0 && univerRef.current) {
      loadData(univerRef.current);
    }
  }, [refreshCounter, loadData]);

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

        const startLetter = colIndexToLetter(startCol);
        const endLetter = colIndexToLetter(startCol + numCols - 1);
        const startRowNum = startRow + 1;  // Excel 行号为从 1 开始
        const endRowNum = startRow + numRows;

        const rangeStr = `${startLetter}${startRowNum}:${endLetter}${endRowNum}`;
        const sheetName = sheet.getName?.() || "Sheet1";
        onRangeSelected(rangeStr, sheetName);
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

  // 是否显示选区指示器
  const showSelectionIndicator = selectionMode;


  return (
    <div className="relative w-full h-full min-h-[400px] bg-white dark:bg-gray-800">
      <div
        ref={containerRef}
        className="w-full h-full bg-white dark:bg-gray-800"
        data-univer-container
        style={{ position: "relative" }}
      />
      {/* 选区模式指示（显式按钮或移动端长按） */}
      {showSelectionIndicator && (
        <div className="absolute top-0 left-0 right-0 z-20 flex items-center justify-center gap-2 py-1.5 text-xs font-medium text-white pointer-events-none" style={{ backgroundColor: "var(--em-primary)" }}>
          <span>请在表格中选择一个区域</span>
        </div>
      )}
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
          <span className="text-sm text-destructive">{error}</span>
        </div>
      )}
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
