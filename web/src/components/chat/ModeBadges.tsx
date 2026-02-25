"use client";

import { useUIStore } from "@/stores/ui-store";
import { Shield, ClipboardList } from "lucide-react";

export function ModeBadges() {
  const fullAccess = useUIStore((s) => s.fullAccessEnabled);
  const planMode = useUIStore((s) => s.planModeEnabled);

  if (!fullAccess && !planMode) return null;

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
      {planMode && (
        <span
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium"
          style={{
            backgroundColor: "color-mix(in srgb, var(--em-cyan) 15%, transparent)",
            color: "var(--em-cyan)",
          }}
        >
          <ClipboardList className="h-3 w-3" />
          PLAN
        </span>
      )}
    </div>
  );
}
