"use client";

import { ExternalLink, Table2 } from "lucide-react";
import type { ExcelPreviewData, CellStyle } from "@/stores/excel-store";
import { useExcelStore } from "@/stores/excel-store";
import { cellStyleToCSS } from "./cell-style-utils";

interface ExcelPreviewTableProps {
  data: ExcelPreviewData;
}

export function ExcelPreviewTable({ data }: ExcelPreviewTableProps) {
  const openPanel = useExcelStore((s) => s.openPanel);

  const handleOpenPanel = () => {
    openPanel(data.filePath, data.sheet);
  };

  // cellStyles[0] = header row styles, cellStyles[1..] = data row styles
  const headerStyles = data.cellStyles?.[0];
  const hasStyles = Boolean(data.cellStyles && data.cellStyles.length > 0);

  return (
    <div className="my-2 rounded-lg border border-border/80 overflow-hidden text-xs shadow-sm">
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-muted/50 border-b border-border/60">
        <div className="flex items-center gap-2 text-muted-foreground min-w-0">
          <Table2 className="h-3.5 w-3.5 flex-shrink-0 text-blue-500" />
          <span className="font-semibold text-foreground truncate text-[11px]">
            {data.filePath.split("/").pop() || data.filePath}
          </span>
          {data.sheet && (
            <>
              <span className="flex-shrink-0 text-muted-foreground/40">/</span>
              <span className="truncate text-muted-foreground/80">{data.sheet}</span>
            </>
          )}
        </div>
        <button
          onClick={handleOpenPanel}
          className="flex items-center gap-1 text-[10px] text-muted-foreground/60 hover:text-foreground transition-colors rounded px-1 py-0.5 hover:bg-muted/60"
        >
          <ExternalLink className="h-3 w-3" />
          <span className="hidden sm:inline">打开</span>
        </button>
      </div>

      {/* Table */}
      <div className="overflow-x-auto max-h-[320px] overflow-y-auto" style={{ touchAction: "pan-x pan-y" }}>
        <table className="w-full border-collapse text-[11px]">
          <thead>
            <tr>
              <th className="sticky top-0 left-0 z-20 w-9 min-w-[36px] bg-muted/80 backdrop-blur-sm border-r border-b border-border/60 px-1 py-1 text-center text-muted-foreground/60 font-normal text-[10px]">
                #
              </th>
              {data.columns.map((col, i) => {
                const hStyle = headerStyles?.[i];
                const css = hStyle ? cellStyleToCSS(hStyle as CellStyle) : {};
                return (
                  <th
                    key={i}
                    className="sticky top-0 z-10 bg-muted/80 backdrop-blur-sm border-r border-b border-border/60 px-2 py-1 text-left font-semibold whitespace-nowrap text-muted-foreground/90 min-w-[56px]"
                    style={css}
                  >
                    {col}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, rowIdx) => {
              // cellStyles[rowIdx + 1] = this data row (offset by 1 for header)
              const rowStyles = data.cellStyles?.[rowIdx + 1];
              return (
                <tr key={rowIdx} className="hover:bg-muted/15 transition-colors">
                  <td className="sticky left-0 z-10 w-9 min-w-[36px] bg-muted/60 backdrop-blur-sm border-r border-b border-border/40 px-1 py-1 text-center text-muted-foreground/60 tabular-nums font-normal text-[10px]">
                    {rowIdx + 1}
                  </td>
                  {row.map((cell, colIdx) => {
                    const cStyle = rowStyles?.[colIdx];
                    const css = cStyle ? cellStyleToCSS(cStyle as CellStyle) : {};
                    return (
                      <td
                        key={colIdx}
                        className={`border-r border-b border-border/15 px-2 py-1 whitespace-nowrap max-w-[200px] truncate ${
                          typeof cell === "number" ? "text-right tabular-nums" : "text-left"
                        }`}
                        style={hasStyles ? css : undefined}
                        title={cell != null ? String(cell) : ""}
                      >
                        {cell != null ? String(cell) : <span className="text-muted-foreground/20">—</span>}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div className="px-3 py-1 bg-muted/30 border-t border-border/50 text-[10px] text-muted-foreground/70">
        {data.totalRows.toLocaleString()} 行 × {data.columns.length} 列
        {data.truncated && `，显示前 ${data.rows.length} 行`}
      </div>
    </div>
  );
}
