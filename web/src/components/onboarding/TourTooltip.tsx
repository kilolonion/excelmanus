"use client";

import { useState, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowRight,
  MessageSquare,
  Settings,
  Upload,
  Cpu,
  Shield,
  FolderOpen,
  Sparkles,
  Eye,
  Table2,
  GripVertical,
  MousePointerClick,
  Server,
  ScrollText,
  Package,
  Plug,
  Brain,
  SlidersHorizontal,
  ArrowUpCircle,
  Send,
  Pause,
  BookOpen,
} from "lucide-react";
import type { TourStep } from "./tour-steps";

// ── Icon map ──

const ICON_MAP: Record<string, React.ReactNode> = {
  MessageSquare: <MessageSquare className="h-4 w-4" />,
  Settings: <Settings className="h-4 w-4" />,
  Upload: <Upload className="h-4 w-4" />,
  Cpu: <Cpu className="h-4 w-4" />,
  Shield: <Shield className="h-4 w-4" />,
  FolderOpen: <FolderOpen className="h-4 w-4" />,
  Sparkles: <Sparkles className="h-4 w-4" />,
  Eye: <Eye className="h-4 w-4" />,
  Table2: <Table2 className="h-4 w-4" />,
  GripVertical: <GripVertical className="h-4 w-4" />,
  Server: <Server className="h-4 w-4" />,
  ScrollText: <ScrollText className="h-4 w-4" />,
  Package: <Package className="h-4 w-4" />,
  Plug: <Plug className="h-4 w-4" />,
  Brain: <Brain className="h-4 w-4" />,
  SlidersHorizontal: <SlidersHorizontal className="h-4 w-4" />,
  ArrowUpCircle: <ArrowUpCircle className="h-4 w-4" />,
  Send: <Send className="h-4 w-4" />,
  Pause: <Pause className="h-4 w-4" />,
  BookOpen: <BookOpen className="h-4 w-4" />,
};

// ── Position calc ──

function calcPosition(
  targetRect: DOMRect,
  placement: TourStep["placement"],
  tooltipW: number,
  tooltipH: number,
): { top: number; left: number } {
  const gap = 14;
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  let top = 0;
  let left = 0;

  switch (placement) {
    case "bottom":
      top = targetRect.bottom + gap;
      left = targetRect.left + targetRect.width / 2 - tooltipW / 2;
      break;
    case "top":
      top = targetRect.top - tooltipH - gap;
      left = targetRect.left + targetRect.width / 2 - tooltipW / 2;
      break;
    case "right":
      top = targetRect.top + targetRect.height / 2 - tooltipH / 2;
      left = targetRect.right + gap;
      break;
    case "left":
      top = targetRect.top + targetRect.height / 2 - tooltipH / 2;
      left = targetRect.left - tooltipW - gap;
      break;
  }

  // Clamp to viewport
  left = Math.max(8, Math.min(left, vw - tooltipW - 8));
  top = Math.max(8, Math.min(top, vh - tooltipH - 8));

  // Push away if overlapping target
  const tB = top + tooltipH;
  const tR = left + tooltipW;
  const rT = targetRect.top - 8;
  const rB = targetRect.bottom + 8;
  const rL = targetRect.left - 8;
  const rR = targetRect.right + 8;

  if (tB > rT && top < rB && tR > rL && left < rR) {
    if (targetRect.right + gap + tooltipW < vw - 8) {
      left = targetRect.right + gap;
      top = Math.max(8, Math.min(targetRect.top + targetRect.height / 2 - tooltipH / 2, vh - tooltipH - 8));
    } else if (targetRect.left - gap - tooltipW > 8) {
      left = targetRect.left - tooltipW - gap;
      top = Math.max(8, Math.min(targetRect.top + targetRect.height / 2 - tooltipH / 2, vh - tooltipH - 8));
    } else if (targetRect.top > vh / 2) {
      top = Math.max(8, targetRect.top - tooltipH - gap);
    } else {
      top = Math.min(vh - tooltipH - 8, targetRect.bottom + gap);
    }
  }

  return { top, left };
}

// ── Component ──

interface TourTooltipProps {
  step: TourStep;
  stepIndex: number;
  totalSteps: number;
  phaseLabel: string;
  interactionDone: boolean;
  targetRect: DOMRect | null;
  onNext: () => void;
  onSkip: () => void;
  isMobile: boolean;
}

