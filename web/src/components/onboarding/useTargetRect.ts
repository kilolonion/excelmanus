import { useState, useEffect, useRef } from "react";

/**
 * Find the nearest scrollable ancestor of an element.
 * Returns null if no scrollable ancestor is found (or it's the viewport).
 */
function findScrollableAncestor(el: Element): Element | null {
  let parent = el.parentElement;
  while (parent) {
    const style = getComputedStyle(parent);
    const overflowY = style.overflowY;
    const overflowX = style.overflowX;
    if (
      (overflowY === "auto" || overflowY === "scroll" || overflowY === "hidden") &&
      parent.scrollHeight > parent.clientHeight
    ) {
      return parent;
    }
    if (
      (overflowX === "auto" || overflowX === "scroll" || overflowX === "hidden") &&
      parent.scrollWidth > parent.clientWidth
    ) {
      return parent;
    }
    parent = parent.parentElement;
  }
  return null;
}

/**
 * Clip a DOMRect to the visible bounds of a container element.
 * Returns null if the clipped rect is too small (fully hidden).
 */
function clipRectToContainer(r: DOMRect, container: Element): DOMRect | null {
  const cr = container.getBoundingClientRect();
  const x = Math.max(r.x, cr.x);
  const y = Math.max(r.y, cr.y);
  const right = Math.min(r.right, cr.right);
  const bottom = Math.min(r.bottom, cr.bottom);
  const w = right - x;
  const h = bottom - y;
  if (w < 2 || h < 2) return null;
  return DOMRect.fromRect({ x, y, width: w, height: h });
}

/**
 * Tracks a DOM element's bounding rect via requestAnimationFrame.
 * Returns null if the element is not found or too small (< 2px).
 * Automatically clips the rect to the nearest scrollable ancestor's visible bounds.
 */
export function useTargetRect(target: string, expandTarget?: string): DOMRect | null {
  const [rect, setRect] = useState<DOMRect | null>(null);
  const prevKey = useRef("");

  useEffect(() => {
    if (!target) {
      if (prevKey.current !== "null") {
        prevKey.current = "null";
        setRect(null);
      }
      return;
    }

    let rafId: number | null = null;

    const selector = target.startsWith("[")
      ? target
      : `[data-coach-id="${target}"]`;

    const track = () => {
      const el = document.querySelector(selector);
      if (el) {
        let r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) {
          if (prevKey.current !== "null") {
            prevKey.current = "null";
            setRect(null);
          }
        } else {
          // Clip to nearest scrollable ancestor so overlay cutout
          // doesn't extend beyond the visible scroll container
          const scrollParent = findScrollableAncestor(el);
          if (scrollParent) {
            const clipped = clipRectToContainer(r, scrollParent);
            if (!clipped) {
              if (prevKey.current !== "null") {
                prevKey.current = "null";
                setRect(null);
              }
              rafId = requestAnimationFrame(track);
              return;
            }
            r = clipped;
          }

          // Expand to include secondary target (e.g. a popover) if present
          if (expandTarget) {
            const expandSel = expandTarget.startsWith("[")
              ? expandTarget
              : `[data-coach-id="${expandTarget}"]`;
            const expandEl = document.querySelector(expandSel);
            if (expandEl) {
              const er = expandEl.getBoundingClientRect();
              if (er.width >= 2 && er.height >= 2) {
                const minX = Math.min(r.x, er.x);
                const minY = Math.min(r.y, er.y);
                const maxR = Math.max(r.right, er.right);
                const maxB = Math.max(r.bottom, er.bottom);
                r = DOMRect.fromRect({ x: minX, y: minY, width: maxR - minX, height: maxB - minY });
              }
            }
          }

          const key = `${r.x},${r.y},${r.width},${r.height}`;
          if (key !== prevKey.current) {
            prevKey.current = key;
            setRect(DOMRect.fromRect(r));
          }
        }
      } else {
        if (prevKey.current !== "null") {
          prevKey.current = "null";
          setRect(null);
        }
      }
      rafId = requestAnimationFrame(track);
    };

    rafId = requestAnimationFrame(track);
    return () => {
      if (rafId !== null) cancelAnimationFrame(rafId);
    };
  }, [target, expandTarget]);

  return rect;
}
