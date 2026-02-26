"use client";

import { useMemo, useRef, useState, useEffect, useCallback } from "react";
import { ExternalLink, Plus, Minus, RefreshCw } from "lucide-react";
import type { ExcelDiffEntry, ExcelCellDiff, CellStyle, MergeRange } from "@/stores/excel-store";
import { useExcelStore } from "@/stores/excel-store";
import { cellStyleToCSS } from "./cell-style-utils";
import { buildMergeMaps, type MergeSpan } from "./merge-utils";

// ── 阈值 ────────────────────────────────────────────────
const INLINE_THRESHOLD = 5;
const VERTICAL_BREAKPOINT = 480;

type DiffLayout = "horizontal" | "vertical";
type DiffProfile = "all-added" | "all-deleted" | "mixed";

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
  if (val == null) return "";
  if (typeof val === "string" && val === "") return "";
  if (typeof val === "string" && val.startsWith("=")) return val;
  return String(val);
}

function isEmpty(val: string | number | boolean | null): boolean {
  return val == null || val === "";
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

function excelCellCompare(a: ExcelCellDiff, b: ExcelCellDiff): number {
  const pa = parseCellRef(a.cell);
  const pb = parseCellRef(b.cell);
  if (!pa || !pb) return 0;
  if (pa.row !== pb.row) return pa.row - pb.row;
  return pa.col - pb.col;
}

const GRID_DENSITY_THRESHOLD = 10;

// ── 变更类型色彩系统 ────────────────────────────────────
const CHANGE_INDICATOR: Record<ChangeType, string> = {
  added: "border-l-2 border-l-green-500 dark:border-l-green-600",
  modified: "border-l-2 border-l-amber-400 dark:border-l-amber-500",
  deleted: "border-l-2 border-l-red-400 dark:border-l-red-500",
};

const CELL_BG: Record<ChangeType, string> = {
  added: "bg-green-50 dark:bg-green-950/30",
  modified: "bg-amber-50 dark:bg-amber-950/25",
  deleted: "bg-red-50 dark:bg-red-950/30",
};

const CELL_BORDER: Record<ChangeType, string> = {
  added: "border border-green-200 dark:border-green-800/60",
  modified: "border border-amber-200 dark:border-amber-800/60",
  deleted: "border border-red-200 dark:border-red-800/60",
};

const CELL_RING: Record<ChangeType, string> = {
  added: "ring-2 ring-inset ring-green-400/50 dark:ring-green-500/40",
  modified: "ring-2 ring-inset ring-amber-400/50 dark:ring-amber-500/40",
  deleted: "ring-2 ring-inset ring-red-400/50 dark:ring-red-500/40",
};

// ── 样式单元格渲染 ──────────────────────────────────────
function StyledCell({
  value,
  style,
  isEmptySlot,
  className = "",
}: {
  value: string | number | boolean | null;
  style?: CellStyle | null;
  isEmptySlot?: boolean;
  className?: string;
}) {
  const css = cellStyleToCSS(style);
  const display = formatCellValue(value);
  if (isEmptySlot) {
    return (
      <span className={`inline-block px-1.5 py-0.5 text-muted-foreground/25 select-none ${className}`}>
        —
      </span>
    );
  }
  return (
    <span
      className={`inline-block px-1.5 py-0.5 rounded-sm min-w-[40px] truncate max-w-[180px] ${
        isEmpty(value) ? "text-muted-foreground/30" : ""
      } ${className}`}
      style={isEmpty(value) ? undefined : css}
      title={display || undefined}
    >
      {display || <span className="text-muted-foreground/20">—</span>}
    </span>
  );
}

// ── 变更类型 Badge ─────────────────────────────────────
function ChangeBadge({ type, size = "sm" }: { type: ChangeType; size?: "sm" | "xs" }) {
  const cls = size === "sm" ? "text-[10px] px-1.5 py-px" : "text-[9px] px-1 py-px";
  const colorMap: Record<ChangeType, string> = {
    added: "bg-green-100 dark:bg-green-900/50 text-green-700 dark:text-green-300 border border-green-200/60 dark:border-green-700/40",
    modified: "bg-amber-100 dark:bg-amber-900/50 text-amber-700 dark:text-amber-300 border border-amber-200/60 dark:border-amber-700/40",
    deleted: "bg-red-100 dark:bg-red-900/50 text-red-700 dark:text-red-300 border border-red-200/60 dark:border-red-700/40",
  };
  const labelMap: Record<ChangeType, string> = { added: "新增", modified: "修改", deleted: "删除" };
  return (
    <span className={`inline-flex items-center rounded-full font-medium leading-none ${cls} ${colorMap[type]}`}>
      {labelMap[type]}
    </span>
  );
}

// ── Inline Diff 视图 ────────────────────────────────────

function InlineHorizontalView({ changes }: { changes: ExcelCellDiff[] }) {
  const sorted = useMemo(() => [...changes].sort(excelCellCompare), [changes]);
  return (
    <div className="overflow-x-auto max-h-[320px] overflow-y-auto text-[11px] leading-[1.6]" style={{ touchAction: "pan-x pan-y" }}>
      <div className="flex items-center bg-muted/60 border-b border-border text-[10px] text-muted-foreground font-medium sticky top-0 z-10 backdrop-blur-sm">
        <span className="w-14 flex-shrink-0 px-1 text-center">Cell</span>
        <span className="flex-1 px-2 text-center border-l border-border/40">Before</span>
        <span className="w-5 flex-shrink-0" />
        <span className="flex-1 px-2 text-center border-l border-border/40">After</span>
      </div>
      {sorted.map((change, i) => {
        const type = classifyChange(change);
        return (
          <div
            key={i}
            className={`flex items-center ${CHANGE_INDICATOR[type]} hover:bg-muted/30 transition-colors ${
              i < sorted.length - 1 ? "border-b border-border/15" : ""
            }`}
          >
            <span className="w-14 flex-shrink-0 px-1 text-center font-mono font-semibold text-muted-foreground/70 tabular-nums text-[10px]">
              {change.cell}
            </span>
            <div className="flex-1 px-1.5 py-1 border-l border-border/20 bg-red-50/30 dark:bg-red-950/8">
              <StyledCell value={change.old} style={change.oldStyle} isEmptySlot={type === "added"} />
            </div>
            <span className="w-5 flex-shrink-0 text-center text-muted-foreground/40 text-[10px]">→</span>
            <div className="flex-1 px-1.5 py-1 border-l border-border/20 bg-green-50/30 dark:bg-green-950/8">
              <StyledCell value={change.new} style={change.newStyle} isEmptySlot={type === "deleted"} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function InlineVerticalView({ changes }: { changes: ExcelCellDiff[] }) {
  const sorted = useMemo(() => [...changes].sort(excelCellCompare), [changes]);
  return (
    <div className="overflow-x-auto max-h-[320px] overflow-y-auto text-[11px] leading-[1.6]" style={{ touchAction: "pan-x pan-y" }}>
      {sorted.map((change, i) => {
        const type = classifyChange(change);
        return (
          <div
            key={i}
            className={`${CHANGE_INDICATOR[type]} ${
              i < sorted.length - 1 ? "border-b border-border/25" : ""
            }`}
          >
            <div className="flex items-center gap-1.5 px-2.5 pt-1.5 pb-0.5">
              <span className="font-mono font-semibold text-muted-foreground/70 tabular-nums text-[10px]">
                {change.cell}
              </span>
              <ChangeBadge type={type} size="xs" />
            </div>
            {type !== "added" && (
              <div className="flex items-center px-2.5 py-1 bg-red-50/30 dark:bg-red-950/8">
                <span className="w-12 flex-shrink-0 text-[9px] text-red-500/70 dark:text-red-400/70 font-medium uppercase tracking-wider">Before</span>
                <StyledCell value={change.old} style={change.oldStyle} className="max-w-none" />
              </div>
            )}
            {type !== "deleted" && (
              <div className="flex items-center px-2.5 py-1 bg-green-50/30 dark:bg-green-950/8">
                <span className="w-12 flex-shrink-0 text-[9px] text-green-600/70 dark:text-green-400/70 font-medium uppercase tracking-wider">After</span>
                <StyledCell value={change.new} style={change.newStyle} className="max-w-none" />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function InlineDiffView({ changes, layout }: { changes: ExcelCellDiff[]; layout: DiffLayout }) {
  return layout === "horizontal"
    ? <InlineHorizontalView changes={changes} />
    : <InlineVerticalView changes={changes} />;
}

// ── Grid Diff 视图 ──────────────────────────────────────
interface GridCell {
  type: ChangeType;
  oldVal: string | number | boolean | null;
  newVal: string | number | boolean | null;
  oldStyle?: CellStyle | null;
  newStyle?: CellStyle | null;
}

/** 单侧表格（用于 before|after 对比和单表模式） */
function GridHalfTable({
  label,
  side,
  cols,
  rows,
  cellMap,
  badge,
  masterMap,
  hiddenSet,
}: {
  label: string;
  side: "before" | "after";
  cols: number[];
  rows: number[];
  cellMap: Map<string, GridCell>;
  badge?: React.ReactNode;
  masterMap?: Map<string, MergeSpan>;
  hiddenSet?: Set<string>;
}) {
  return (
    <div className="flex-1 min-w-0 overflow-x-auto">
      {/* 面板标题栏 */}
      <div className={`flex items-center justify-center gap-1.5 text-[10px] font-semibold py-1 sticky top-0 z-10 ${
        side === "before"
          ? "bg-red-50 dark:bg-red-950/30 text-red-600 dark:text-red-400 border-b border-red-100 dark:border-red-900/30"
          : "bg-green-50 dark:bg-green-950/30 text-green-600 dark:text-green-400 border-b border-green-100 dark:border-green-900/30"
      }`}>
        <span className="uppercase tracking-wider">{label}</span>
        {badge}
      </div>
      <table className="border-collapse text-[11px] w-full">
        <thead>
          <tr>
            <th className="sticky left-0 z-20 bg-muted/80 backdrop-blur-sm border-r border-b border-border/60 w-9 min-w-[36px] px-1 py-1 text-center text-muted-foreground/60 font-normal text-[10px]" />
            {cols.map((c) => (
              <th
                key={c}
                className="sticky top-0 z-10 bg-muted/80 backdrop-blur-sm border-r border-b border-border/60 px-2 py-1 text-center font-semibold text-muted-foreground/80 min-w-[56px] text-[10px] uppercase"
              >
                {indexToColLetter(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r} className="hover:bg-muted/15 transition-colors">
              <td className="sticky left-0 z-10 bg-muted/60 backdrop-blur-sm border-r border-b border-border/40 px-1 py-1 text-center text-muted-foreground/60 tabular-nums font-normal text-[10px]">
                {r}
              </td>
              {cols.map((c) => {
                // 合并单元格检测
                if (hiddenSet?.has(`${r},${c}`)) return null;
                const mergeSpan = masterMap?.get(`${r},${c}`);

                const ref = `${indexToColLetter(c)}${r}`;
                const cell = cellMap.get(ref);
                if (!cell) {
                  return (
                    <td key={c} className="border-r border-b border-border/15 px-2 py-1 text-center"
                      colSpan={mergeSpan?.colSpan} rowSpan={mergeSpan?.rowSpan}
                    >
                      <span className="text-muted-foreground/15">·</span>
                    </td>
                  );
                }
                const val = side === "before" ? cell.oldVal : cell.newVal;
                const style = side === "before" ? cell.oldStyle : cell.newStyle;
                const isEmptySlot = (side === "before" && cell.type === "added") ||
                  (side === "after" && cell.type === "deleted");

                if (isEmptySlot) {
                  return (
                    <td key={c} className="border-r border-b border-border/15 px-2 py-1 text-center"
                      colSpan={mergeSpan?.colSpan} rowSpan={mergeSpan?.rowSpan}
                    >
                      <span className="text-muted-foreground/20">—</span>
                    </td>
                  );
                }

                const css = cellStyleToCSS(style);
                const hasBg = css.backgroundColor != null;
                return (
                  <td
                    key={c}
                    className={`border-r border-b border-border/15 px-2 py-1 truncate max-w-[120px] ${
                      hasBg ? CELL_RING[cell.type] : `${CELL_BG[cell.type]} ${CELL_BORDER[cell.type]}`
                    } font-medium`}
                    style={css}
                    title={formatCellValue(val) || undefined}
                    colSpan={mergeSpan?.colSpan}
                    rowSpan={mergeSpan?.rowSpan}
                  >
                    {formatCellValue(val) || <span className="text-muted-foreground/20">—</span>}
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

/** 单表展示模式：全新增/全删除时只展示有意义的一侧 */
function GridSingleView({
  changes,
  profile,
  mergeRanges,
}: {
  changes: ExcelCellDiff[];
  profile: "all-added" | "all-deleted";
  mergeRanges?: MergeRange[];
}) {
  const { cols, rows, cellMap } = useMemo(() => {
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
        oldStyle: c.oldStyle,
        newStyle: c.newStyle,
      });
    }
    if (minCol === Infinity) return { cols: [], rows: [], cellMap: map };
    const cappedMaxRow = Math.min(maxRow, minRow + 49);
    const cappedMaxCol = Math.min(maxCol, minCol + 25);
    const colArr: number[] = [];
    for (let c = minCol; c <= cappedMaxCol; c++) colArr.push(c);
    const rowArr: number[] = [];
    for (let r = minRow; r <= cappedMaxRow; r++) rowArr.push(r);
    return { cols: colArr, rows: rowArr, cellMap: map };
  }, [changes]);

  const side = profile === "all-added" ? "after" as const : "before" as const;
  const label = profile === "all-added" ? "写入内容" : "删除内容";

  const { masterMap: mMap, hiddenSet: hSet } = useMemo(
    () => buildMergeMaps(mergeRanges),
    [mergeRanges],
  );

  return (
    <div className="max-h-[360px] overflow-y-auto overflow-x-auto" style={{ touchAction: "pan-x pan-y" }}>
      <GridHalfTable
        label={label}
        side={side}
        cols={cols}
        rows={rows}
        cellMap={cellMap}
        masterMap={mMap}
        hiddenSet={hSet}
        badge={
          <span className={`text-[9px] px-1.5 py-px rounded-full font-medium ${
            profile === "all-added"
              ? "bg-green-200/60 dark:bg-green-800/40 text-green-700 dark:text-green-300"
              : "bg-red-200/60 dark:bg-red-800/40 text-red-700 dark:text-red-300"
          }`}>
            {changes.length} cells
          </span>
        }
      />
    </div>
  );
}

function GridDiffView({ changes, layout, profile, mergeRanges, oldMergeRanges }: { changes: ExcelCellDiff[]; layout: DiffLayout; profile: DiffProfile; mergeRanges?: MergeRange[]; oldMergeRanges?: MergeRange[] }) {
  // 全增/全删：单表展示，不浪费空间显示空表
  if (profile !== "mixed") {
    return <GridSingleView changes={changes} profile={profile} mergeRanges={profile === "all-deleted" ? oldMergeRanges : mergeRanges} />;
  }

  const { cols, rows, cellMap } = useMemo(() => {
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
        oldStyle: c.oldStyle,
        newStyle: c.newStyle,
      });
    }
    if (minCol === Infinity) return { cols: [], rows: [], cellMap: map };
    const cappedMaxRow = Math.min(maxRow, minRow + 49);
    const cappedMaxCol = Math.min(maxCol, minCol + 25);
    const colArr: number[] = [];
    for (let c = minCol; c <= cappedMaxCol; c++) colArr.push(c);
    const rowArr: number[] = [];
    for (let r = minRow; r <= cappedMaxRow; r++) rowArr.push(r);
    return { cols: colArr, rows: rowArr, cellMap: map };
  }, [changes]);

  const { masterMap: oldMMap, hiddenSet: oldHSet } = useMemo(
    () => buildMergeMaps(oldMergeRanges),
    [oldMergeRanges],
  );
  const { masterMap: newMMap, hiddenSet: newHSet } = useMemo(
    () => buildMergeMaps(mergeRanges),
    [mergeRanges],
  );

  const isVertical = layout === "vertical";

  return (
    <div
      className={`max-h-[420px] overflow-y-auto ${
        isVertical ? "flex flex-col gap-0" : "flex flex-row gap-0"
      }`}
      style={{ touchAction: "pan-x pan-y" }}
    >
      <GridHalfTable label="Before" side="before" cols={cols} rows={rows} cellMap={cellMap} masterMap={oldMMap} hiddenSet={oldHSet} />
      <div className={isVertical
        ? "h-px bg-border/60 flex-shrink-0"
        : "w-px bg-border/60 flex-shrink-0"
      } />
      <GridHalfTable label="After" side="after" cols={cols} rows={rows} cellMap={cellMap} masterMap={newMMap} hiddenSet={newHSet} />
    </div>
  );
}

// ── 容器宽度检测 Hook ─────────────────────────────────
function useContainerWidth(ref: React.RefObject<HTMLDivElement | null>): number {
  const [width, setWidth] = useState(0);
  const handleResize = useCallback((entries: ResizeObserverEntry[]) => {
    if (entries[0]) {
      setWidth(entries[0].contentRect.width);
    }
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(handleResize);
    observer.observe(el);
    setWidth(el.clientWidth);
    return () => observer.disconnect();
  }, [ref, handleResize]);

  return width;
}

// ── 主组件 ──────────────────────────────────────────────
export function ExcelDiffTable({ data }: ExcelDiffTableProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const containerWidth = useContainerWidth(containerRef);
  const openPanel = useExcelStore((s) => s.openPanel);

  const handleOpenPanel = () => {
    openPanel(data.filePath, data.sheet);
  };

  const counts = { added: 0, modified: 0, deleted: 0 };
  for (const change of data.changes) {
    counts[classifyChange(change)]++;
  }

  const total = data.changes.length;
  const profile: DiffProfile =
    counts.added === total ? "all-added"
    : counts.deleted === total ? "all-deleted"
    : "mixed";

  const layout: DiffLayout = containerWidth > 0 && containerWidth < VERTICAL_BREAKPOINT
    ? "vertical"
    : "horizontal";

  const useInline = useMemo(() => {
    const n = data.changes.length;
    if (n <= INLINE_THRESHOLD) return true;
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
    if (area / n > GRID_DENSITY_THRESHOLD) return true;
    return false;
  }, [data.changes]);

  // Header 变更类型图标
  const HeaderIcon = profile === "all-added" ? Plus
    : profile === "all-deleted" ? Minus
    : RefreshCw;

  return (
    <div ref={containerRef} className="my-2 rounded-lg border border-border/80 overflow-hidden text-xs shadow-sm">
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-muted/50 border-b border-border/60">
        <div className="flex items-center gap-2 text-muted-foreground min-w-0">
          <HeaderIcon className={`h-3.5 w-3.5 flex-shrink-0 ${
            profile === "all-added" ? "text-green-500" : profile === "all-deleted" ? "text-red-500" : "text-amber-500"
          }`} />
          <span className="font-semibold text-foreground truncate text-[11px]">
            {data.filePath.split("/").pop() || data.filePath}
          </span>
          {data.sheet && (
            <>
              <span className="flex-shrink-0 text-muted-foreground/40">/</span>
              <span className="truncate text-muted-foreground/80">{data.sheet}</span>
            </>
          )}
          <span className="text-[9px] text-muted-foreground/50 flex-shrink-0 font-mono">({data.affectedRange})</span>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="text-[10px] tabular-nums font-medium">
            {(counts.added + counts.modified) > 0 && (
              <span className="text-green-600 dark:text-green-400">+{counts.added + counts.modified}</span>
            )}
            {(counts.deleted + counts.modified) > 0 && (
              <span className="text-red-500 dark:text-red-400 ml-1">−{counts.deleted + counts.modified}</span>
            )}
          </span>
          <button
            onClick={handleOpenPanel}
            className="flex items-center gap-1 text-[10px] text-muted-foreground/60 hover:text-foreground transition-colors rounded px-1 py-0.5 hover:bg-muted/60"
          >
            <ExternalLink className="h-3 w-3" />
            <span className="hidden sm:inline">打开</span>
          </button>
        </div>
      </div>

      {/* Diff 内容区 */}
      {useInline ? (
        <InlineDiffView changes={data.changes} layout={layout} />
      ) : (
        <GridDiffView changes={data.changes} layout={layout} profile={profile} mergeRanges={data.mergeRanges} oldMergeRanges={data.oldMergeRanges} />
      )}

      {/* Footer */}
      <div className="px-3 py-1 bg-muted/30 border-t border-border/50 text-[10px] text-muted-foreground/70 flex flex-wrap items-center gap-x-3 gap-y-0.5">
        <span className="font-medium">{total} 处变更</span>
        {counts.modified > 0 && (
          <span className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400 dark:bg-amber-500" />
            {counts.modified} 修改
          </span>
        )}
        {counts.added > 0 && (
          <span className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 dark:bg-green-400" />
            {counts.added} 新增
          </span>
        )}
        {counts.deleted > 0 && (
          <span className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-red-500 dark:bg-red-400" />
            {counts.deleted} 删除
          </span>
        )}
        {profile === "mixed" && (
          <span className="ml-auto text-muted-foreground/30 text-[9px]">{layout === "vertical" ? "↕" : "↔"}</span>
        )}
      </div>
      {/* 元数据提示 — 预览中无法展示的工作表特征 */}
      {data.metadataHints && data.metadataHints.length > 0 && (
        <div className="px-3 py-1 bg-blue-50/50 dark:bg-blue-950/20 border-t border-blue-100/60 dark:border-blue-900/30 text-[10px] text-blue-600/80 dark:text-blue-400/80 flex flex-wrap items-center gap-x-2.5 gap-y-0.5">
          <span className="font-medium text-blue-500/70 dark:text-blue-400/60 select-none">ℹ</span>
          {data.metadataHints.map((hint, i) => (
            <span key={i} className="inline-flex items-center gap-0.5 bg-blue-100/60 dark:bg-blue-900/30 rounded px-1.5 py-px">
              {hint}
            </span>
          ))}
          <span className="text-blue-400/50 dark:text-blue-500/40 ml-auto">打开文件查看完整效果</span>
        </div>
      )}
    </div>
  );
}
