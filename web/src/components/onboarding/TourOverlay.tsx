"use client";

import { createPortal } from "react-dom";
import { useReducedMotion } from "framer-motion";

/** A visual spotlight. Scrolling, touch, keyboard and popup controls stay usable. */
export function TourOverlay({ targetRect, padding = 6 }: { targetRect: DOMRect | null; padding?: number }) {
  const reducedMotion = useReducedMotion();
  if (!targetRect) return null;
  return createPortal(
    <div aria-hidden="true" className="em-tour-spotlight" style={{
      top: targetRect.top - padding, left: targetRect.left - padding,
      width: targetRect.width + padding * 2, height: targetRect.height + padding * 2,
      transition: reducedMotion ? "none" : "top 160ms ease, left 160ms ease, width 160ms ease, height 160ms ease",
    }} />,
    document.body,
  );
}
