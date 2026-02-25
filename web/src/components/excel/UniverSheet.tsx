"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import { useExcelStore } from "@/stores/excel-store";
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

  return (
    <div className="relative w-full h-full min-h-[400px] bg-white dark:bg-gray-800">
      <div
        ref={containerRef}
        className="w-full h-full bg-white dark:bg-gray-800"
        style={{ position: "relative" }}
      />
      {selectionMode && (
        <div className="absolute top-0 left-0 right-0 z-20 flex items-center justify-center gap-2 py-1.5 text-xs font-medium text-white" style={{ backgroundColor: "var(--em-primary)" }}>
          <span>请在表格中选择一个区域</span>
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
    </div>
  );
}
