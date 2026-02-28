"use client";

import { useCallback, useState } from "react";
import { useUIStore } from "@/stores/ui-store";
import { useSessionStore } from "@/stores/session-store";
import { toggleFullAccess } from "@/lib/api";
import { Shield, Search, ClipboardList } from "lucide-react";

const MODE_BADGES: Record<string, { icon: typeof Shield; label: string; color: string }> = {
  read: { icon: Search, label: "READ", color: "var(--em-cyan)" },
  plan: { icon: ClipboardList, label: "PLAN", color: "var(--em-gold)" },
};

export function ModeBadges() {
  const fullAccess = useUIStore((s) => s.fullAccessEnabled);
  const setFullAccessEnabled = useUIStore((s) => s.setFullAccessEnabled);
  const chatMode = useUIStore((s) => s.chatMode);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const modeBadge = MODE_BADGES[chatMode];
  const [toggling, setToggling] = useState(false);

  const handleToggleFullAccess = useCallback(async () => {
    if (!activeSessionId || toggling) return;
    const newValue = !fullAccess;
    setToggling(true);
    // 乐观更新
    setFullAccessEnabled(newValue);
    try {
      await toggleFullAccess(activeSessionId, newValue);
    } catch {
      // 回滚
      setFullAccessEnabled(!newValue);
    } finally {
      setToggling(false);
    }
  }, [activeSessionId, fullAccess, toggling, setFullAccessEnabled]);

  return (
    <div className="flex items-center gap-1.5 ml-2">
      <button
        type="button"
        onClick={handleToggleFullAccess}
        disabled={!activeSessionId || toggling}
        title={fullAccess ? "点击关闭 Full Access" : "点击开启 Full Access"}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium transition-all duration-200 cursor-pointer disabled:cursor-default"
        style={{
          backgroundColor: fullAccess
            ? "color-mix(in srgb, var(--em-gold) 15%, transparent)"
            : "color-mix(in srgb, var(--foreground) 6%, transparent)",
          color: fullAccess ? "var(--em-gold)" : "var(--muted-foreground)",
          opacity: toggling ? 0.5 : 1,
        }}
      >
        <Shield className="h-3 w-3" />
        FULL
      </button>
      {modeBadge && (
        <span
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium"
          style={{
            backgroundColor: `color-mix(in srgb, ${modeBadge.color} 15%, transparent)`,
            color: modeBadge.color,
          }}
        >
          <modeBadge.icon className="h-3 w-3" />
          {modeBadge.label}
        </span>
      )}
    </div>
  );
}
