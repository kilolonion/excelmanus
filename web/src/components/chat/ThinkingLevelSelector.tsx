"use client";

import { useState, useCallback } from "react";
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
import { useUIStore } from "@/stores/ui-store";
import { apiPut } from "@/lib/api";

const EFFORT_LEVELS = [
  { key: "none", label: "关闭", desc: "不使用推理" },
  { key: "minimal", label: "极简", desc: "最少推理" },
  { key: "low", label: "低", desc: "轻度推理" },
  { key: "medium", label: "中", desc: "平衡模式" },
  { key: "high", label: "高", desc: "深度推理" },
  { key: "xhigh", label: "极高", desc: "最深推理" },
] as const;

const EFFORT_LABEL_MAP: Record<string, string> = Object.fromEntries(
  EFFORT_LEVELS.map(({ key, label }) => [key, label])
);

export function ThinkingLevelSelector() {
  const thinkingEffort = useUIStore((s) => s.thinkingEffort);
  const setThinkingEffort = useUIStore((s) => s.setThinkingEffort);
  const [saving, setSaving] = useState(false);

  const handleSelect = useCallback(
    async (effort: string) => {
      if (effort === thinkingEffort) return;
      setThinkingEffort(effort);
      setSaving(true);
      try {
        await apiPut("/thinking", { effort, budget: 0 });
      } catch {
        // 静默失败，本地已乐观更新
      } finally {
        setSaving(false);
      }
    },
    [thinkingEffort, setThinkingEffort]
  );

  const currentLabel = EFFORT_LABEL_MAP[thinkingEffort] ?? "中";

  return (
    <DropdownMenu>
      <TooltipProvider delayDuration={400}>
        <Tooltip>
          <TooltipTrigger asChild>
            <DropdownMenuTrigger asChild>
              <button
                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium transition-colors text-muted-foreground hover:text-foreground hover:bg-accent/40 outline-none"
              >
                {saving ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Brain className="h-3 w-3" />
                )}
                <span className="hidden sm:inline">{currentLabel}</span>
              </button>
            </DropdownMenuTrigger>
          </TooltipTrigger>
          <TooltipContent side="top" className="text-xs">
            思考深度: {currentLabel}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>

      <DropdownMenuContent align="end" sideOffset={6} className="min-w-[140px]">
        {EFFORT_LEVELS.map(({ key, label, desc }) => (
          <DropdownMenuItem
            key={key}
            onClick={() => handleSelect(key)}
            className="flex items-center justify-between gap-3 text-xs"
          >
            <div className="flex flex-col">
              <span className="font-medium">{label}</span>
              <span className="text-[10px] text-muted-foreground">{desc}</span>
            </div>
            {thinkingEffort === key && (
              <Check className="h-3.5 w-3.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
            )}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