export function TourTooltip({
  step,
  stepIndex,
  totalSteps,
  phaseLabel,
  interactionDone,
  targetRect,
  onNext,
  onSkip,
  isMobile,
}: TourTooltipProps) {
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [tooltipH, setTooltipH] = useState(160);

  const isInteractive = !!step.interaction;
  const tooltipW = isMobile ? Math.min(260, window.innerWidth - 32) : 300;

  // Measure tooltip height
  useEffect(() => {
    if (tooltipRef.current) {
      const h = tooltipRef.current.getBoundingClientRect().height;
      if (Math.abs(h - tooltipH) > 4) setTooltipH(h);
    }
  }, [step, stepIndex, interactionDone, tooltipH]);

  const pos = targetRect
    ? calcPosition(targetRect, step.placement, tooltipW, tooltipH)
    : { top: window.innerHeight / 2 - tooltipH / 2, left: window.innerWidth / 2 - tooltipW / 2 };

  const icon = ICON_MAP[step.icon] || <Settings className="h-4 w-4" />;

  return createPortal(
    <AnimatePresence mode="wait">
      <motion.div
        ref={tooltipRef}
        key={`${phaseLabel}-${stepIndex}`}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -8 }}
        transition={{ duration: 0.25 }}
        className="fixed z-[10002] rounded-xl bg-background border border-border shadow-xl p-4"
        style={{ top: pos.top, left: pos.left, width: tooltipW, pointerEvents: "auto" }}
      >
        {/* Phase badge + progress */}
        <div className="flex items-center justify-between mb-2">
          <span
            className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full"
            style={{ backgroundColor: "var(--em-primary-alpha-10)", color: "var(--em-primary)" }}
          >
            {phaseLabel}
          </span>
          <span className="text-[10px] text-muted-foreground">
            {stepIndex + 1} / {totalSteps}
          </span>
        </div>

        {/* Title + Description */}
        <div className="flex items-start gap-3">
          <div
            className="flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center"
            style={{ backgroundColor: "var(--em-primary-alpha-10)", color: "var(--em-primary)" }}
          >
            {icon}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold mb-0.5">{step.title}</p>
            <p className="text-xs text-muted-foreground leading-relaxed">{step.description}</p>
          </div>
        </div>

        {/* Interaction hint */}
        {isInteractive && (
          <div className="mt-2.5 flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-[var(--em-primary-alpha-06)] border border-[var(--em-primary-alpha-15)]">
            {interactionDone ? (
              <span className="text-xs font-medium text-green-600 dark:text-green-400">
                ✓ 做得好！
              </span>
            ) : (
              <>
                <MousePointerClick className="h-3.5 w-3.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
                <span className="text-xs font-medium" style={{ color: "var(--em-primary)" }}>
                  {step.interaction!.hint}
                </span>
              </>
            )}
          </div>
        )}

        {/* Footer: dots + buttons */}
        <div className="flex items-center justify-between mt-3 pt-2 border-t border-border/60">
          {totalSteps > 10 ? (
            <span className="text-[11px] font-medium tabular-nums" style={{ color: "var(--em-primary)" }}>
              {stepIndex + 1} / {totalSteps}
            </span>
          ) : (
            <div className="flex gap-1">
              {Array.from({ length: totalSteps }).map((_, i) => (
                <div
                  key={i}
                  className="rounded-full transition-all"
                  style={{
                    width: i === stepIndex ? 8 : 6,
                    height: i === stepIndex ? 8 : 6,
                    backgroundColor: i < stepIndex
                      ? "var(--em-primary)"
                      : i === stepIndex
                        ? "var(--em-primary)"
                        : "var(--em-primary-alpha-15)",
                    boxShadow: i === stepIndex ? "0 0 4px var(--em-primary-alpha-25)" : undefined,
                  }}
                />
              ))}
            </div>
          )}
          <div className="flex items-center gap-2 flex-shrink-0 whitespace-nowrap">
            <button
              type="button"
              onClick={onSkip}
              className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
            >
              跳过
            </button>
            {isInteractive && !interactionDone ? (
              <button
                type="button"
                onClick={onNext}
                className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
              >
                跳过此步
              </button>
            ) : (
              <button
                type="button"
                onClick={onNext}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium text-white transition-opacity hover:opacity-90"
                style={{ backgroundColor: "var(--em-primary)" }}
              >
                {stepIndex < totalSteps - 1 ? (
                  <>下一步 <ArrowRight className="h-3 w-3" /></>
                ) : "完成"}
              </button>
            )}
          </div>
        </div>
      </motion.div>
    </AnimatePresence>,
    document.body
  );
}
