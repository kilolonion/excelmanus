"use client";

import { createPortal } from "react-dom";
import { useReducedMotion } from "framer-motion";
import { getSpotlightRect } from "./tour-layout";
import { useGuideViewport } from "./useTargetRect";

/** A visual spotlight. Scrolling, touch, keyboard and popup controls stay usable. */
export function TourOverlay({ targetRect, padding = 6 }: { targetRect: DOMRect | null; padding?: number }) {
  const reducedMotion = useReducedMotion();
  const viewport = useGuideViewport();
  const spotlightRect = getSpotlightRect(targetRect, viewport, padding);
  if (!spotlightRect) return null;
  return createPortal(
    <div aria-hidden="true" className="em-tour-spotlight" style={{
      top: spotlightRect.top, left: spotlightRect.left,
      width: spotlightRect.width, height: spotlightRect.height,
      transition: reducedMotion ? "none" : "top 160ms ease, left 160ms ease, width 160ms ease, height 160ms ease",
    }} />,
    document.body,
  );
}
