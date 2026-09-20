"use client";

import { useCallback, useState } from "react";
import { Check, Loader2, Shield } from "lucide-react";
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
import { useSessionStore } from "@/stores/session-store";
import { toggleFullAccess } from "@/lib/api";

const POLICIES = [
  { key: "ask", label: "询问", desc: "写入前确认" },
  { key: "skip", label: "跳过", desc: "自动批准所有命令（含联网）" },
] as const;

export function ModeBadges() {
  const fullAccess = useUIStore((s) => s.fullAccessEnabled);
  const setFullAccessEnabled = useUIStore((s) => s.setFullAccessEnabled);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const [toggling, setToggling] = useState(false);
  const active = fullAccess ? "skip" : "ask";
  const currentLabel = fullAccess ? "跳过" : "询问";

  const handleSelect = useCallback(
    async (key: "ask" | "skip") => {
      const wantSkip = key === "skip";
      if (wantSkip === fullAccess || !activeSessionId || toggling) return;
      setToggling(true);
      setFullAccessEnabled(wantSkip);
      try {
        await toggleFullAccess(activeSessionId, wantSkip);
      } catch {
        setFullAccessEnabled(!wantSkip);
      } finally {
        setToggling(false);
      }
    },
    [activeSessionId, fullAccess, toggling, setFullAccessEnabled],
  );

  return (
    <DropdownMenu>
      <TooltipProvider delayDuration={400}>
        <Tooltip>
          <TooltipTrigger asChild>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                disabled={!activeSessionId || toggling}
                data-coach-id="coach-mode-badges"
                className={`inline-flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium transition-colors outline-none disabled:cursor-default disabled:opacity-50 ${
                  fullAccess
                    ? "hover:bg-accent/40"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent/40"
                }`}
                style={fullAccess ? { color: "var(--em-gold)" } : undefined}
              >
                {toggling ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Shield className="h-3 w-3" />
                )}
                <span className="hidden sm:inline">{currentLabel}</span>
              </button>
            </DropdownMenuTrigger>
          </TooltipTrigger>
          <TooltipContent side="top" className="text-xs">
            审批策略: {currentLabel}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>

      <DropdownMenuContent align="end" sideOffset={6} className="min-w-[160px]">
        {POLICIES.map(({ key, label, desc }) => (
          <DropdownMenuItem
            key={key}
            onClick={() => void handleSelect(key)}
            className="flex items-center justify-between gap-3 text-xs"
          >
            <div className="flex flex-col">
              <span className="font-medium">{label}</span>
              <span className="text-[10px] text-muted-foreground">{desc}</span>
            </div>
            {active === key && (
              <Check className="h-3.5 w-3.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
            )}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
