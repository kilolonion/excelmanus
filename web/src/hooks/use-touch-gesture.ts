import { useCallback, useRef } from "react";

/**
 * 移动端 Excel 交互的触摸手势状态。
 *
 * 状态机：
 *   IDLE → touchstart → PENDING（启动长按定时器）
 *     ├─ 定时器触发前移动超过阈值 → SCROLLING → touchend → IDLE
 *     ├─ 定时器触发（长按）→ LONG_PRESS
 *     │    ├─ 拖动 → SELECTING → touchend → IDLE
 *     │    └─ 抬起 → IDLE（单单元格选择）
 *     └─ 定时器触发前抬起，移动小于阈值 → TAP → IDLE
 */
export type GestureState =
  | "idle"
  | "pending"
  | "scrolling"
  | "long_press"
  | "selecting";

export interface TouchGestureOptions {
  /** 触发长按前需保持的毫秒数。默认：400 */
  longPressMs?: number;
  /** 长按检测期间允许的移动像素数。默认：10 */
  moveThreshold?: number;
  /** 检测到长按时调用（应启用选区模式）。 */
  onLongPress?: (point: { x: number; y: number }) => void;
  /** 选区结束后手势结束时调用（应禁用选区模式）。 */
  onSelectionEnd?: () => void;
  /** 快速点击时调用（可选的单单元格选择）。 */
  onTap?: (point: { x: number; y: number }) => void;
}

/**
 * 移动端长按与滚动手势检测 Hook。
 * 返回需绑定到容器元素的触摸事件处理器。
 *
 * - 快速滑动 → 滚动（不干扰 Univer 原生滚动）
 * - 长按（~400ms）→ 启用选区模式 + 触觉反馈
 * - 选区完成后 → 恢复滚动模式
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
      // 触觉反馈不可用 — 静默失败
    }
  }, []);

  const handleTouchStart = useCallback(
    (e: React.TouchEvent) => {
      // 仅处理单指触摸；多指 → 透传（捏合缩放）
      if (e.touches.length !== 1) {
        clearTimer();
        stateRef.current = "idle";
        return;
      }

      const touch = e.touches[0];
      startPointRef.current = { x: touch.clientX, y: touch.clientY };
      startTimeRef.current = Date.now();
      stateRef.current = "pending";

      // 启动长按定时器
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
          // 定时器触发前移动过远 → 判定为滚动，取消长按
          if (distance > moveThreshold) {
            clearTimer();
            stateRef.current = "scrolling";
            // 不阻止默认行为 — 让 Univer 处理原生滚动
          }
          break;

        case "long_press":
          // 长按后的任何移动 → 用户正在拖拽选区
          if (distance > moveThreshold / 2) {
            stateRef.current = "selecting";
          }
          break;

        case "scrolling":
          // 已在滚动中 — Univer 原生处理
          break;

        case "selecting":
          // 已在选区中 — Univer 原生处理
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
          // 快速点击 — 非长按，无明显移动
          if (startPointRef.current && elapsed < longPressMs) {
            onTap?.(startPointRef.current);
          }
          break;

        case "long_press":
          // 检测到长按但无拖动 — 单单元格选择
          onSelectionEnd?.();
          break;

        case "selecting":
          // 拖拽选区完成
          onSelectionEnd?.();
          break;

        case "scrolling":
          // 滚动结束 — 无需操作
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
