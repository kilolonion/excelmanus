import { useCallback, useRef } from "react";

/**
 * Touch gesture states for mobile Excel interaction.
 *
 * State machine:
 *   IDLE → touchstart → PENDING (start long-press timer)
 *     ├─ move > threshold before timer → SCROLLING → touchend → IDLE
 *     ├─ timer fires (long press) → LONG_PRESS
 *     │    ├─ drag → SELECTING → touchend → IDLE
 *     │    └─ lift → IDLE (single cell select)
 *     └─ lift before timer, < threshold → TAP → IDLE
 */
export type GestureState =
  | "idle"
  | "pending"
  | "scrolling"
  | "long_press"
  | "selecting";

export interface TouchGestureOptions {
  /** Milliseconds to hold before triggering long press. Default: 400 */
  longPressMs?: number;
  /** Pixels of movement allowed during long press detection. Default: 10 */
  moveThreshold?: number;
  /** Called when long press is detected (selection mode should be enabled). */
  onLongPress?: (point: { x: number; y: number }) => void;
  /** Called when gesture ends after selection (selection mode should be disabled). */
  onSelectionEnd?: () => void;
  /** Called on a quick tap (optional single-cell select). */
  onTap?: (point: { x: number; y: number }) => void;
}

/**
 * Hook for detecting long-press vs scroll gestures on mobile.
 * Returns touch event handlers to attach to a container element.
 *
 * - Quick swipe → scroll (no interference with Univer's native scroll)
 * - Long press (~400ms) → enable selection mode + haptic feedback
 * - After selection complete → back to scroll mode
 */
export function useTouchGesture(options: TouchGestureOptions = {}) {
  const {
    longPressMs = 400,
    moveThreshold = 10,
    onLongPress,
    onSelectionEnd,
    onTap,
  } = options;

  const stateRef = useRef<GestureState>("idle");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startPointRef = useRef<{ x: number; y: number } | null>(null);
  const startTimeRef = useRef<number>(0);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const triggerHaptic = useCallback(() => {
    try {
      if (navigator.vibrate) {
        navigator.vibrate(30);
      }
    } catch {
      // Haptic not available — silent fail
    }
  }, []);

  const handleTouchStart = useCallback(
    (e: React.TouchEvent) => {
      // Only handle single-finger touches; multi-finger → pass through (pinch zoom)
      if (e.touches.length !== 1) {
        clearTimer();
        stateRef.current = "idle";
        return;
      }

      const touch = e.touches[0];
      startPointRef.current = { x: touch.clientX, y: touch.clientY };
      startTimeRef.current = Date.now();
      stateRef.current = "pending";

      // Start long-press timer
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        if (stateRef.current === "pending" && startPointRef.current) {
          stateRef.current = "long_press";
          triggerHaptic();
          onLongPress?.(startPointRef.current);
        }
      }, longPressMs);
    },
    [longPressMs, onLongPress, clearTimer, triggerHaptic]
  );

  const handleTouchMove = useCallback(
    (e: React.TouchEvent) => {
      if (e.touches.length !== 1) return;

      const touch = e.touches[0];
      const start = startPointRef.current;
      if (!start) return;

      const dx = touch.clientX - start.x;
      const dy = touch.clientY - start.y;
      const distance = Math.sqrt(dx * dx + dy * dy);

      switch (stateRef.current) {
        case "pending":
          // Moved too far before timer → it's a scroll, cancel long-press
          if (distance > moveThreshold) {
            clearTimer();
            stateRef.current = "scrolling";
            // Don't prevent default — let Univer handle native scroll
          }
          break;

        case "long_press":
          // Any movement after long press → user is dragging to select
          if (distance > moveThreshold / 2) {
            stateRef.current = "selecting";
          }
          break;

        case "scrolling":
          // Already scrolling — Univer handles it natively
          break;

        case "selecting":
          // Already selecting — Univer handles it natively
          break;
      }
    },
    [moveThreshold, clearTimer]
  );

  const handleTouchEnd = useCallback(
    (_e: React.TouchEvent) => {
      clearTimer();
      const elapsed = Date.now() - startTimeRef.current;
      const state = stateRef.current;

      switch (state) {
        case "pending":
          // Quick tap — no long press, no significant movement
          if (startPointRef.current && elapsed < longPressMs) {
            onTap?.(startPointRef.current);
          }
          break;

        case "long_press":
          // Long press detected but no drag — single cell selection
          onSelectionEnd?.();
          break;

        case "selecting":
          // Drag selection complete
          onSelectionEnd?.();
          break;

        case "scrolling":
          // Scroll ended — nothing to do
          break;
      }

      stateRef.current = "idle";
      startPointRef.current = null;
    },
    [longPressMs, onTap, onSelectionEnd, clearTimer]
  );

  const handleTouchCancel = useCallback(() => {
    clearTimer();
    if (
      stateRef.current === "long_press" ||
      stateRef.current === "selecting"
    ) {
      onSelectionEnd?.();
    }
    stateRef.current = "idle";
    startPointRef.current = null;
  }, [clearTimer, onSelectionEnd]);

  return {
    gestureState: stateRef,
    handlers: {
      onTouchStart: handleTouchStart,
      onTouchMove: handleTouchMove,
      onTouchEnd: handleTouchEnd,
      onTouchCancel: handleTouchCancel,
    },
  };
}
