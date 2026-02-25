"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import { useExcelStore } from "@/stores/excel-store";
import { useIsMobile } from "@/hooks/use-mobile";
import { useTouchGesture, type GestureState } from "@/hooks/use-touch-gesture";
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
    // Header row (row 0)
    const headerRow: Record<number, any> = {};
    snap.headers.forEach((h, ci) => {
      const cell: any = { v: h };
      // Apply style if available
      const styleKey = `0,${ci}`;
      if ((snap as any).cell_styles?.[styleKey]) {
        cell.s = (snap as any).cell_styles[styleKey];
      }
      headerRow[ci] = cell;
    });
    cellData[0] = headerRow;

    // Data rows (row 1+)
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
          // Even null cells may have styles (e.g. background color)
          const styleKey = `${ri + 1},${ci}`;
          if ((snap as any).cell_styles?.[styleKey]) {
            rowData[ci] = { v: null, s: (snap as any).cell_styles[styleKey] };
          }
        }
      });
      cellData[ri + 1] = rowData;
    });

    const colCount = Math.max(snap.headers.length, snap.column_letters.length, 26);
    const rowCount = snap.rows.length + 1 + 50; // extra rows for editing

    const sheetData: any = {
      id: sheetId,
      name: snap.sheet,
      cellData,
      rowCount: Math.max(rowCount, 100),
      columnCount: Math.max(colCount, 26),
    };

    // Apply merged cells
    if ((snap as any).merged_cells?.length) {
      sheetData.mergeData = (snap as any).merged_cells.map((m: any) => ({
        startRow: m.startRow,
        startColumn: m.startColumn,
        endRow: m.endRow,
        endColumn: m.endColumn,
      }));
    }

    // Apply column widths
    if ((snap as any).column_widths) {
      const colInfo: Record<number, any> = {};
      for (const [colIdx, width] of Object.entries((snap as any).column_widths)) {
        colInfo[Number(colIdx)] = { w: (width as number) * 7.5 }; // Excel width units to pixels approx
      }
      sheetData.columnData = colInfo;
    }

    // Apply row heights
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
 * Convert a 0-based column index to Excel column letter (0→A, 25→Z, 26→AA).
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
  // Mobile gesture state: tracks whether user is in long-press selection mode
  const [mobileSelectActive, setMobileSelectActive] = useState(false);
  // Visual feedback: shows ripple at long-press point
  const [longPressPoint, setLongPressPoint] = useState<{ x: number; y: number } | null>(null);
  // Auto-hide hint pill after 4 seconds
  const [hintVisible, setHintVisible] = useState(true);
  useEffect(() => {
    if (!isMobile) return;
    const t = setTimeout(() => setHintVisible(false), 4000);
    return () => clearTimeout(t);
  }, [isMobile]);

  // Helper: extract current selection range from Univer API
  const extractAndReportSelection = useCallback(() => {
    const api = univerRef.current;
    if (!api || !onRangeSelected) return;
    try {
      const wb = api.getActiveWorkbook();
      if (!wb) return;
      const sheet = wb.getActiveSheet();
      if (!sheet) return;
      const sel = sheet.getSelection();
      if (!sel) return;
      const range = sel.getActiveRange();
      if (!range) return;

      const startRow = range.getRow();
      const startCol = range.getColumn();
      const numRows = range.getNumRows?.() ?? 1;
      const numCols = range.getNumColumns?.() ?? 1;

      const startLetter = colIndexToLetter(startCol);
      const endLetter = colIndexToLetter(startCol + numCols - 1);
      const startRowNum = startRow + 1;
      const endRowNum = startRow + numRows;

      const rangeStr = `${startLetter}${startRowNum}:${endLetter}${endRowNum}`;
      const sheetName = sheet.getName?.() || "Sheet1";
      onRangeSelected(rangeStr, sheetName);
    } catch {
      // ignore selection read errors
    }
  }, [onRangeSelected]);

  // Mobile touch gesture hook
  const { gestureState, handlers: touchHandlers } = useTouchGesture({
    longPressMs: 400,
    moveThreshold: 10,
    onLongPress: (point) => {
      // Long press detected → enable selection in Univer
      const api = univerRef.current;
      if (!api) return;
      try {
        const wb = api.getActiveWorkbook();
        wb?.enableSelection();
      } catch { /* ignore */ }
      setMobileSelectActive(true);
      setLongPressPoint(point);
      // Clear ripple after animation
      setTimeout(() => setLongPressPoint(null), 600);
    },
    onSelectionEnd: () => {
      // Selection gesture finished → extract range, then disable selection
      setTimeout(() => {
        extractAndReportSelection();
        const api = univerRef.current;
        if (api) {
          try {
            const wb = api.getActiveWorkbook();
            wb?.disableSelection();
          } catch { /* ignore */ }
        }
        setMobileSelectActive(false);
      }, 80); // small delay for Univer to update internal state
    },
    onTap: () => {
      // Quick tap on mobile — optionally select single cell
      // For now, no-op (scroll mode stays active)
    },
  });

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

        // Single request to fetch ALL sheets at once (eliminates N serial HTTP round-trips)
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
          // ignore stale workbook cleanup errors
        }

        // Rotate workbook id on every reload to avoid duplicate unit conflicts.
        let workbookId = createPreviewWorkbookId();
        workbookIdRef.current = workbookId;
        let workbookData = snapshotToWorkbookData(allSnapshots, workbookId);
        try {
          api.createWorkbook(workbookData);
        } catch (createErr) {
          if (!isDuplicateUnitIdError(createErr)) {
            throw createErr;
          }
          // One-time retry with a fresh id to recover from stale runtime state.
          workbookId = createPreviewWorkbookId();
          workbookIdRef.current = workbookId;
          workbookData = snapshotToWorkbookData(allSnapshots, workbookId);
          api.createWorkbook(workbookData);
        }
        if (loadVersion !== loadVersionRef.current) return;

        // Switch to initial sheet if specified
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
            // ignore sheet switching errors
          }
        }

        if (loadVersion !== loadVersionRef.current) return;

        // 移动端：数据 workbook 创建后立即禁用选区，确保触摸 = 滚动
        if (isMobile && !selectionMode) {
          try {
            const wb = api.getActiveWorkbook();
            wb?.disableSelection();
          } catch { /* ignore */ }
        }

        setLoading(false);
      } catch (err: any) {
        if (loadVersion !== loadVersionRef.current) return;
        console.error("Error loading Excel data:", err);
        setError(err.message || "加载失败");
        setLoading(false);
      }
    },
    [filePath, initialSheet]
  );

  useEffect(() => {
    if (!containerRef.current) return;

    let disposed = false;
    let api: any = null;

    const init = async () => {
      try {
        // Prefetch data in parallel with Univer engine loading
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

        // 注意：此处不调用 disableSelection()，因为数据 workbook 尚未创建。
        // 会在 createWorkbook() 成功后、以及 loadData() 结束后统一处理。

        // Use prefetched data if available, otherwise loadData will fetch again
        const prefetchedData = await dataPromise;
        if (prefetchedData && prefetchedData.all_snapshots?.length) {
          // Inject prefetched data directly
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
              } catch { /* ignore */ }
            }
            // 移动端：prefetch 路径创建的 workbook 也需禁用选区
            if (isMobile && !selectionMode) {
              try {
                const wb = univerAPI.getActiveWorkbook();
                wb?.disableSelection();
              } catch { /* ignore */ }
            }

            if (loadVersion === loadVersionRef.current) setLoading(false);
          } catch {
            // Prefetch path failed, fall back to normal loadData
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
          // ignore dispose errors
        }
      }
      univerRef.current = null;
    };
  }, [loadData]);

  // Reload data when refreshCounter changes (after a write operation)
  useEffect(() => {
    if (refreshCounter > 0 && univerRef.current) {
      loadData(univerRef.current);
    }
  }, [refreshCounter, loadData]);

  // Highlight cells when highlightCells changes
  useEffect(() => {
    if (!highlightCells?.length || !univerRef.current) return;
    // Highlighting logic via Univer API would go here
  }, [highlightCells]);

  // Sync Univer selection state when selectionMode prop or mobile state changes
  useEffect(() => {
    const api = univerRef.current;
    if (!api) return;
    try {
      const wb = api.getActiveWorkbook();
      if (!wb) return;
      if (selectionMode) {
        // Explicit selection mode (button toggle) — always enable
        wb.enableSelection();
      } else if (isMobile && !mobileSelectActive) {
        // Mobile default: disable selection so touch = scroll
        wb.disableSelection();
      } else if (!isMobile) {
        // Desktop: always enable selection
        wb.enableSelection();
      }
    } catch { /* ignore */ }
  }, [selectionMode, isMobile, mobileSelectActive, loading]);

  // Selection mode: listen for selection changes in Univer and report back
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
        const startRowNum = startRow + 1;  // Excel is 1-based
        const endRowNum = startRow + numRows;

        const rangeStr = `${startLetter}${startRowNum}:${endLetter}${endRowNum}`;
        const sheetName = sheet.getName?.() || "Sheet1";
        onRangeSelected(rangeStr, sheetName);
      } catch {
        // ignore selection read errors
      }
    };

    // Subscribe to selection changes via Univer callback API
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
      // Fallback: if onSelectionChange is not available, use pointerup
    }

    // Fallback: also capture via pointerup on the container
    const container = containerRef.current;
    const handlePointerUp = () => {
      // Small delay to let Univer update its internal selection state
      setTimeout(extractSelection, 50);
    };
    container?.addEventListener("pointerup", handlePointerUp);

    return () => {
      disposed = true;
      container?.removeEventListener("pointerup", handlePointerUp);
      unsubscribe?.();
    };
  }, [selectionMode, onRangeSelected]);

  // Determine if we should show selection indicator
  const showSelectionIndicator = selectionMode || (isMobile && mobileSelectActive);

  // Mobile touch handlers: only attach on mobile when NOT in explicit selection mode
  // (when selectionMode is on, Univer handles everything directly)
  const mobileTouchProps = isMobile && !selectionMode ? touchHandlers : {};

  return (
    <div className="relative w-full h-full min-h-[400px] bg-white dark:bg-gray-800">
      <div
        ref={containerRef}
        className="w-full h-full bg-white dark:bg-gray-800"
        style={{ position: "relative" }}
        {...mobileTouchProps}
      />
      {/* Selection mode indicator (explicit button or mobile long-press) */}
      {showSelectionIndicator && (
        <div className="absolute top-0 left-0 right-0 z-20 flex items-center justify-center gap-2 py-1.5 text-xs font-medium text-white pointer-events-none" style={{ backgroundColor: "var(--em-primary)" }}>
          <span>{mobileSelectActive ? "拖动选择区域，松手完成" : "请在表格中选择一个区域"}</span>
        </div>
      )}
      {/* Mobile long-press ripple feedback */}
      {longPressPoint && (
        <div
          className="absolute z-30 pointer-events-none"
          style={{
            left: longPressPoint.x - (containerRef.current?.getBoundingClientRect().left ?? 0),
            top: longPressPoint.y - (containerRef.current?.getBoundingClientRect().top ?? 0),
            transform: "translate(-50%, -50%)",
          }}
        >
          <div
            className="rounded-full"
            style={{
              width: 40,
              height: 40,
              backgroundColor: "var(--em-primary)",
              opacity: 0.3,
              animation: "mobile-select-ripple 0.6s ease-out forwards",
            }}
          />
        </div>
      )}
      {/* Mobile hint: show on first load, auto-fade after 4s */}
      {isMobile && hintVisible && !selectionMode && !loading && !error && !mobileSelectActive && (
        <div
          className="absolute bottom-2 left-1/2 -translate-x-1/2 z-20 px-3 py-1 rounded-full bg-black/60 text-white text-[10px] pointer-events-none"
          style={{ animation: "mobile-hint-fade 4s ease-in-out forwards" }}
        >
          滑动浏览 · 长按选区
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
      {/* CSS animation for ripple */}
      <style jsx>{`
        @keyframes mobile-select-ripple {
          0% {
            transform: scale(0.5);
            opacity: 0.4;
          }
          100% {
            transform: scale(2.5);
            opacity: 0;
          }
        }
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
