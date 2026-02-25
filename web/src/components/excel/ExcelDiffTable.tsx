"use client";

import { useMemo } from "react";
import { ExternalLink } from "lucide-react";
import type { ExcelDiffEntry, ExcelCellDiff } from "@/stores/excel-store";
import { useExcelStore } from "@/stores/excel-store";

// ── 阈值：≤ 此值用 Inline diff，> 时用 Grid diff ────────
const INLINE_THRESHOLD = 5;

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

function formatCellValue(val: string | number | boolean | null): string {
  if (val == null) return "(空)";
  if (typeof val === "string" && val === "") return "(空)";
  if (typeof val === "string" && val.startsWith("=")) return val;
  return String(val);
}

// ── 单元格引用解析工具 ──────────────────────────────────
function colLetterToIndex(col: string): number {
  let idx = 0;
  for (let i = 0; i < col.length; i++) {
    idx = idx * 26 + (col.charCodeAt(i) - 64);
  }
  return idx;
}

function indexToColLetter(idx: number): string {
  let s = "";
  while (idx > 0) {
    const rem = (idx - 1) % 26;
    s = String.fromCharCode(65 + rem) + s;
    idx = Math.floor((idx - 1) / 26);
  }
  return s;
}

function parseCellRef(ref: string): { col: number; row: number } | null {
  const m = ref.toUpperCase().match(/^([A-Z]+)(\d+)$/);
  if (!m) return null;
  return { col: colLetterToIndex(m[1]), row: parseInt(m[2], 10) };
}

// Excel 自然排序：先行后列
function excelCellCompare(a: ExcelCellDiff, b: ExcelCellDiff): number {
  const pa = parseCellRef(a.cell);
  const pb = parseCellRef(b.cell);
  if (!pa || !pb) return 0;
  if (pa.row !== pb.row) return pa.row - pb.row;
  return pa.col - pb.col;
}

// 网格密度阈值：gridArea / changes > 此值时降级为 Inline
const GRID_DENSITY_THRESHOLD = 10;

