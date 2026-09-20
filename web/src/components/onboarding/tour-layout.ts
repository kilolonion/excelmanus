import type { GuideViewport } from "./useTargetRect";

type Rect = { left: number; top: number; right: number; bottom: number; width: number; height: number };
type Placement = "top" | "bottom" | "left" | "right";

/** Mobile uses a compact teaching card until the user explicitly opens a practice. */
export function getTourCardMaxHeight(viewportHeight: number, isMobile: boolean, practiceExpanded: boolean) {
  const available = Math.max(1, viewportHeight - 20);
  if (!isMobile) return available;
  const desired = practiceExpanded
    ? Math.max(260, viewportHeight * 0.7)
    : Math.max(220, Math.min(320, viewportHeight * 0.48));
  return Math.min(available, desired);
}

/** Expand a target for the spotlight without drawing outside the safe visual viewport. */
export function getSpotlightRect(target: Rect | null, viewport: GuideViewport, padding: number) {
  if (!target) return null;
  const viewportRight = viewport.left + viewport.width;
  const viewportBottom = viewport.top + viewport.height;
  const left = Math.max(viewport.left, target.left - padding);
  const top = Math.max(viewport.top, target.top - padding);
  const right = Math.min(viewportRight, target.right + padding);
  const bottom = Math.min(viewportBottom, target.bottom + padding);
  if (right - left < 1 || bottom - top < 1) return null;
  return { left, top, width: right - left, height: bottom - top };
}

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
