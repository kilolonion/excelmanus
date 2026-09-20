"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * 受控提示气泡状态：用于"看起来不可用但仍可交互"的控件。
 * 悬浮/聚焦走 Radix 默认的 onOpenChange；触屏点击不会触发焦点打开，
 * 由控件在 click 中调用 `show()` 手动打开，并自动消失兜底。
 */
export function useHintTooltip(autoDismissMs = 2400) {
  const [open, setOpen] = useState(false);
  const timerRef = useRef<number | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current != null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const show = useCallback(() => {
    clearTimer();
    setOpen(true);
    timerRef.current = window.setTimeout(() => setOpen(false), autoDismissMs);
  }, [autoDismissMs, clearTimer]);

  const onOpenChange = useCallback(
    (next: boolean) => {
      clearTimer();
      setOpen(next);
    },
    [clearTimer],
  );

  useEffect(() => clearTimer, [clearTimer]);

  return { open, onOpenChange, show };
}
