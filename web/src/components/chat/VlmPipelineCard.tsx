"use client";

import React, { useState, useCallback, useEffect, useRef } from "react";
import {
  Grid3X3,
  Database,
  Palette,
  ShieldCheck,
  Check,
  Loader2,
  Circle,
  ChevronDown,
  ChevronRight,
  Maximize2,
  Image as ImageIcon,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { useChatStore, type VlmPhaseEntry } from "@/stores/chat-store";
import { MiniSpecTable } from "./MiniSpecTable";
import { buildApiUrl } from "@/lib/api";

/**
 * VLM pipeline stage metadata for display.
 */
const STAGE_META: Record<string, { label: string; icon: React.ElementType }> = {
  vlm_extract_structure: { label: "结构识别", icon: Grid3X3 },
  vlm_extract_data: { label: "数据填充", icon: Database },
  vlm_extract_style: { label: "样式提取", icon: Palette },
  vlm_extract_verification: { label: "自校验", icon: ShieldCheck },
};

const ALL_STAGES = [
  "vlm_extract_structure",
  "vlm_extract_data",
  "vlm_extract_style",
  "vlm_extract_verification",
];

interface VlmPipelineCardProps {
  imagePath?: string;
}

export const VlmPipelineCard = React.memo(function VlmPipelineCard({
  imagePath,
}: VlmPipelineCardProps) {
  const vlmPhases = useChatStore((s) => s.vlmPhases);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const [imageExpanded, setImageExpanded] = useState(false);

  if (vlmPhases.length === 0) return null;

  const totalPhases = vlmPhases[0]?.totalPhases ?? 4;
  const completedStages = new Set(vlmPhases.map((p) => p.stage));
  const currentPipelineStatus = useChatStore((s) => s.pipelineStatus);
  const activeStage = isStreaming ? currentPipelineStatus?.stage : undefined;

  return (
    <div className="my-3 rounded-xl border border-border/60 bg-card overflow-hidden">
      {/* Image preview header */}
      {imagePath && (
        <div className="border-b border-border/40 bg-muted/20">
          <button
            type="button"
            onClick={() => setImageExpanded((v) => !v)}
            className="flex items-center gap-2 px-3 py-2 w-full text-left text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <ImageIcon className="h-3.5 w-3.5 flex-shrink-0" />
            <span className="truncate flex-1">{imagePath.split("/").pop()}</span>
            {imageExpanded ? (
              <ChevronDown className="h-3 w-3 flex-shrink-0" />
            ) : (
              <Maximize2 className="h-3 w-3 flex-shrink-0" />
            )}
          </button>
          <AnimatePresence>
            {imageExpanded && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="overflow-hidden"
              >
                <div className="px-3 pb-3">
                  <img
                    src={buildApiUrl(`/files/image?path=${encodeURIComponent(imagePath)}`)}
                    alt="源图片"
                    className="max-h-48 rounded-lg object-contain w-full bg-white dark:bg-zinc-800"
                    loading="lazy"
                  />
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      {/* Timeline */}
      <div className="px-3 py-2.5">
        {ALL_STAGES.slice(0, totalPhases).map((stageKey, idx) => {
          const phase = vlmPhases.find((p) => p.stage === stageKey);
          const isDone = completedStages.has(stageKey);
          const isActive = activeStage === stageKey;
          const isPending = !isDone && !isActive;
          const meta = STAGE_META[stageKey] || { label: stageKey, icon: Circle };

          return (
            <VlmTimelineNode
              key={stageKey}
              stageKey={stageKey}
              label={meta.label}
              Icon={meta.icon}
              phase={phase}
              isDone={isDone}
              isActive={isActive}
              isPending={isPending}
              isLast={idx === totalPhases - 1}
            />
          );
        })}
      </div>

      {/* Uncertainty summary */}
      <UncertaintySummary phases={vlmPhases} />
    </div>
  );
});

function VlmTimelineNode({
  stageKey,
  label,
  Icon,
  phase,
  isDone,
  isActive,
  isPending,
  isLast,
}: {
  stageKey: string;
  label: string;
  Icon: React.ElementType;
  phase: VlmPhaseEntry | undefined;
  isDone: boolean;
  isActive: boolean;
  isPending: boolean;
  isLast: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [specData, setSpecData] = useState<Record<string, unknown> | null>(null);
  const [loadingSpec, setLoadingSpec] = useState(false);

  // 当前阶段的已用时间
  const [elapsed, setElapsed] = useState(0);
  const startRef = useRef(Date.now());
  useEffect(() => {
    if (!isActive) {
      setElapsed(0);
      return;
    }
    startRef.current = Date.now();
    const timer = setInterval(() => {
      setElapsed(Math.round((Date.now() - startRef.current) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [isActive]);

  const handleExpand = useCallback(async () => {
    if (!isDone || !phase?.specPath) return;
    const next = !expanded;
    setExpanded(next);
    if (next && !specData) {
      setLoadingSpec(true);
      try {
        const res = await fetch(
          buildApiUrl(`/files/spec?path=${encodeURIComponent(phase.specPath)}`)
        );
        if (res.ok) {
          const json = await res.json();
          setSpecData(json);
        }
      } catch {
        // 加载 spec 失败
      } finally {
        setLoadingSpec(false);
      }
    }
  }, [expanded, isDone, phase?.specPath, specData]);

  const diffSummary = phase?.diff?.summary;

  return (
    <div className="flex gap-2.5">
      {/* Timeline rail */}
      <div className="flex flex-col items-center flex-shrink-0 w-5">
        {/* Node dot */}
        <div
          className={`h-5 w-5 rounded-full flex items-center justify-center flex-shrink-0 transition-colors duration-300 ${
            isDone
              ? "bg-[var(--em-primary-alpha-15)]"
              : isActive
                ? "bg-[var(--em-cyan)]/15"
                : "bg-muted/40"
          }`}
        >
          {isDone ? (
            <Check className="h-3 w-3" style={{ color: "var(--em-primary)" }} />
          ) : isActive ? (
            <Loader2 className="h-3 w-3 animate-spin" style={{ color: "var(--em-cyan)" }} />
          ) : (
            <Circle className="h-2.5 w-2.5 text-muted-foreground/40" />
          )}
        </div>
        {/* Connector line */}
        {!isLast && (
          <div
            className={`w-px flex-1 min-h-[16px] transition-colors duration-300 ${
              isDone ? "bg-[var(--em-primary-alpha-20)]" : "bg-border/40"
            }`}
          />
        )}
      </div>

      {/* Content */}
      <div className={`flex-1 min-w-0 pb-3 ${isLast ? "pb-0" : ""}`}>
        <div className="flex items-center gap-1.5">
          <Icon
            className={`h-3.5 w-3.5 flex-shrink-0 ${
              isDone
                ? "text-[var(--em-primary)]"
                : isActive
                  ? "text-[var(--em-cyan)]"
                  : "text-muted-foreground/40"
            }`}
          />
          <span
            className={`text-xs font-medium ${
              isPending ? "text-muted-foreground/50" : ""
            }`}
          >
            {label}
          </span>

          {isDone && phase && (
            <span className="text-[10px] text-muted-foreground ml-auto">
              ✓
            </span>
          )}
          {isActive && elapsed > 0 && (
            <span className="text-[10px] text-muted-foreground/60 tabular-nums ml-auto">
              {elapsed}s
            </span>
          )}
        </div>

        {/* Diff summary */}
        {isDone && diffSummary && (
          <motion.p
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            className="text-[10px] text-muted-foreground mt-0.5 leading-relaxed"
          >
            {diffSummary}
          </motion.p>
        )}

        {/* Expand button for table preview */}
        {isDone && phase?.specPath && (
          <button
            type="button"
            onClick={handleExpand}
            className="flex items-center gap-1 mt-1 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
          >
            {expanded ? (
              <ChevronDown className="h-2.5 w-2.5" />
            ) : (
              <ChevronRight className="h-2.5 w-2.5" />
            )}
            <span>{expanded ? "收起表格" : "查看表格"}</span>
          </button>
        )}

        {/* Mini spec table preview */}
        <AnimatePresence>
          {expanded && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden"
            >
              {loadingSpec ? (
                <div className="flex items-center gap-1.5 py-2 text-[10px] text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  加载中...
                </div>
              ) : specData ? (
                <div className="mt-1.5">
                  <MiniSpecTable
                    spec={specData}
                    diffHighlights={phase?.diff}
                    stageKey={stageKey}
                  />
                </div>
              ) : (
                <p className="text-[10px] text-muted-foreground py-2">
                  无法加载表格数据
                </p>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

function UncertaintySummary({ phases }: { phases: VlmPhaseEntry[] }) {
  const totalUncertainties = phases.reduce((acc, p) => {
    const changes = p.diff?.changes ?? [];
    // 若有 diff 摘要文本则从中计数
    return acc;
  }, 0);

  // 无有效数据时不显示
  if (phases.length === 0) return null;

  // 从最后一阶段的 diff 摘要中提取不确定数量
  const lastPhase = phases[phases.length - 1];
  const match = lastPhase?.diff?.summary?.match(/(\d+)\s*个不确定/);
  const count = match ? parseInt(match[1], 10) : 0;

  if (count === 0) return null;

  return (
    <div className="border-t border-border/40 px-3 py-2 text-[10px] text-amber-600 dark:text-amber-400 flex items-center gap-1.5">
      <ShieldCheck className="h-3 w-3 flex-shrink-0" />
      <span>{count} 处不确定项</span>
    </div>
  );
}
