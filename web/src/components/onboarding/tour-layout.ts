import type { GuideViewport } from "./useTargetRect";

type Rect = { left: number; top: number; right: number; bottom: number; width: number; height: number };
type Placement = "top" | "bottom" | "left" | "right";

/** Prefer the requested side, then the side with the least target overlap.
 * The card is always inside the visual viewport, including landscape keyboards. */
export function placeTourCard(target: Rect | null, viewport: GuideViewport, width: number, height: number, placement: Placement) {
  const margin = 10;
  const gap = 14;
  const w = Math.min(width, Math.max(1, viewport.width - margin * 2));
  const h = Math.min(height, Math.max(1, viewport.height - margin * 2));
  const clamp = (x: number, min: number, max: number) => Math.max(min, Math.min(x, max));
  const bound = (left: number, top: number) => ({
    left: clamp(left, viewport.left + margin, viewport.left + viewport.width - w - margin),
    top: clamp(top, viewport.top + margin, viewport.top + viewport.height - h - margin),
  });
  if (!target) return { ...bound(viewport.left + (viewport.width - w) / 2, viewport.top + (viewport.height - h) / 2), width: w, maxHeight: viewport.height - margin * 2 };
  const positions = {
    top: { left: target.left + (target.width - w) / 2, top: target.top - h - gap },
    bottom: { left: target.left + (target.width - w) / 2, top: target.bottom + gap },
    left: { left: target.left - w - gap, top: target.top + (target.height - h) / 2 },
    right: { left: target.right + gap, top: target.top + (target.height - h) / 2 },
  };
  const order: Placement[] = [placement, ...(["top", "bottom", "right", "left"] as Placement[]).filter((p) => p !== placement)];
  const options = order.map((p) => {
    const pos = bound(positions[p].left, positions[p].top);
    const overlap = Math.max(0, Math.min(pos.left + w, target.right) - Math.max(pos.left, target.left))
      * Math.max(0, Math.min(pos.top + h, target.bottom) - Math.max(pos.top, target.top));
    return { ...pos, overlap };
  });
  options.sort((a, b) => a.overlap - b.overlap);
  return { left: options[0].left, top: options[0].top, width: w, maxHeight: viewport.height - margin * 2 };
}
