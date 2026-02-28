"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { ChevronDown, ChevronUp, Maximize2 } from "lucide-react";

interface ScrollablePreviewProps {
  children: React.ReactNode;
  /** 折叠态最大高度(px)，默认 160 */
  collapsedHeight?: number;
  /** 展开态最大高度(px)，默认 420 */
  expandedHeight?: number;
  /** 额外 className */
  className?: string;
  /** 流式模式：自动跳过折叠态，内容增长时自动滚到底部 */
  autoScroll?: boolean;
}

/**
 * 可折叠滚动预览容器（三态）。
 * - collapsed: 内容超出 collapsedHeight 时截断，显示渐变遮罩 + 点击提示
 * - scroll:    在 collapsedHeight 内可滚动，上下边缘显示方向指示按钮
 * - expanded:  在 expandedHeight 内可滚动，同样带方向指示
 */
export function ScrollablePreview({
  children,
  collapsedHeight = 160,
  expandedHeight = 420,
  className = "",
  autoScroll = false,
}: ScrollablePreviewProps) {
  const innerRef = useRef<HTMLDivElement>(null);
  const [overflows, setOverflows] = useState(false);
  const [mode, setMode] = useState<"collapsed" | "scroll" | "expanded">("collapsed");
  const [canScrollUp, setCanScrollUp] = useState(false);
  const [canScrollDown, setCanScrollDown] = useState(false);

  // ── Detect content overflow ──
  const checkOverflow = useCallback(() => {
    const el = innerRef.current;
    if (!el) return;
    const nowOverflows = el.scrollHeight > collapsedHeight + 4;
    setOverflows(nowOverflows);
    // autoScroll: 溢出时自动跳过折叠态
    if (autoScroll && nowOverflows) {
      setMode((prev) => (prev === "collapsed" ? "scroll" : prev));
    }
  }, [collapsedHeight, autoScroll]);

  useEffect(() => {
    checkOverflow();
    const el = innerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(checkOverflow);
    ro.observe(el);
    return () => ro.disconnect();
  }, [checkOverflow]);

  // ── autoScroll: 内容增长时自动滚到底部 ──
  useEffect(() => {
    if (!autoScroll) return;
    const el = innerRef.current;
    if (!el) return;
    // 使用 MutationObserver 监听子节点变化（流式追加行）
    const scrollToBottom = () => {
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight;
      });
    };
    const mo = new MutationObserver(scrollToBottom);
    mo.observe(el, { childList: true, subtree: true, characterData: true });
    // 初始也滚一次
    scrollToBottom();
    return () => mo.disconnect();
  }, [autoScroll]);

  // ── Track scroll position for directional indicators ──
  const syncScroll = useCallback(() => {
    const el = innerRef.current;
    if (!el) return;
    setCanScrollUp(el.scrollTop > 2);
    setCanScrollDown(el.scrollTop + el.clientHeight < el.scrollHeight - 2);
  }, []);

  useEffect(() => {
    if (mode === "collapsed") return;
    const el = innerRef.current;
    if (!el) return;
    requestAnimationFrame(syncScroll);
    el.addEventListener("scroll", syncScroll, { passive: true });
    const ro = new ResizeObserver(syncScroll);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", syncScroll);
      ro.disconnect();
    };
  }, [mode, syncScroll]);

  const handleCollapse = useCallback(() => {
    if (innerRef.current) innerRef.current.scrollTop = 0;
    setMode("collapsed");
  }, []);

  const scrollStep = useCallback(
    (dir: "up" | "down") => {
      const el = innerRef.current;
      if (!el) return;
      const h = mode === "expanded" ? expandedHeight : collapsedHeight;
      el.scrollBy({ top: dir === "up" ? -h * 0.75 : h * 0.75, behavior: "smooth" });
    },
    [mode, collapsedHeight, expandedHeight],
  );

  // 内容不溢出：直接渲染，无任何限制
  if (!overflows) {
    return (
      <div className={className}>
        <div ref={innerRef}>{children}</div>
      </div>
    );
  }

  const isActive = mode !== "collapsed";
  const maxH = mode === "expanded" ? expandedHeight : collapsedHeight;

  return (
    <div className={className}>
      {/* Scroll area wrapper */}
      <div className="relative">
        <div
          ref={innerRef}
          className={`transition-[max-height] duration-300 ease-in-out ${
            isActive ? "overflow-y-auto overflow-x-auto" : "overflow-hidden"
          }`}
          style={{ maxHeight: maxH, touchAction: "pan-x pan-y" }}
        >
          {children}
        </div>

        {/* 折叠态：底部渐变遮罩 + 点击激活滚动 */}
        {mode === "collapsed" && (
          <div
            className="absolute bottom-0 left-0 right-0 cursor-pointer group/act"
            onClick={() => setMode("scroll")}
          >
            <div className="h-10 bg-gradient-to-t from-background/95 via-background/60 to-transparent" />
            <div className="flex items-center justify-center gap-1 py-1 bg-background/95 text-[10px] text-muted-foreground group-hover/act:text-foreground transition-colors">
              <ChevronDown className="h-3 w-3" />
              <span>点击展开滚动查看</span>
            </div>
          </div>
        )}

        {/* 滚动态/展开态：顶部向上滚动指示 */}
        {isActive && canScrollUp && (
          <div className="absolute top-0 inset-x-0 z-10 pointer-events-none">
            <div className="h-6 bg-gradient-to-b from-background/80 to-transparent" />
            <div className="absolute top-0 inset-x-0 flex justify-center pointer-events-auto">
              <button
                type="button"
                onClick={() => scrollStep("up")}
                className="flex items-center justify-center h-5 w-7 rounded-b-md bg-background/80 backdrop-blur-sm border border-t-0 border-border/50 text-muted-foreground/60 hover:text-foreground hover:bg-background transition-all shadow-sm"
              >
                <ChevronUp className="h-3 w-3" />
              </button>
            </div>
          </div>
        )}

        {/* 滚动态/展开态：底部向下滚动指示 */}
        {isActive && canScrollDown && (
          <div className="absolute bottom-0 inset-x-0 z-10 pointer-events-none">
            <div className="h-6 bg-gradient-to-t from-background/80 to-transparent" />
            <div className="absolute bottom-0 inset-x-0 flex justify-center pointer-events-auto">
              <button
                type="button"
                onClick={() => scrollStep("down")}
                className="flex items-center justify-center h-5 w-7 rounded-t-md bg-background/80 backdrop-blur-sm border border-b-0 border-border/50 text-muted-foreground/60 hover:text-foreground hover:bg-background transition-all shadow-sm"
              >
                <ChevronDown className="h-3 w-3" />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 滚动态/展开态：底部控制栏 */}
      {isActive && (
        <div className="flex items-center justify-center gap-3 py-1 text-[10px] text-muted-foreground border-t border-border/30 bg-muted/20">
          {mode === "scroll" && (
            <>
              <button
                type="button"
                className="flex items-center gap-1 hover:text-foreground transition-colors"
                onClick={() => setMode("expanded")}
              >
                <Maximize2 className="h-3 w-3" />
                <span>展开更大区域</span>
              </button>
              <span className="text-muted-foreground/20">·</span>
            </>
          )}
          <button
            type="button"
            className="flex items-center gap-1 hover:text-foreground transition-colors"
            onClick={handleCollapse}
          >
            <ChevronUp className="h-3 w-3" />
            <span>收起</span>
          </button>
        </div>
      )}
    </div>
  );
}
