"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const STORAGE_KEY = "excel-panel-width";
const DEFAULT_WIDTH = 520;
const MIN_WIDTH = 360;
const MAX_WIDTH_RATIO = 0.85; // 视口宽度的 85%
const FLOAT_THRESHOLD_RATIO = 0.50; // 超过视口 50% 切换为浮动

export interface ResizablePanelState {
  /** 当前面板宽度 */
  panelWidth: number;
  /** 是否因为宽度超过阈值而进入浮动模式 */
  isFloatingByResize: boolean;
  /** 拖拽手柄的 props，直接展开到手柄元素上 */
  handleProps: {
    onMouseDown: (e: React.MouseEvent) => void;
    onDoubleClick: () => void;
  };
  /** 拖拽进行中 */
  isDragging: boolean;
  /** 重置为默认宽度 */
  resetWidth: () => void;
}

function loadWidth(): number {
  if (typeof window === "undefined") return DEFAULT_WIDTH;
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v) {
      const n = Number(v);
      if (n >= MIN_WIDTH && n <= window.innerWidth * MAX_WIDTH_RATIO) return n;
    }
  } catch { /* ignore */ }
  return DEFAULT_WIDTH;
}

function saveWidth(w: number) {
  try {
    localStorage.setItem(STORAGE_KEY, String(Math.round(w)));
  } catch { /* ignore */ }
}

export function useResizablePanel(enabled: boolean): ResizablePanelState {
  const [panelWidth, setPanelWidth] = useState(loadWidth);
  const [isDragging, setIsDragging] = useState(false);
  const [viewportWidth, setViewportWidth] = useState(
    () => (typeof window === "undefined" ? 1920 : window.innerWidth)
  );
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);

  // 视口宽度变化时确保不越界 + 更新 viewportWidth
  useEffect(() => {
    if (!enabled) return;
    const onResize = () => {
      const vw = window.innerWidth;
      setViewportWidth(vw);
      const maxW = vw * MAX_WIDTH_RATIO;
      setPanelWidth((prev) => {
        if (prev > maxW) return Math.max(MIN_WIDTH, maxW);
        return prev;
      });
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [enabled]);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (!enabled) return;
      e.preventDefault();
      e.stopPropagation();
      startXRef.current = e.clientX;
      startWidthRef.current = panelWidth;
      setIsDragging(true);
    },
    [enabled, panelWidth]
  );

  // 全局 mousemove / mouseup
  useEffect(() => {
    if (!isDragging) return;

    const onMouseMove = (e: MouseEvent) => {
      const maxW = window.innerWidth * MAX_WIDTH_RATIO;
      // 面板在右侧，鼠标左移 → 宽度增加
      const delta = startXRef.current - e.clientX;
      const newW = Math.min(maxW, Math.max(MIN_WIDTH, startWidthRef.current + delta));
      setPanelWidth(newW);
    };

    const onMouseUp = () => {
      setIsDragging(false);
      // 保存最终宽度
      setPanelWidth((w) => {
        saveWidth(w);
        return w;
      });
    };

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [isDragging]);

  // 拖拽中禁用文本选择
  useEffect(() => {
    if (isDragging) {
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    } else {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    }
  }, [isDragging]);

  const resetWidth = useCallback(() => {
    setPanelWidth(DEFAULT_WIDTH);
    saveWidth(DEFAULT_WIDTH);
  }, []);

  const onDoubleClick = useCallback(() => {
    resetWidth();
  }, [resetWidth]);

  const isFloatingByResize =
    enabled && panelWidth > viewportWidth * FLOAT_THRESHOLD_RATIO;

  return {
    panelWidth,
    isFloatingByResize,
    handleProps: { onMouseDown, onDoubleClick },
    isDragging,
    resetWidth,
  };
}

export { DEFAULT_WIDTH, MIN_WIDTH, FLOAT_THRESHOLD_RATIO };
