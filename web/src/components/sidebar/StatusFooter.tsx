"use client";

import { useEffect, useState, useRef } from "react";
import { Circle, Settings, QrCode } from "lucide-react";
import dynamic from "next/dynamic";
import { useIsMobile } from "@/hooks/use-mobile";
import { useUIStore } from "@/stores/ui-store";
import { healthSkillpackCount, healthToolCount, ensureHealthHubPolling, useHealthHubStore } from "@/stores/health-hub-store";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

const MobilePairingDialog = dynamic(() => import("./MobilePairingDialog").then(m => m.MobilePairingDialog), { ssr: false });

export function StatusFooter() {
  const health = useHealthHubStore((s) => s.health);
  const connected = useHealthHubStore((s) => s.connected);
  const [reconnectFlash, setReconnectFlash] = useState(false);
  const prevConnected = useRef<boolean | null>(null);
  const isMobile = useIsMobile();
  const [openTooltipId, setOpenTooltipId] = useState<string | null>(null);
  const [pairingOpen, setPairingOpen] = useState(false);

  useEffect(() => {
    if (!isMobile || !openTooltipId) return;
    const handler = (e: PointerEvent) => {
      const target = e.target as HTMLElement;
      if (target.closest('[data-slot="tooltip-trigger"]') || target.closest('[data-slot="tooltip-content"]')) return;
      setOpenTooltipId(null);
    };
    document.addEventListener("pointerdown", handler);
    return () => document.removeEventListener("pointerdown", handler);
  }, [isMobile, openTooltipId]);
  useEffect(() => {
    ensureHealthHubPolling();
  }, []);

  useEffect(() => {
    if (prevConnected.current === false && connected === true) {
      const frame = requestAnimationFrame(() => setReconnectFlash(true));
      const timer = setTimeout(() => setReconnectFlash(false), 500);
      return () => {
        cancelAnimationFrame(frame);
        clearTimeout(timer);
      };
    }
    prevConnected.current = connected;
  }, [connected]);

  const dotColor =
    connected === null
      ? "text-muted-foreground"
      : connected
        ? "text-green-500"
        : "text-red-500";

  const dotLabel =
    connected === null
      ? "检查中…"
      : connected
        ? "已连接"
        : "未连接";

  const dotClasses = `h-2 w-2 fill-current ${dotColor}${connected === true ? " animate-pulse-green" : ""}`;

  return (
    <>
      <div
        className="em-sidebar-footer-divider h-px flex-shrink-0"
        style={{
          background:
            "linear-gradient(to right, transparent, var(--border), transparent)",
        }}
      />

      <div className="em-sidebar-footer px-3 py-3 flex items-center justify-between flex-shrink-0">
        <TooltipProvider delayDuration={300}>
          <Tooltip
            open={isMobile ? openTooltipId === "conn" : undefined}
            onOpenChange={isMobile ? () => {} : undefined}
          >
            <TooltipTrigger asChild>
              <span
                className={`flex items-center gap-1.5 text-[11px] text-muted-foreground cursor-default rounded-sm${reconnectFlash ? " animate-pulse-green" : ""}`}
                style={reconnectFlash ? { color: "var(--em-primary)" } : undefined}
                tabIndex={0}
                onClick={isMobile ? () => setOpenTooltipId((prev) => prev === "conn" ? null : "conn") : undefined}
              >
                <Circle className={dotClasses} />
                {health?.version ? (
                  <span className="truncate max-w-[56px]">v{health.version}</span>
                ) : (
                  <span>{dotLabel}</span>
                )}
                {health && (
                  <span className="text-muted-foreground/50 hidden sm:inline">
                    · {healthToolCount(health)}T · {healthSkillpackCount(health)}S
                  </span>
                )}
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">
              <span style={{ color: "var(--em-primary)" }}>{dotLabel}</span>
              {health && (
                <>
                  <br />模型: {health.model}
                  <br />工具: {healthToolCount(health)} · 技能包: {healthSkillpackCount(health)} · 会话: {health.active_sessions}
                </>
              )}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>

        <div className="flex items-center gap-1 flex-shrink-0">
          <button
            onClick={() => {
              if (window.excelManusAndroid?.scanPairing) window.excelManusAndroid.scanPairing();
              else setPairingOpen(true);
            }}
            className="h-8 w-8 inline-flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            title="扫码连接" aria-label="扫码连接"
          ><QrCode className="h-4 w-4" /></button>
          <button
            onClick={() => useUIStore.getState().openSettings("model")}
            className="h-7 w-7 inline-flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            title="设置"
            data-coach-id="coach-settings"
          >
            <Settings className="h-4 w-4" />
          </button>
        </div>
      </div>
      {pairingOpen && <MobilePairingDialog open={pairingOpen} onOpenChange={setPairingOpen} />}
    </>
  );
}
