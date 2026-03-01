"use client";

import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";

interface TourOverlayProps {
  /** Bounding rect of the highlighted target element. null = no overlay. */
  targetRect: DOMRect | null;
  /** Padding around the cutout (px). */
  padding?: number;
  /** Border-radius of the cutout (px). */
  borderRadius?: number;
  /** Whether the cutout area should allow click-through (interactive steps). */
  allowInteraction: boolean;
  /** Whether to show the pulse animation on the highlight ring. */
  pulse: boolean;
  /** Overlay background opacity (0-1). */
  opacity?: number;
  /** When true, overlay is visual-only (pointer-events: none everywhere).
   *  Used for drag/input steps where gestures must cross overlay boundaries
   *  or where popups (e.g. slash command menu) must remain accessible. */
  passThrough?: boolean;
}

/**
 * Self-made tour overlay engine.
 *
 * Uses a single div with a massive box-shadow to create a semi-transparent
 * overlay with a rounded-corner cutout around the target element.
 *
 * - The box-shadow spread (9999px) covers the full viewport.
 * - border-radius on the cutout div naturally rounds the overlay hole.
 * - A highlight ring with optional pulse animation sits around the cutout.
 */
export function TourOverlay({
  targetRect,
  padding = 6,
  borderRadius = 8,
  allowInteraction,
  pulse,
  opacity = 0.5,
  passThrough = false,
}: TourOverlayProps) {
  if (!targetRect) return null;

  // Cutout bounds (clamped to viewport)
  const cx = Math.max(0, targetRect.x - padding);
  const cy = Math.max(0, targetRect.y - padding);
  const cw = targetRect.width + padding * 2;
  const ch = targetRect.height + padding * 2;
  const overlayColor = `rgba(0, 0, 0, ${opacity})`;

  // Build highlight box-shadow: huge spread for overlay + coloured ring
  const highlightShadow = pulse
    ? `0 0 0 9999px ${overlayColor}`
    : `0 0 0 9999px ${overlayColor}, 0 0 0 2px var(--em-primary), 0 0 12px 2px var(--em-primary-alpha-15)`;

  return createPortal(
    <AnimatePresence>
      <motion.div
        key="tour-overlay"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.25 }}
        className="fixed inset-0 z-[10000]"
        style={{ pointerEvents: "none" }}
      >
        {/* ── Overlay + highlight ring (single box-shadow approach) ──
         *  A single div with a massive box-shadow creates the semi-transparent
         *  overlay while naturally respecting border-radius for the cutout. */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className={`absolute ${pulse ? "coach-highlight-pulse" : ""}`}
          style={{
            top: cy,
            left: cx,
            width: cw,
            height: ch,
            borderRadius,
            boxShadow: highlightShadow,
            pointerEvents: (passThrough || allowInteraction) ? "none" : "auto",
            transition: "top 0.25s ease, left 0.25s ease, width 0.25s ease, height 0.25s ease",
          }}
        />

        {/* ── Cutout: allow or block interaction ── */}
        {!passThrough && (
          <div
            className="absolute"
            style={{
              top: cy,
              left: cx,
              width: cw,
              height: ch,
              borderRadius,
              pointerEvents: allowInteraction ? "none" : "auto",
            }}
          />
        )}
      </motion.div>
    </AnimatePresence>,
    document.body,
  );
}
