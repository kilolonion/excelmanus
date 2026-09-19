import type { ViewRect, WorkbookViewSnapshot } from "@/lib/workbook-view";
import { cellRefFromIndex } from "@/lib/excel-cell-edit";

export function rangeIsLoaded(view: WorkbookViewSnapshot, sheet: string, rect: ViewRect): boolean {
  return firstUnloadedCell(view, sheet, rect) === null;
}

export function firstUnloadedCell(view: WorkbookViewSnapshot, sheet: string, rect: ViewRect): { row: number; col: number } | null {
  let remaining = [rect];
  for (const cover of view.coverage.loaded.filter((r) => r.sheet === sheet)) {
    remaining = remaining.flatMap((r) => {
      const r0 = Math.max(r.r0, cover.r0), r1 = Math.min(r.r1, cover.r1);
      const c0 = Math.max(r.c0, cover.c0), c1 = Math.min(r.c1, cover.c1);
      if (r0 > r1 || c0 > c1) return [r];
      return [
        { ...r, r1: r0 - 1 }, { ...r, r0: r1 + 1 },
        { r0, r1, c0: r.c0, c1: c0 - 1 }, { r0, r1, c0: c1 + 1, c1: r.c1 },
      ].filter((p) => p.r0 <= p.r1 && p.c0 <= p.c1);
    });
  }
  return remaining.length ? { row: remaining[0].r0 - 1, col: remaining[0].c0 - 1 } : null;
}

export function pageForCell(row: number, col: number): { rect: ViewRect; address: string } {
  const r0 = Math.floor(row / 200) * 200;
  const c0 = Math.floor(col / 50) * 50;
  const r1 = Math.min(r0 + 199, 1048575), c1 = Math.min(c0 + 49, 16383);
  return { rect: { r0: r0 + 1, c0: c0 + 1, r1: r1 + 1, c1: c1 + 1 },
    address: `${cellRefFromIndex(r0, c0)}:${cellRefFromIndex(r1, c1)}` };
}

export function mergeViewWindows(current: WorkbookViewSnapshot, next: WorkbookViewSnapshot): WorkbookViewSnapshot {
  if (current.content_version !== next.content_version || current.file.workspaceKey !== next.file.workspaceKey || current.file.relative !== next.file.relative) {
    throw new Error("STALE_VIEW: 不能合并不同文件或版本的范围");
  }
  const loaded = [...current.coverage.loaded];
  const windows = [...current.windows];
  for (const win of next.windows) {
    if (rangeIsLoaded(current, win.sheet, win.rect)) continue;
    windows.push(win);
    loaded.push({ ...win.rect, sheet: win.sheet });
  }
  return { ...current, windows, coverage: { ...current.coverage, loaded } };
}
