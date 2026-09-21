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
import { toggleAutoApprove, toggleFullAccess } from "@/lib/api";

const POLICIES = [
  { key: "ask", label: "询问", desc: "写入前确认" },
  { key: "auto", label: "自动审批", desc: "自动批准，但禁止网络和越界文件" },
  { key: "full", label: "完全访问", desc: "允许网络、子进程和工作区外文件" },
] as const;

export function ModeBadges() {
  const fullAccess = useUIStore((s) => s.fullAccessEnabled);
  const autoApprove = useUIStore((s) => s.autoApproveEnabled);
  const setFullAccessEnabled = useUIStore((s) => s.setFullAccessEnabled);
  const setAutoApproveEnabled = useUIStore((s) => s.setAutoApproveEnabled);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const [toggling, setToggling] = useState(false);
  const active = fullAccess ? "full" : autoApprove ? "auto" : "ask";
  const currentLabel = fullAccess ? "完全访问" : autoApprove ? "自动审批" : "询问";

  const handleSelect = useCallback(
    async (key: "ask" | "auto" | "full") => {
      if (!activeSessionId || toggling || key === active) return;
      setToggling(true);
      const nextFull = key === "full";
      const nextAuto = key === "auto";
      setFullAccessEnabled(nextFull);
      setAutoApproveEnabled(nextAuto);
      try {
        if (nextFull) {
          await toggleFullAccess(activeSessionId, true);
        } else if (nextAuto) {
          await toggleAutoApprove(activeSessionId, true);
        } else {
          // 关闭当前档位；顺序保证互斥状态在后端也被清理。
          if (fullAccess) await toggleFullAccess(activeSessionId, false);
          if (autoApprove) await toggleAutoApprove(activeSessionId, false);
        }
      } catch {
        setFullAccessEnabled(fullAccess);
        setAutoApproveEnabled(autoApprove);
      } finally {
        setToggling(false);
      }
    },
    [activeSessionId, active, autoApprove, fullAccess, toggling, setAutoApproveEnabled, setFullAccessEnabled],
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
                  fullAccess || autoApprove
                    ? "hover:bg-accent/40"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent/40"
                }`}
                style={fullAccess ? { color: "var(--em-gold)" } : autoApprove ? { color: "var(--em-primary)" } : undefined}
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
            权限模式: {currentLabel}
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
