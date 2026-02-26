"use client";

import { ExternalLink } from "lucide-react";
import type { ExcelPreviewData } from "@/stores/excel-store";
import { useExcelStore } from "@/stores/excel-store";

interface ExcelPreviewTableProps {
  data: ExcelPreviewData;
}

export function ExcelPreviewTable({ data }: ExcelPreviewTableProps) {
  const openPanel = useExcelStore((s) => s.openPanel);

  const handleOpenPanel = () => {
    openPanel(data.filePath, data.sheet);
  };

  return (
    <div className="my-2 rounded-lg border border-border overflow-hidden text-xs">
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-muted/40 border-b border-border">
        <div className="flex items-center gap-2 text-muted-foreground">
          <span className="font-medium text-foreground">
            {data.filePath.split("/").pop() || data.filePath}
          </span>
          {data.sheet && (
            <>
              <span>/</span>
              <span>{data.sheet}</span>
            </>
          )}
        </div>
        <button
          onClick={handleOpenPanel}
          className="flex items-center gap-1 p-1.5 md:p-1 rounded text-xs text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
        >
          <ExternalLink className="h-2.5 w-2.5 md:h-3 md:w-3" />
          在面板中打开
        </button>
      </div>

      {/* Table */}
      <div className="overflow-x-auto max-h-[320px] overflow-y-auto" style={{ touchAction: "pan-x pan-y" }}>
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="sticky top-0 z-10 w-10 min-w-[40px] bg-muted/60 border-r border-b border-border px-2 py-1 text-right text-muted-foreground font-normal">
                #
              </th>
              {data.columns.map((col, i) => (
                <th
                  key={i}
                  className="sticky top-0 z-10 bg-muted/60 border-r border-b border-border px-3 py-1 text-left font-semibold whitespace-nowrap"
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, rowIdx) => (
              <tr key={rowIdx} className="hover:bg-muted/20">
                <td className="w-10 min-w-[40px] bg-muted/30 border-r border-b border-border/50 px-2 py-0.5 text-right text-muted-foreground tabular-nums">
                  {rowIdx + 1}
                </td>
                {row.map((cell, colIdx) => (
                  <td
                    key={colIdx}
                    className={`border-r border-b border-border/50 px-3 py-0.5 whitespace-nowrap max-w-[200px] truncate ${
                      typeof cell === "number" ? "text-right tabular-nums" : "text-left"
                    }`}
                    title={cell != null ? String(cell) : ""}
                  >
                    {cell != null ? String(cell) : ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div className="px-3 py-1 bg-muted/20 border-t border-border text-[10px] text-muted-foreground">
        共 {data.totalRows.toLocaleString()} 行 × {data.columns.length} 列
        {data.truncated && `，显示前 ${data.rows.length} 行`}
      </div>
    </div>
  );
}
