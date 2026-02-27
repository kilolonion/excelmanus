"use client";

import React, { useEffect, useRef, useState, useCallback } from "react";
import {
  Loader2,
  Check,
  Route,
  FileSearch,
  Cpu,
  Wrench,
  Grid3X3,
  Database,
  Palette,
  ShieldCheck,
} from "lucide-react";
import type { PipelineStatus } from "@/stores/chat-store";

/**
 * Ordered pipeline stages with display metadata.
 * The `key` matches the `stage` field from backend pipeline_progress events.
 */
const STAGES = [
  { key: "connecting", label: "连接中", icon: Loader2 },
  { key: "initializing", label: "初始化", icon: Loader2 },
  { key: "routing", label: "分析意图", icon: Route },
  { key: "prefetching", label: "预取上下文", icon: FileSearch },
  { key: "preparing_context", label: "准备上下文", icon: FileSearch },
  { key: "compacting", label: "压缩上下文", icon: FileSearch },
  { key: "calling_llm", label: "模型通信", icon: Cpu },
  { key: "generating_tool_call", label: "生成调用", icon: Wrench },
  { key: "executing_tools", label: "执行工具", icon: Wrench },
  // VLM 渐进式提取阶段
  { key: "vlm_extract_structure", label: "识别结构", icon: Grid3X3 },
  { key: "vlm_extract_data", label: "提取数据", icon: Database },
  { key: "vlm_extract_style", label: "提取样式", icon: Palette },
  { key: "vlm_extract_verification", label: "自校验", icon: ShieldCheck },
] as const;

type StageKey = (typeof STAGES)[number]["key"];

function stageIndex(key: string): number {
  const idx = STAGES.findIndex((s) => s.key === key);
  return idx >= 0 ? idx : -1;
}

interface PipelineStepperProps {
  status: PipelineStatus | null;
}

