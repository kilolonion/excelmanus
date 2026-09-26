"use client";

import { useState, useCallback, useEffect, type ReactNode } from "react";
import { Brain, Check, Loader2 } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";
import { apiGet, apiPut } from "@/lib/api";
import { THINKING_EFFORT_LEVELS } from "@/lib/thinking";

const EFFORT_LABEL_MAP: Record<string, string> = Object.fromEntries(
  THINKING_EFFORT_LEVELS.map(({ key, label }) => [key, label])
);

interface ThinkingControls {
  model_allowed_efforts: string[];
  effective_effort: string;
  control_kind: string;
  levels_source?: string | null;
}

export function ThinkingLevelSelector() {
  const thinkingEffort = useUIStore((s) => s.thinkingEffort);
  const thinkingEffortOptions = useUIStore((s) => s.thinkingEffortOptions);
  const setThinkingEffort = useUIStore((s) => s.setThinkingEffort);
  const [saving, setSaving] = useState(false);
  const sessionId = useSessionStore((s) => s.activeSessionId);
  const model = useUIStore((s) => s.currentModel);
  const version = useUIStore((s) => s.modelProfileVersion);
  const [controls, setControls] = useState<ThinkingControls | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let current = true;
    setControls(null);
    apiGet<ThinkingControls>(sessionId ? `/thinking?session_id=${encodeURIComponent(sessionId)}` : "/thinking")
      .then((data) => { if (current) setControls(data); })
      .catch(() => { if (current) setError("无法读取当前模型的思考控制能力"); });
    return () => { current = false; };
  }, [model, version, thinkingEffort, sessionId]);

  const handleSelect = useCallback(
    async (effort: string) => {
      if (effort === thinkingEffort && controls?.effective_effort === effort) return;
      setError("");
      setSaving(true);
      try {
        await apiPut("/thinking", { effort, budget: 0, ...(sessionId ? {session_id: sessionId} : {}) });
        setThinkingEffort(effort);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "思考设置未生效");
      } finally {
        setSaving(false);
      }
    },
    [thinkingEffort, setThinkingEffort, sessionId, controls?.effective_effort]
  );

  const selectedEffort = controls?.effective_effort ?? thinkingEffort;
  const currentLabel = controls?.control_kind === "toggle" ? (selectedEffort === "none" ? "关闭" : "开启")
    : controls?.control_kind === "none" ? "无推理控制" : controls?.control_kind === "unknown" ? "自动"
    : EFFORT_LABEL_MAP[selectedEffort] ?? "自动";
  const visibleLevels = THINKING_EFFORT_LEVELS.filter(({ key }) =>
    thinkingEffortOptions.includes(key) && (controls?.model_allowed_efforts ?? []).includes(key)
  );
  const hasSelectableLevels = visibleLevels.length > 0;

  // A model whose reasoning controls have not been verified has no safe
  // client-side options to show. Keep the status indicator useful without
  // mounting an empty Radix popover that renders as a blank capsule.
  const triggerButton = (
    <button
      type="button"
      disabled={saving || !hasSelectableLevels}
      className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium transition-colors text-muted-foreground hover:text-foreground hover:bg-accent/40 outline-none disabled:cursor-default disabled:hover:bg-transparent"
    >
      {saving ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : (
        <Brain className="h-3 w-3" />
      )}
      <span className="hidden sm:inline">{currentLabel}</span>
    </button>
  );

  const triggerWithTooltip = (trigger: ReactNode) => (
    <TooltipProvider delayDuration={400}>
      <Tooltip>
        <TooltipTrigger asChild>{trigger}</TooltipTrigger>
        <TooltipContent side="top" className="text-xs">
          {error || `思考控制: ${currentLabel}`}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );

  if (!hasSelectableLevels) {
    return triggerWithTooltip(triggerButton);
  }

  return (
    <DropdownMenu>
      {triggerWithTooltip(
        <DropdownMenuTrigger asChild>
          {triggerButton}
        </DropdownMenuTrigger>
      )}

      <DropdownMenuContent align="end" sideOffset={6} className="min-w-[140px]">
        {visibleLevels.map(({ key, label, desc }) => (
          <DropdownMenuItem
            key={key}
            onClick={() => handleSelect(key)}
            className="flex items-center justify-between gap-3 text-xs"
          >
            <div className="flex flex-col">
              <span className="font-medium">{controls?.control_kind === "toggle" ? (key === "none" ? "关闭" : "开启") : label}</span>
              <span className="text-[10px] text-muted-foreground">{controls?.control_kind === "toggle" ? "此模型仅提供思考开关" : desc}</span>
            </div>
            {selectedEffort === key && (
              <Check className="h-3.5 w-3.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
            )}
          </DropdownMenuItem>
        ))}
        {controls?.levels_source === "user_declared" && (
          <div className="border-t border-border/60 px-2 py-1.5 text-[10px] text-muted-foreground">
            等级来自你的全局思考配置，接口参数按所选思考模式换算
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
