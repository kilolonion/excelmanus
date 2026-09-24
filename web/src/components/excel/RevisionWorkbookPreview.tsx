"use client";

import type { RevisionPreviewResponse } from "@/lib/api";
import type { CellStyle } from "@/stores/excel-store";
import { cellRefFromIndex } from "@/lib/excel-cell-edit";
import { cellStyleToCSS, formatCellByPattern } from "./cell-style-utils";

/** A bounded, immutable view: never opens the live workbook or writes edits. */
export function RevisionWorkbookPreview({ data, loading, onSheet }: {
  data: RevisionPreviewResponse;
  loading: boolean;
  onSheet: (sheet: string) => void;
}) {
  const win = data.regions?.[0];
  if (!win) return null;
  const used = data.sheets?.find((s) => s.name === win.sheet)?.used;
  const r1 = Math.min(win.rect.r1, Math.max(used?.rows ?? win.rect.r1, win.rect.r0));
  const c1 = Math.min(win.rect.c1, Math.max(used?.cols ?? win.rect.c1, win.rect.c0));
  const rows = Array.from({ length: r1 - win.rect.r0 + 1 }, (_, i) => i + win.rect.r0);
  const cols = Array.from({ length: c1 - win.rect.c0 + 1 }, (_, i) => i + win.rect.c0);
  const letter = (c: number) => cellRefFromIndex(0, c - 1).replace(/1$/, "");
  const truncated = (used?.rows ?? 0) > r1 || (used?.cols ?? 0) > c1;
  return <div className="space-y-2" aria-busy={loading}>
    <div className="flex items-center gap-2 text-xs">
      <label htmlFor="revision-sheet">工作表</label>
      <select id="revision-sheet" value={win.sheet} disabled={loading} onChange={(e) => onSheet(e.target.value)} className="rounded border bg-background px-2 py-1">
        {(data.sheets ?? []).map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
      </select>
      <span className="text-muted-foreground">只读预览</span>
    </div>
    <div className="max-h-[50vh] overflow-auto rounded border bg-white text-black">
      <table className="border-collapse text-xs" style={{ tableLayout: "fixed", width: "max-content" }} aria-label="历史工作表预览">
        <colgroup><col style={{ width: 36 }} />{cols.map((c) => <col key={c} style={{ width: win.geometry?.columns.find((d) => d.index === c)?.pixels ?? 64 }} />)}</colgroup>
        <thead><tr><th className="border bg-gray-100" />{cols.map((c) => <th key={c} className="border bg-gray-100 px-2 font-normal">{letter(c)}</th>)}</tr></thead>
        <tbody>{rows.map((r) => <tr key={r} style={{ height: win.geometry?.rows.find((d) => d.index === r)?.pixels ?? 20 }}>
          <th className="border bg-gray-100 px-1 font-normal">{r}</th>
          {cols.map((c) => {
            // Check only the displayed cells; expanding a whole-column merge into
            // a hidden-cell set could allocate millions of entries.
            const merge = win.merges?.find((m) => r >= m.min_row && r <= m.max_row && c >= m.min_col && c <= m.max_col);
            if (merge && (r !== Math.max(merge.min_row, win.rect.r0) || c !== Math.max(merge.min_col, win.rect.c0))) return null;
            const cell = win.cells[`${r},${c}`];
            const style = cell?.s as CellStyle | undefined;
            const raw = cell?.v;
            const value = typeof raw === "number" ? formatCellByPattern(raw, style) ?? String(raw) : raw == null ? cell?.f ?? "" : String(raw);
            return <td key={c} rowSpan={merge ? Math.min(merge.max_row, r1) - r + 1 : undefined} colSpan={merge ? Math.min(merge.max_col, c1) - c + 1 : undefined}
              className="border border-gray-200 px-1 whitespace-pre-wrap break-words" title={cell?.f ? `公式：${cell.f}` : undefined}
              style={{ textAlign: typeof raw === "number" ? "right" : undefined, ...cellStyleToCSS(style) }}>{value}</td>;
          })}
        </tr>)}</tbody>
      </table>
    </div>
    <p className="text-[11px] text-muted-foreground">{truncated ? `当前显示 ${letter(win.rect.c0)}${win.rect.r0}:${letter(c1)}${r1}。` : ""}无缓存结果的公式显示公式原文。恢复会还原整份文件，包括所有工作表与格式。</p>
  </div>;
}
