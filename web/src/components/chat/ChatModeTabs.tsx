"use client";

import { motion } from "framer-motion";
import { Eye, Pencil, ClipboardList as ClipboardListIcon } from "lucide-react";
import { useCallback } from "react";
import { useUIStore } from "@/stores/ui-store";

const PRESET_COLORS: Record<string, { text: string; bg: string }> = {
  observe: { text: "var(--em-primary)", bg: "var(--em-primary-alpha-08)" },
  edit: { text: "var(--em-primary)", bg: "var(--em-primary-alpha-10)" },
  plan: { text: "var(--em-primary)", bg: "var(--em-primary-alpha-08)" },
};

type PresetKey = "observe" | "edit" | "plan";

const PRESETS: { key: PresetKey; label: string; icon: typeof Pencil }[] = [
  { key: "edit", label: "编辑", icon: Pencil },
  { key: "observe", label: "观察", icon: Eye },
  { key: "plan", label: "计划", icon: ClipboardListIcon },
];

function currentPreset(chatMode: string): PresetKey {
  if (chatMode === "plan") return "plan";
  if (chatMode === "read") return "observe";
  return "edit";
}

export function ChatModeTabs() {
  const chatMode = useUIStore((s) => s.chatMode);
  const setChatMode = useUIStore((s) => s.setChatMode);
  const active = currentPreset(chatMode);

  const applyPreset = useCallback(
    (key: PresetKey) => {
      if (key === "plan") setChatMode("plan");
      else if (key === "observe") setChatMode("read");
      else setChatMode("write");
    },
    [setChatMode],
  );

  return (
    <div className="flex items-center gap-0.5 px-3 pt-1 pb-0" data-coach-id="coach-mode-presets">
      {PRESETS.map(({ key, label, icon: Icon }) => (
        <button
          key={key}
          type="button"
          onClick={() => applyPreset(key)}
          className={`relative inline-flex items-center gap-1 px-2.5 h-7 rounded-lg text-xs font-medium transition-colors ${
            active === key
              ? ""
              : "text-muted-foreground hover:text-foreground hover:bg-accent/40"
          }`}
          style={
            active === key
              ? { color: PRESET_COLORS[key].text }
              : undefined
          }
        >
          {active === key && (
            <motion.div
              layoutId="chat-mode-indicator"
              className="absolute inset-0 rounded-lg"
              style={{ backgroundColor: PRESET_COLORS[key].bg }}
              transition={{ type: "spring", stiffness: 400, damping: 30 }}
            />
          )}
          <Icon className="h-3 w-3 relative z-10" />
          <span className="relative z-10 hidden sm:inline">{label}</span>
        </button>
      ))}
    </div>
  );
}
