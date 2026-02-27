"use client";

import { useEffect, useState, useCallback } from "react";
import { Upload } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { ApplyPanel } from "./ApplyPanel";

export function BackupApplyBadge() {
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const pendingBackups = useExcelStore((s) => s.pendingBackups);
  const backupEnabled = useExcelStore((s) => s.backupEnabled);
  const fetchBackups = useExcelStore((s) => s.fetchBackups);
  const [panelOpen, setPanelOpen] = useState(false);

  const poll = useCallback(() => {
    if (activeSessionId) {
      fetchBackups(activeSessionId);
    }
  }, [activeSessionId, fetchBackups]);

  useEffect(() => {
    poll();
    const id = setInterval(poll, 10000);
    return () => clearInterval(id);
  }, [poll]);

  if (!backupEnabled || pendingBackups.length === 0) return null;

  return (
    <>
      <TooltipProvider delayDuration={300}>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={() => setPanelOpen(true)}
              className="relative flex items-center gap-1 sm:gap-1.5 mr-1 sm:mr-3 px-1.5 sm:px-2 py-1 rounded-md text-xs font-medium transition-colors hover:bg-muted/50"
              style={{ color: "var(--em-primary)" }}
            >
              <Upload className="h-3.5 w-3.5" />
              <Badge
                variant="secondary"
                className="h-4 min-w-[18px] px-1 text-[10px] font-semibold text-white"
                style={{ backgroundColor: "var(--em-primary)" }}
              >
                {pendingBackups.length}
              </Badge>
              <span className="hidden sm:inline">待应用</span>
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="text-xs">
            {pendingBackups.length} 个文件待应用到原文件 — 点击管理
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <ApplyPanel open={panelOpen} onOpenChange={setPanelOpen} />
    </>
  );
}
