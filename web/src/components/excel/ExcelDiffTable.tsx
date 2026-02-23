"use client";

import { ExternalLink } from "lucide-react";
import type { ExcelDiffEntry } from "@/stores/excel-store";
import { useExcelStore } from "@/stores/excel-store";

interface ExcelDiffTableProps {
  data: ExcelDiffEntry;
}

type ChangeType = "added" | "modified" | "deleted";

function classifyChange(change: { old: string | number | boolean | null; new: string | number | boolean | null }): ChangeType {
  const oldEmpty = change.old == null || change.old === "";
  const newEmpty = change.new == null || change.new === "";
  if (oldEmpty && !newEmpty) return "added";
  if (!oldEmpty && newEmpty) return "deleted";
  return "modified";
}

const CHANGE_STYLES: Record<ChangeType, { bg: string; text: string; label: string; dot: string }> = {
  added: { bg: "bg-green-50 dark:bg-green-950/30", text: "text-green-700 dark:text-green-400", label: "新增", dot: "🟢" },
  modified: { bg: "bg-amber-50 dark:bg-amber-950/30", text: "text-amber-700 dark:text-amber-400", label: "修改", dot: "🟡" },
  deleted: { bg: "bg-red-50 dark:bg-red-950/30", text: "text-red-700 dark:text-red-400", label: "删除", dot: "🔴" },
};

function formatCellValue(val: string | number | boolean | null): string {
  if (val == null) return "(空)";
  if (typeof val === "string" && val === "") return "(空)";
  if (typeof val === "string" && val.startsWith("=")) return val;
  return String(val);
}

export function ExcelDiffTable({ data }: ExcelDiffTableProps) {
  const openPanel = useExcelStore((s) => s.openPanel);

  const handleOpenPanel = () => {
    openPanel(data.filePath, data.sheet);
  };

  const counts = { added: 0, modified: 0, deleted: 0 };
  for (const change of data.changes) {
    counts[classifyChange(change)]++;
  }

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
          <span className="text-[10px]">({data.affectedRange})</span>
        </div>
        <button
          onClick={handleOpenPanel}
          className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
        >
          <ExternalLink className="h-3 w-3" />
          在面板中打开
        </button>
      </div>

      {/* Diff Table */}
      <div className="overflow-x-auto max-h-[280px] overflow-y-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="sticky top-0 z-10 bg-muted/60 border-r border-b border-border px-3 py-1 text-left font-semibold">
                单元格
              </th>
              <th className="sticky top-0 z-10 bg-muted/60 border-r border-b border-border px-3 py-1 text-left font-semibold">
                旧值
              </th>
              <th className="sticky top-0 z-10 bg-muted/60 border-r border-b border-border px-3 py-1 text-left font-semibold">
                新值
              </th>
              <th className="sticky top-0 z-10 bg-muted/60 border-b border-border px-3 py-1 text-center font-semibold w-16">
                类型
              </th>
            </tr>
          </thead>
          <tbody>
            {data.changes.map((change, i) => {
              const type = classifyChange(change);
              const style = CHANGE_STYLES[type];
              const isFormula = typeof change.new === "string" && change.new.startsWith("=");
              return (
                <tr key={i} className={style.bg}>
                  <td className="border-r border-b border-border/50 px-3 py-0.5 font-mono">
                    {change.cell}
                  </td>
                  <td className="border-r border-b border-border/50 px-3 py-0.5 max-w-[160px] truncate text-muted-foreground">
                    {type === "added" ? (
                      <span className="italic">(空)</span>
                    ) : (
                      formatCellValue(change.old)
                    )}
                  </td>
                  <td className={`border-r border-b border-border/50 px-3 py-0.5 max-w-[160px] truncate font-medium ${style.text} ${isFormula ? "italic" : ""}`}>
                    {type === "deleted" ? (
                      <span className="italic">(空)</span>
                    ) : (
                      <>
                        {formatCellValue(change.new)}
                        {isFormula && <span className="ml-1 text-[10px] opacity-60">fx</span>}
                      </>
                    )}
                  </td>
                  <td className="border-b border-border/50 px-2 py-0.5 text-center">
                    <span className="text-[10px]">{style.dot} {style.label}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div className="px-3 py-1 bg-muted/20 border-t border-border text-[10px] text-muted-foreground flex gap-3">
        <span>共修改 {data.changes.length} 个单元格</span>
        {counts.modified > 0 && <span>🟡 {counts.modified} 修改</span>}
        {counts.added > 0 && <span>🟢 {counts.added} 新增</span>}
        {counts.deleted > 0 && <span>🔴 {counts.deleted} 删除</span>}
      </div>
    </div>
  );
}
