"use client";

import { useState, useRef, useEffect, useLayoutEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Brain, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatThinkingDuration, thinkingPreview } from "@/lib/thinking";

interface ThinkingBlockProps {
  content: string;
  duration?: number;
  startedAt?: number;
  isActive?: boolean;
  title?: string;
  defaultExpanded?: boolean;
}

const FADE_MASK =
  "linear-gradient(to bottom, transparent 0%, black 14%, black 86%, transparent 100%)";
const ACTIVE_MAX_H = "6.5rem";
const AUTO_FOLLOW_THRESHOLD_PX = 12;

export function isNearScrollBottom(
  scrollTop: number,
  clientHeight: number,
  scrollHeight: number,
): boolean {
  return scrollHeight - scrollTop - clientHeight <= AUTO_FOLLOW_THRESHOLD_PX;
}

export function ThinkingBlock({
  content,
  duration,
  startedAt,
  isActive = false,
  title,
  defaultExpanded = false,
}: ThinkingBlockProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [elapsed, setElapsed] = useState(0);
  const contentRef = useRef<HTMLDivElement>(null);
  // 流式内容默认跟随底部；一旦用户向上查看历史内容，就由用户接管滚动位置。
  const autoFollowRef = useRef(true);

  useEffect(() => {
    if (!isActive) return;
    const origin = startedAt ?? Date.now();
    const tick = () => setElapsed(Math.round((Date.now() - origin) / 1000));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [isActive, startedAt]);

  useEffect(() => {
    if (isActive) autoFollowRef.current = true;
  }, [isActive]);

  useLayoutEffect(() => {
    if (isActive && autoFollowRef.current && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [content, isActive]);

  const seconds = isActive
    ? elapsed
    : duration != null
      ? Math.round(duration)
      : 0;
  const durationStr = formatThinkingDuration(seconds);
  const heading = title ?? (isActive ? "思考中" : "思考完成");
  const showBody = (isActive || expanded) && !!content;
  const preview = !isActive && !expanded ? thinkingPreview(content) : "";

  return (
    <div className="my-2 rounded-2xl border border-[var(--em-hairline)] bg-background overflow-hidden">
      <button
        type="button"
        onClick={() => !isActive && setExpanded((v) => !v)}
        disabled={isActive}
        aria-expanded={isActive || expanded}
        aria-label={isActive ? heading : expanded ? "收起思考" : "展开思考"}
        className={cn(
          "flex w-full items-center gap-2 px-3 sm:px-3.5 py-2.5 text-left",
          !isActive && "hover:bg-[var(--em-fill)] transition-colors",
          isActive && "cursor-default",
        )}
      >
        <Brain
          className={cn(
            "h-4 w-4 flex-shrink-0",
            isActive
              ? "text-muted-foreground animate-tool-running-pulse"
              : "text-[var(--em-primary)]",
          )}
        />
        <span className="text-[13px] font-medium text-foreground whitespace-nowrap shrink-0">
          {heading}
        </span>
        <span
          className={cn(
            "text-[11px] font-medium px-1.5 py-px rounded-full whitespace-nowrap shrink-0",
            isActive
              ? "bg-[var(--em-fill)] text-[var(--em-text-secondary)]"
              : "bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]",
          )}
        >
          {isActive ? "进行中" : "已完成"}
        </span>
        {preview && (
          <span className="hidden sm:inline min-w-0 truncate text-[12px] text-muted-foreground">
            {preview}
          </span>
        )}
        <span className="ml-auto flex items-center gap-1.5 flex-shrink-0">
          {durationStr && (
            <span className="text-[11px] tabular-nums text-muted-foreground">
              {durationStr}
            </span>
          )}
          {!isActive && (
            <ChevronDown
              className={cn(
                "h-4 w-4 text-muted-foreground/60 transition-transform",
                expanded && "rotate-180",
              )}
            />
          )}
        </span>
      </button>

      <AnimatePresence initial={false}>
        {showBody && (
          <motion.div
            key="thinking-content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: [0.4, 0, 0.2, 1] }}
            className="overflow-hidden"
          >
            <div
              ref={contentRef}
              onScroll={(event) => {
                const target = event.currentTarget;
                autoFollowRef.current = isNearScrollBottom(
                  target.scrollTop,
                  target.clientHeight,
                  target.scrollHeight,
                );
              }}
              onWheelCapture={(event) => {
                // scroll 事件可能晚于下一段流式内容到达，先记录向上滚动的用户意图。
                if (event.deltaY < 0) autoFollowRef.current = false;
              }}
              onTouchStart={() => {
                autoFollowRef.current = false;
              }}
              className={cn(
                "px-3 sm:px-3.5 pb-3 text-[12px] text-[var(--em-text-secondary)] whitespace-pre-wrap leading-relaxed break-words",
                "max-h-48 overflow-y-auto overscroll-contain",
                isActive && "scrollbar-none",
              )}
              style={
                isActive
                  ? {
                      maxHeight: ACTIVE_MAX_H,
                      maskImage: FADE_MASK,
                      WebkitMaskImage: FADE_MASK,
                    }
                  : undefined
              }
            >
              {content}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