// ── Inline Diff 视图（代码 diff 风格）──────────────────
function InlineDiffView({ changes }: { changes: ExcelCellDiff[] }) {
  const sorted = useMemo(() => [...changes].sort(excelCellCompare), [changes]);
  return (
    <div className="overflow-x-auto max-h-[320px] overflow-y-auto font-mono text-[11px] leading-[1.6]">
      {sorted.map((change, i) => {
        const type = classifyChange(change);
        const isFormula = typeof change.new === "string" && change.new.startsWith("=");
        return (
          <div key={i}>
            {/* 旧值行（删除/修改） */}
            {type !== "added" && (
              <div className="flex items-baseline bg-red-50 dark:bg-red-950/30 border-l-2 border-red-400 dark:border-red-600">
                <span className="w-6 flex-shrink-0 text-center text-red-500 dark:text-red-400 select-none">−</span>
                <span className="px-1 text-red-800 dark:text-red-300 font-semibold w-14 flex-shrink-0">{change.cell}</span>
                <span className="px-1 text-red-700 dark:text-red-300 truncate">{formatCellValue(change.old)}</span>
              </div>
            )}
            {/* 新值行（新增/修改） */}
            {type !== "deleted" && (
              <div className="flex items-baseline bg-green-50 dark:bg-green-950/30 border-l-2 border-green-500 dark:border-green-600">
                <span className="w-6 flex-shrink-0 text-center text-green-600 dark:text-green-400 select-none">+</span>
                <span className="px-1 text-green-800 dark:text-green-300 font-semibold w-14 flex-shrink-0">{change.cell}</span>
                <span className={`px-1 text-green-700 dark:text-green-300 truncate ${isFormula ? "italic" : ""}`}>
                  {formatCellValue(change.new)}
                  {isFormula && <span className="ml-1 text-[9px] opacity-60">fx</span>}
                </span>
              </div>
            )}
            {/* 分隔线（非最后一项） */}
            {i < sorted.length - 1 && (
              <div className="h-px bg-border/30" />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Grid Diff 视图（迷你表格风格）─────────────────────
interface GridCell {
  type: ChangeType;
  oldVal: string | number | boolean | null;
  newVal: string | number | boolean | null;
}

function GridDiffView({ changes }: { changes: ExcelCellDiff[] }) {
  const { cols, rows, cellMap } = useMemo(() => {
    // 仅基于 changes 的 bounding box 确定网格范围（不用 affectedRange 扩展，避免稀疏场景）
    let minCol = Infinity, maxCol = 0, minRow = Infinity, maxRow = 0;

    const map = new Map<string, GridCell>();
    for (const c of changes) {
      const ref = c.cell.toUpperCase();
      const parsed = parseCellRef(ref);
      if (!parsed) continue;
      minCol = Math.min(minCol, parsed.col);
      maxCol = Math.max(maxCol, parsed.col);
      minRow = Math.min(minRow, parsed.row);
      maxRow = Math.max(maxRow, parsed.row);
      map.set(ref, {
        type: classifyChange(c),
        oldVal: c.old,
        newVal: c.new,
      });
    }

    if (minCol === Infinity) return { cols: [], rows: [], cellMap: map };

    // 限制网格大小避免爆炸
    const cappedMaxRow = Math.min(maxRow, minRow + 49);
    const cappedMaxCol = Math.min(maxCol, minCol + 25);

    const colArr: number[] = [];
    for (let c = minCol; c <= cappedMaxCol; c++) colArr.push(c);
    const rowArr: number[] = [];
    for (let r = minRow; r <= cappedMaxRow; r++) rowArr.push(r);

    return { cols: colArr, rows: rowArr, cellMap: map };
  }, [changes]);

  const GRID_BG: Record<ChangeType, string> = {
    added: "bg-green-100 dark:bg-green-900/40",
    modified: "bg-amber-100 dark:bg-amber-900/40",
    deleted: "bg-red-100 dark:bg-red-900/40",
  };
  const GRID_BORDER: Record<ChangeType, string> = {
    added: "ring-1 ring-inset ring-green-300 dark:ring-green-700",
    modified: "ring-1 ring-inset ring-amber-300 dark:ring-amber-700",
    deleted: "ring-1 ring-inset ring-red-300 dark:ring-red-700",
  };

  return (
    <div className="overflow-x-auto max-h-[320px] overflow-y-auto">
      <table className="border-collapse text-[11px]">
        <thead>
          <tr>
            {/* 行号列头 */}
            <th className="sticky top-0 left-0 z-20 bg-muted/70 border-r border-b border-border w-10 min-w-[40px] px-1 py-0.5 text-center text-muted-foreground font-normal" />
            {cols.map((c) => (
              <th
                key={c}
                className="sticky top-0 z-10 bg-muted/70 border-r border-b border-border px-2 py-0.5 text-center font-semibold text-muted-foreground min-w-[60px]"
              >
                {indexToColLetter(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r}>
              <td className="sticky left-0 z-10 bg-muted/50 border-r border-b border-border/50 px-1 py-0.5 text-center text-muted-foreground tabular-nums font-normal">
                {r}
              </td>
              {cols.map((c) => {
                const ref = `${indexToColLetter(c)}${r}`;
                const cell = cellMap.get(ref);
                if (!cell) {
                  return (
                    <td
                      key={c}
                      className="border-r border-b border-border/20 px-2 py-0.5 text-muted-foreground/30 text-center"
                    >
                      ·
                    </td>
                  );
                }
                const displayVal = cell.type === "deleted"
                  ? formatCellValue(cell.oldVal)
                  : formatCellValue(cell.newVal);
                const tooltipParts: string[] = [];
                if (cell.type === "modified") {
                  tooltipParts.push(`旧: ${formatCellValue(cell.oldVal)}`);
                  tooltipParts.push(`新: ${formatCellValue(cell.newVal)}`);
                } else if (cell.type === "added") {
                  tooltipParts.push(`新增: ${formatCellValue(cell.newVal)}`);
                } else {
                  tooltipParts.push(`删除: ${formatCellValue(cell.oldVal)}`);
                }
                return (
                  <td
                    key={c}
                    className={`border-r border-b border-border/30 px-2 py-0.5 truncate max-w-[120px] font-medium ${GRID_BG[cell.type]} ${GRID_BORDER[cell.type]}`}
                    title={tooltipParts.join("\n")}
                  >
                    {displayVal}
                    {cell.type === "deleted" && (
                      <span className="ml-0.5 text-[9px] text-red-400 line-through" />
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── 主组件：智能切换 ──────────────────────────────────
export function ExcelDiffTable({ data }: ExcelDiffTableProps) {
  const openPanel = useExcelStore((s) => s.openPanel);

  const handleOpenPanel = () => {
    openPanel(data.filePath, data.sheet);
  };

  const counts = { added: 0, modified: 0, deleted: 0 };
  for (const change of data.changes) {
    counts[classifyChange(change)]++;
  }

  // 智能视图选择：
  // 1. ≤ INLINE_THRESHOLD 时用 Inline
  // 2. > INLINE_THRESHOLD 但网格密度太低（大部分空白）时也降级为 Inline
  const useInline = useMemo(() => {
    const n = data.changes.length;
    if (n <= INLINE_THRESHOLD) return true;
    // 计算 bounding box 面积
    let minCol = Infinity, maxCol = 0, minRow = Infinity, maxRow = 0;
    for (const c of data.changes) {
      const p = parseCellRef(c.cell);
      if (!p) continue;
      minCol = Math.min(minCol, p.col);
      maxCol = Math.max(maxCol, p.col);
      minRow = Math.min(minRow, p.row);
      maxRow = Math.max(maxRow, p.row);
    }
    if (minCol === Infinity) return true;
    const area = (maxCol - minCol + 1) * (maxRow - minRow + 1);
    // 网格太稀疏 → 降级为 Inline
    if (area / n > GRID_DENSITY_THRESHOLD) return true;
    return false;
  }, [data.changes]);

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
        <div className="flex items-center gap-2">
          {/* +N -N 统计 */}
          <span className="text-[10px] tabular-nums">
            {(counts.added + counts.modified) > 0 && (
              <span className="text-green-600 dark:text-green-400">+{counts.added + counts.modified}</span>
            )}
            {(counts.deleted + counts.modified) > 0 && (
              <span className="text-red-500 dark:text-red-400 ml-1">-{counts.deleted + counts.modified}</span>
            )}
          </span>
          <button
            onClick={handleOpenPanel}
            className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
          >
            <ExternalLink className="h-3 w-3" />
            在面板中打开
          </button>
        </div>
      </div>

      {/* Diff 内容区 */}
      {useInline ? (
        <InlineDiffView changes={data.changes} />
      ) : (
        <GridDiffView changes={data.changes} />
      )}

      {/* Footer */}
      <div className="px-3 py-1 bg-muted/20 border-t border-border text-[10px] text-muted-foreground flex gap-3">
        <span>共 {data.changes.length} 处变更</span>
        {counts.modified > 0 && <span className="text-amber-600 dark:text-amber-400">● {counts.modified} 修改</span>}
        {counts.added > 0 && <span className="text-green-600 dark:text-green-400">● {counts.added} 新增</span>}
        {counts.deleted > 0 && <span className="text-red-500 dark:text-red-400">● {counts.deleted} 删除</span>}
      </div>
    </div>
  );
}
