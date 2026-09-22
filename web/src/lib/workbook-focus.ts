import type { ISelectionStyle } from "@univerjs/sheets";

/** Split at the last bang: quoted sheet names may themselves contain !. */
export function splitWorkbookRangeSpec(spec?: string): { sheet?: string; range?: string } {
  if (!spec) return {};
  const bang = spec.lastIndexOf("!");
  const sheet = bang >= 0 ? spec.slice(0, bang) : undefined;
  return {
    ...(sheet ? { sheet: sheet.startsWith("'") && sheet.endsWith("'") ? sheet.slice(1, -1).replace(/''/g, "'") : sheet } : {}),
    range: spec.slice(bang + 1),
  };
}

export function workbookFocusRanges(range?: string): string[] {
  if (!range || range.length > 4096) return [];
  const ranges = range.split(",").map((part) => part.trim().replace(/\$/g, "").toUpperCase());
  if (ranges.length > 64 || ranges.some((part) => !/^(?:[A-Z]{1,3}[1-9]\d{0,6}(?::[A-Z]{1,3}[1-9]\d{0,6})?|[A-Z]{1,3}:[A-Z]{1,3}|[1-9]\d{0,6}:[1-9]\d{0,6})$/.test(part))) return [];
  if (ranges.some((part) => (part.match(/\d+/g) ?? []).some((row) => Number(row) > 1048576)
    || (part.match(/[A-Z]+/g) ?? []).some((column) => [...column].reduce((value, char) => value * 26 + char.charCodeAt(0) - 64, 0) > 16384))) return [];
  return [...new Set(ranges)];
}

type HighlightStyle = Partial<ISelectionStyle> & { fill: string; stroke: string };
export interface WorkbookHighlightRegistry {
  getShapeMap: () => ReadonlyMap<string, {
    selection: { style?: Partial<ISelectionStyle> | null | void };
    control: {
      setEvent: (enabled: boolean) => void;
      updateStyle: (style: Partial<ISelectionStyle>) => void;
    } | null;
  }>;
}

/** View-only breathing window. Native marks keep scroll, zoom and frozen panes aligned. */
export function highlightWorkbookRanges<R>(sheet: {
  getRange: (address: string) => R;
  highlightRanges: (ranges: R[], style: HighlightStyle) => { dispose: () => void };
}, addresses: string[], stage?: "selection" | "inspect" | "planned" | "changed", options?: {
  registry: WorkbookHighlightRegistry;
  isActive: () => boolean;
}) {
  const color = stage === "planned" ? "245,158,11" : stage === "selection" || stage === "inspect" ? "59,130,246" : "16,185,129";
  const ranges = addresses.map((address) => sheet.getRange(address));
  const rgba = (opacity: number) => `rgba(${color},${opacity.toFixed(3)})`;
  const outline: HighlightStyle = { fill: rgba(0.06), stroke: rgba(0.85), strokeWidth: 1.8, widgets: {}, isAnimationDash: false };
  const halo: HighlightStyle = { fill: "transparent", stroke: rgba(0.09), strokeWidth: 7, widgets: {}, isAnimationDash: false };
  const overlays: { dispose: () => void }[] = [];
  try {
    if (options) overlays.push(sheet.highlightRanges(ranges, halo));
    overlays.push(sheet.highlightRanges(ranges, outline));
  } catch (error) {
    overlays.forEach((overlay) => overlay.dispose());
    throw error;
  }

  // Remember only our marks. Never restyle normal selection, copy or formula marks.
  const marks = options ? [...options.registry.getShapeMap()]
    .filter(([, shape]) => shape.selection.style === halo || shape.selection.style === outline)
    .map(([id]) => id) : [];
  let disposed = false;
  let frame: number | undefined;
  let lastPaint: number | undefined;
  let elapsed = 0;
  const motion = typeof window !== "undefined" ? window.matchMedia?.("(prefers-reduced-motion: reduce)") : undefined;
  const stop = () => {
    if (frame !== undefined) cancelAnimationFrame(frame);
    frame = undefined;
    lastPaint = undefined;
  };
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    stop();
    motion?.removeEventListener("change", syncMotion);
    if (typeof document !== "undefined") document.removeEventListener("visibilitychange", syncMotion);
    overlays.forEach((overlay) => overlay.dispose());
  };
  const paint = (breath: number, arrival = 0) => {
    if (!options || disposed) return;
    if (!options.isActive()) { dispose(); return; }
    Object.assign(outline, { fill: rgba(0.05 + breath * 0.02 + arrival * 0.025), stroke: rgba(0.65 + breath * 0.28 + arrival * 0.07), strokeWidth: 1.6 + breath * 0.5 });
    Object.assign(halo, { stroke: rgba(0.06 + breath * 0.06 + arrival * 0.02), strokeWidth: 6 + breath * 3 + arrival * 5 });
    let remaining = 0;
    const shapes = options.registry.getShapeMap();
    for (const id of marks) {
      const shape = shapes.get(id);
      if (!shape) continue;
      remaining++;
      // Skeleton refresh replaces controls; read the current control on every paint.
      // Styles live only in the mark service, never in workbook cell data.
      const style = shape.selection.style === halo ? halo : outline;
      shape.control?.setEvent(false);
      shape.control?.updateStyle(style);
    }
    if (!remaining) dispose();
  };
  const animate = (now: number) => {
    frame = undefined;
    if (disposed) return;
    // One shared clock for every area, capped at 30 paints/s. No per-frame allocations
    // of native shapes (refreshShapes rebuilds every mark in the sheet).
    if (lastPaint === undefined || now - lastPaint >= 1000 / 30 - 1) {
      elapsed += lastPaint === undefined ? 0 : now - lastPaint;
      lastPaint = now;
      paint((1 + Math.cos(elapsed * Math.PI * 2 / 2800)) / 2, Math.pow(Math.max(0, 1 - elapsed / 700), 3));
    }
    if (!disposed) frame = requestAnimationFrame(animate);
  };
  function syncMotion() {
    stop();
    if (disposed) return;
    if (motion?.matches || document.hidden) paint(0.5);
    else frame = requestAnimationFrame(animate);
  }
  if (marks.length && typeof requestAnimationFrame === "function" && typeof document !== "undefined") {
    paint(0.5);
    if (!disposed) {
      motion?.addEventListener("change", syncMotion);
      document.addEventListener("visibilitychange", syncMotion);
      syncMotion();
    }
  }
  return { dispose };
}
