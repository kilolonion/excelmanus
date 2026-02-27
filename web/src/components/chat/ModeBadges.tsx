"use client";

import { useUIStore } from "@/stores/ui-store";
import { Shield, Search, ClipboardList } from "lucide-react";

const MODE_BADGES: Record<string, { icon: typeof Shield; label: string; color: string }> = {
  read: { icon: Search, label: "READ", color: "var(--em-cyan)" },
  plan: { icon: ClipboardList, label: "PLAN", color: "var(--em-gold)" },
};

export function ModeBadges() {
  const fullAccess = useUIStore((s) => s.fullAccessEnabled);
  const chatMode = useUIStore((s) => s.chatMode);
  const modeBadge = MODE_BADGES[chatMode];

  if (!fullAccess && !modeBadge) return null;

  return (
    <div className="flex items-center gap-1.5 ml-2">
      {fullAccess && (
        <span
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium"
          style={{
            backgroundColor: "color-mix(in srgb, var(--em-gold) 15%, transparent)",
            color: "var(--em-gold)",
          }}
        >
          <Shield className="h-3 w-3" />
          FULL
        </span>
      )}
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