export const PipelineStepper = React.memo(function PipelineStepper({
  status,
}: PipelineStepperProps) {
  const [elapsed, setElapsed] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef<HTMLSpanElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);
  /* 记录上一阶段索引，用于进入动画方向 */
  const prevIdxRef = useRef(-1);
  const [animatingKey, setAnimatingKey] = useState<string | null>(null);

  // --- elapsed timer ---
  useEffect(() => {
    if (!status) {
      setElapsed(0);
      return;
    }
    setElapsed(Math.round((Date.now() - status.startedAt) / 1000));
    const timer = setInterval(() => {
      setElapsed(Math.round((Date.now() - status.startedAt) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [status]);

  // --- scroll shadow detection ---
  const updateScrollShadows = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 2);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 2);
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    updateScrollShadows();
    el.addEventListener("scroll", updateScrollShadows, { passive: true });
    const ro = new ResizeObserver(updateScrollShadows);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", updateScrollShadows);
      ro.disconnect();
    };
  }, [updateScrollShadows]);

  // --- auto-scroll to active step + trigger enter animation ---
  const currentIdx = status ? stageIndex(status.stage) : -1;

  useEffect(() => {
    if (currentIdx < 0) return;
    // 阶段变化时触发弹出动画
    if (prevIdxRef.current !== currentIdx) {
      const key = STAGES[currentIdx]?.key;
      if (key) {
        setAnimatingKey(key);
        const t = setTimeout(() => setAnimatingKey(null), 400);
        prevIdxRef.current = currentIdx;
        return () => clearTimeout(t);
      }
    }
  }, [currentIdx]);

  useEffect(() => {
    // 将当前 chip 平滑滚动到视内
    requestAnimationFrame(() => {
      activeRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
        inline: "center",
      });
      updateScrollShadows();
    });
  }, [currentIdx, updateScrollShadows]);

  if (!status) {
    return (
      <div className="flex items-center gap-2 h-7 text-sm text-muted-foreground">
        <Loader2
          className="h-3.5 w-3.5 animate-spin flex-shrink-0"
          style={{ color: "var(--em-primary)" }}
        />
        <span>正在准备...</span>
      </div>
    );
  }

  // 显示已完成 + 当前 + 下一个（最多 4 个的窗口）
  const visibleStages = STAGES.filter((_, i) => {
    if (currentIdx < 0) return false;
    return i <= currentIdx + 1 && i >= Math.max(0, currentIdx - 2);
  });

  return (
    <div className="relative group/stepper">
      {/* 左侧渐变遮罩 */}
      <div
        className="pointer-events-none absolute left-0 top-0 bottom-0 w-6 z-10 transition-opacity duration-200"
        style={{
          background:
            "linear-gradient(to right, var(--background, #fff), transparent)",
          opacity: canScrollLeft ? 1 : 0,
        }}
      />
      {/* 右侧渐变遮罩 */}
      <div
        className="pointer-events-none absolute right-0 top-0 bottom-0 w-6 z-10 transition-opacity duration-200"
        style={{
          background:
            "linear-gradient(to left, var(--background, #fff), transparent)",
          opacity: canScrollRight ? 1 : 0,
        }}
      />

      {/* 可滚动轨道 */}
      <div
        ref={scrollRef}
        className="flex items-center gap-1 py-1.5 text-xs text-muted-foreground overflow-x-auto scrollbar-none"
        style={{ scrollbarWidth: "none", msOverflowStyle: "none" }}
      >
        {visibleStages.map((stage, vi) => {
          const idx = stageIndex(stage.key);
          const isCurrent = idx === currentIdx;
          const isDone = idx < currentIdx;
          const Icon = stage.icon;
          const isAnimating = animatingKey === stage.key;

          return (
            <React.Fragment key={stage.key}>
              {/* 连接线 */}
              {vi > 0 && (
                <span className="flex items-center mx-0.5 flex-shrink-0">
                  <span
                    className="h-px w-3 transition-colors duration-300"
                    style={{
                      backgroundColor: isDone
                        ? "var(--em-primary)"
                        : "var(--border, #e5e7eb)",
                    }}
                  />
                  <span
                    className="h-0 w-0 border-t-[3px] border-t-transparent border-b-[3px] border-b-transparent border-l-[4px] transition-colors duration-300"
                    style={{
                      borderLeftColor: isDone
                        ? "var(--em-primary)"
                        : "var(--border, #e5e7eb)",
                    }}
                  />
                </span>
              )}

              {/* 阶段芯片 */}
              <span
                ref={isCurrent ? activeRef : undefined}
                className={`
                  inline-flex items-center gap-1 px-2 py-1 rounded-full whitespace-nowrap
                  transition-all duration-300 ease-out flex-shrink-0
                  ${
                    isCurrent
                      ? "bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)] font-medium shadow-[0_0_0_1px_var(--em-primary-alpha-15)]"
                      : isDone
                        ? "text-[var(--em-primary)]"
                        : "text-muted-foreground/40"
                  }
                  ${isAnimating ? "animate-chip-enter" : ""}
                `}
              >
                {/* 图标 */}
                <span
                  className={`flex-shrink-0 flex items-center justify-center rounded-full transition-all duration-300 ${
                    isCurrent
                      ? "h-4.5 w-4.5 bg-[var(--em-primary-alpha-15)]"
                      : "h-3.5 w-3.5"
                  }`}
                >
                  {isCurrent ? (
                    <Loader2
                      className="h-3 w-3 animate-spin"
                      style={{ color: "var(--em-primary)" }}
                    />
                  ) : isDone ? (
                    <Check
                      className="h-3 w-3"
                      style={{ color: "var(--em-primary)" }}
                    />
                  ) : (
                    <Icon className="h-3 w-3 opacity-40" />
                  )}
                </span>

                {/* 标签 */}
                <span className="leading-none">{stage.label}</span>

                {/* 已用时间徽章 */}
                {isCurrent && elapsed > 0 && (
                  <span
                    className="text-[10px] leading-none opacity-60 tabular-nums bg-[var(--em-primary-alpha-06)] px-1 py-0.5 rounded"
                  >
                    {elapsed}s
                  </span>
                )}
              </span>
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
});
