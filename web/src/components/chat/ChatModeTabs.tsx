"use client";

import { motion } from "framer-motion";
import { Pencil, Search as SearchIcon, ClipboardList as ClipboardListIcon } from "lucide-react";
import { useUIStore } from "@/stores/ui-store";

const CHAT_MODE_COLORS: Record<string, { text: string; bg: string }> = {
  write: { text: "var(--em-primary)", bg: "var(--em-primary-alpha-10)" },
  read:  { text: "var(--em-cyan)",    bg: "color-mix(in srgb, var(--em-cyan) 10%, transparent)" },
  plan:  { text: "var(--em-gold)",    bg: "color-mix(in srgb, var(--em-gold) 12%, transparent)" },
};

const CHAT_MODES = [
  { key: "write" as const, label: "写入", icon: Pencil },
  { key: "read" as const, label: "读取", icon: SearchIcon },
  { key: "plan" as const, label: "计划", icon: ClipboardListIcon },
];

export function ChatModeTabs() {
  const chatMode = useUIStore((s) => s.chatMode);
  const setChatMode = useUIStore((s) => s.setChatMode);
  return (
    <div className="flex items-center gap-0.5 px-3 pt-1.5 pb-0">
      {CHAT_MODES.map(({ key, label, icon: Icon }) => (
        <button
          key={key}
          onClick={() => setChatMode(key)}
          className={`relative inline-flex items-center gap-1 px-2.5 sm:py-1 py-0.5 rounded-lg text-xs font-medium transition-colors ${
            chatMode === key
              ? ""
              : "text-muted-foreground hover:text-foreground hover:bg-accent/40"
          }`}
          style={
            chatMode === key
              ? { color: CHAT_MODE_COLORS[key].text }
              : undefined
          }
        >
          {chatMode === key && (
            <motion.div
              layoutId="chat-mode-indicator"
              className="absolute inset-0 rounded-lg"
              style={{ backgroundColor: CHAT_MODE_COLORS[key].bg }}
              transition={{ type: "spring", stiffness: 400, damping: 30 }}
            />
          )}
          <Icon className="sm:h-3 sm:w-3 h-2.5 w-2.5 relative z-10" />
          <span className="relative z-10">{label}</span>
        </button>
      ))}
    </div>
  );
}
