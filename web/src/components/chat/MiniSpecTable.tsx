"use client";

import React, { useMemo } from "react";
import type { PipelineStatus } from "@/stores/chat-store";

interface MiniSpecTableProps {
  spec: Record<string, unknown>;
  diffHighlights?: PipelineStatus["diff"];
  stageKey?: string;
  maxRows?: number;
  maxCols?: number;
}

interface CellData {
  address: string;
  value: unknown;
  styleId?: string;
}

interface SheetData {
  name: string;
  dimensions: { rows: number; cols: number };
  cells: CellData[];
  merges: string[];
}

/**
 * Parse column letter(s) to 0-based index: A→0, B→1, ..., Z→25, AA→26
 */
function colToIndex(col: string): number {
  let idx = 0;
  for (const ch of col.toUpperCase()) {
    idx = idx * 26 + (ch.charCodeAt(0) - 64);
  }
  return idx - 1;
}

/**
 * Convert 0-based index to column letter: 0→A, 1→B, ..., 25→Z
 */
function indexToCol(idx: number): string {
  let s = "";
  let n = idx + 1;
  while (n > 0) {
    n--;
    s = String.fromCharCode(65 + (n % 26)) + s;
    n = Math.floor(n / 26);
  }
  return s;
}

/**
 * Parse cell address like "A1" into { col: 0, row: 0 }
 */
function parseAddress(addr: string): { col: number; row: number } | null {
  const match = /^([A-Z]+)(\d+)$/i.exec(addr.trim());
  if (!match) return null;
  return { col: colToIndex(match[1]), row: parseInt(match[2], 10) - 1 };
}

/**
 * Determine highlight color based on stage and diff type.
 */
function getCellHighlight(
  stageKey: string | undefined,
  isNew: boolean,
): string | null {
  if (!isNew) return null;
  switch (stageKey) {
    case "vlm_extract_structure":
      return "bg-zinc-200/50 dark:bg-zinc-700/30"; // 骨架灰
    case "vlm_extract_data":
      return "bg-emerald-100/60 dark:bg-emerald-900/20"; // 数据绿
    case "vlm_extract_style":
      return "bg-amber-100/60 dark:bg-amber-900/20"; // 样式黄
    case "vlm_extract_verification":
      return "bg-blue-100/60 dark:bg-blue-900/20"; // 校验蓝
    default:
      return "bg-emerald-100/60 dark:bg-emerald-900/20";
  }
}

export const MiniSpecTable = React.memo(function MiniSpecTable({
  spec,
  diffHighlights,
  stageKey,
  maxRows = 8,
  maxCols = 8,
}: MiniSpecTableProps) {
  const sheet: SheetData | null = useMemo(() => {
    const sheets = (spec as { sheets?: SheetData[] }).sheets;
    if (!sheets || sheets.length === 0) return null;
    return sheets[0]; // 显示第一个 sheet
  }, [spec]);

  const grid = useMemo(() => {
    if (!sheet) return null;

    const dims = sheet.dimensions || { rows: 0, cols: 0 };
    const rows = Math.min(dims.rows || 0, maxRows);
    const cols = Math.min(dims.cols || 0, maxCols);
    if (rows === 0 || cols === 0) return null;

    // 构建单元格映射
    const cellMap = new Map<string, CellData>();
    for (const cell of sheet.cells || []) {
      const parsed = parseAddress(cell.address);
      if (parsed && parsed.row < rows && parsed.col < cols) {
        cellMap.set(`${parsed.row},${parsed.col}`, cell);
      }
    }

    // 构建 diff 集合（新增单元格）
    const addedCells = new Set<string>();
    if (diffHighlights?.changes) {
      for (const ch of diffHighlights.changes) {
        if (ch.cells_added && ch.cells_added > 0) {
          // 结构/数据阶段中本阶段所有单元格视为「新增」
          for (const cell of sheet.cells || []) {
            const parsed = parseAddress(cell.address);
            if (parsed) addedCells.add(`${parsed.row},${parsed.col}`);
          }
        }
        if (ch.modified_details) {
          for (const mod of ch.modified_details) {
            const parsed = parseAddress(mod.cell);
            if (parsed) addedCells.add(`${parsed.row},${parsed.col}`);
          }
        }
      }
    }

    return { rows, cols, cellMap, addedCells, totalRows: dims.rows, totalCols: dims.cols };
  }, [sheet, maxRows, maxCols, diffHighlights]);

  if (!sheet || !grid) {
    return (
      <p className="text-[10px] text-muted-foreground py-1">
        无表格数据
      </p>
    );
  }

  const truncatedRows = grid.totalRows > grid.rows;
  const truncatedCols = grid.totalCols > grid.cols;

  return (
    <div className="rounded-lg border border-border/40 overflow-hidden">
      {/* 工作表名 */}
      <div className="px-2 py-1 bg-muted/20 text-[10px] text-muted-foreground flex items-center justify-between">
        <span className="font-medium">{sheet.name}</span>
        <span>
          {grid.totalRows}行 × {grid.totalCols}列
        </span>
      </div>

      {/* 表格 */}
      <div className="overflow-x-auto">
        <table className="w-full text-[10px] border-collapse">
          <thead>
            <tr>
              <th className="w-6 px-1 py-0.5 text-center text-muted-foreground/50 font-normal bg-muted/10 border-b border-r border-border/30">
                #
              </th>
              {Array.from({ length: grid.cols }, (_, c) => (
                <th
                  key={c}
                  className="px-1.5 py-0.5 text-center text-muted-foreground/50 font-normal bg-muted/10 border-b border-r border-border/30 min-w-[40px]"
                >
                  {indexToCol(c)}
                </th>
              ))}
              {truncatedCols && (
                <th className="px-1 py-0.5 text-center text-muted-foreground/30 font-normal bg-muted/10 border-b border-border/30">
                  …
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: grid.rows }, (_, r) => (
              <tr key={r}>
                <td className="px-1 py-0.5 text-center text-muted-foreground/50 bg-muted/10 border-r border-b border-border/30 tabular-nums">
                  {r + 1}
                </td>
                {Array.from({ length: grid.cols }, (_, c) => {
                  const key = `${r},${c}`;
                  const cell = grid.cellMap.get(key);
                  const isNew = grid.addedCells.has(key);
                  const highlight = getCellHighlight(stageKey, isNew);
                  const displayValue = cell
                    ? String(cell.value ?? "")
                    : stageKey === "vlm_extract_structure"
                      ? "░"
                      : "";

                  return (
                    <td
                      key={c}
                      className={`px-1.5 py-0.5 border-r border-b border-border/20 truncate max-w-[80px] ${
                        highlight || ""
                      }`}
                      title={displayValue.length > 10 ? displayValue : undefined}
                    >
                      {displayValue}
                    </td>
                  );
                })}
                {truncatedCols && (
                  <td className="px-1 py-0.5 text-center text-muted-foreground/30 border-b border-border/20">
                    …
                  </td>
                )}
              </tr>
            ))}
            {truncatedRows && (
              <tr>
                <td
                  colSpan={grid.cols + 1 + (truncatedCols ? 1 : 0)}
                  className="px-1.5 py-0.5 text-center text-muted-foreground/40 text-[9px]"
                >
                  … 还有 {grid.totalRows - grid.rows} 行
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
});
