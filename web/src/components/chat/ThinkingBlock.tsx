"use client";

import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

interface ThinkingBlockProps {
  content: string;
  duration?: number;
  startedAt?: number;
  isActive?: boolean;
}

const FADE_MASK =
  "linear-gradient(to bottom, transparent 0%, black 14%, black 86%, transparent 100%)";
const PREVIEW_MAX_H = "6.5rem"; // ~4.2 lines at text-xs leading-relaxed

export function ThinkingBlock({
  content,
  duration,
  startedAt,
  isActive = false,
}: ThinkingBlockProps) {
  const [expanded, setExpanded] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isActive) return;
    const origin = startedAt ?? Date.now();
    const tick = () => setElapsed(Math.round((Date.now() - origin) / 1000));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [isActive, startedAt]);

  useEffect(() => {
    if (isActive && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [content, isActive]);

  const seconds = isActive
    ? elapsed
    : duration != null
      ? Math.round(duration)
      : 0;

  const fmtDuration = (s: number): string => {
    if (s <= 0) return "";
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const r = s % 60;
    return r > 0 ? `${m}m ${r}s` : `${m}m`;
  };

  const durationStr = fmtDuration(seconds);
  const showContent = isActive || expanded;

  return (
    <div className="my-2">
      <button
        type="button"
        onClick={() => !isActive && setExpanded((v) => !v)}
        className={cn(
          "flex items-center gap-1.5 text-sm transition-colors select-none max-w-full",
          isActive
            ? "text-muted-foreground cursor-default"
            : "text-muted-foreground hover:text-foreground cursor-pointer",
        )}
      >
        <span className="truncate">
          {isActive ? "思考中" : "思考完成"}
          {durationStr && (
            <span className="ml-1 font-normal opacity-60">
              {durationStr}
            </span>
          )}
          {!isActive && !expanded && content && (
            <span className="ml-1.5 font-normal opacity-40 text-xs">
              {content.replace(/\n/g, " ").slice(0, 60)}{content.length > 60 ? "…" : ""}
            </span>
          )}
        </span>

        {isActive ? (
          <span className="inline-flex items-center gap-px ml-0.5 opacity-50">
            <span className="w-[3px] h-[3px] rounded-full bg-current animate-bounce [animation-delay:0ms]" />
            <span className="w-[3px] h-[3px] rounded-full bg-current animate-bounce [animation-delay:150ms]" />
            <span className="w-[3px] h-[3px] rounded-full bg-current animate-bounce [animation-delay:300ms]" />
          </span>
        ) : (
          <ChevronDown
            className={cn(
              "h-3.5 w-3.5 flex-shrink-0 transition-transform duration-200",
              !expanded && "-rotate-90",
            )}
          />
        )}
      </button>

      <AnimatePresence initial={false}>
        {showContent && content && (
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
              className={cn(
                "mt-1.5 text-xs text-muted-foreground whitespace-pre-wrap leading-relaxed",
                "rounded-lg bg-muted/20 dark:bg-muted/10 px-3 py-2",
                "scrollbar-none",
                isActive && "overflow-y-auto",
              )}
              style={
                isActive
                  ? {
                      maxHeight: PREVIEW_MAX_H,
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
