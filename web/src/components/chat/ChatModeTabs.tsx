"use client";

import { motion } from "framer-motion";
import { Eye, Pencil, Zap, ClipboardList as ClipboardListIcon, Code2 } from "lucide-react";
import { useCallback } from "react";
import { useUIStore } from "@/stores/ui-store";
import { useSessionStore } from "@/stores/session-store";
import { toggleFullAccess } from "@/lib/api";

const PRESET_COLORS: Record<string, { text: string; bg: string }> = {
  observe: { text: "var(--em-primary)", bg: "var(--em-primary-alpha-08)" },
  edit: { text: "var(--em-primary)", bg: "var(--em-primary-alpha-10)" },
  auto: { text: "var(--em-gold)", bg: "color-mix(in srgb, var(--em-gold) 15%, transparent)" },
  plan: { text: "var(--em-primary)", bg: "var(--em-primary-alpha-08)" },
};

type PresetKey = "observe" | "edit" | "auto" | "plan";

const PRESETS: { key: PresetKey; label: string; icon: typeof Pencil }[] = [
  { key: "observe", label: "观察", icon: Eye },
  { key: "edit", label: "编辑", icon: Pencil },
  { key: "auto", label: "自动编辑", icon: Zap },
  { key: "plan", label: "计划", icon: ClipboardListIcon },
];

function currentPreset(chatMode: string, fullAccess: boolean): PresetKey {
  if (chatMode === "plan") return "plan";
  if (chatMode === "read") return "observe";
  if (fullAccess) return "auto";
  return "edit";
}

export function ChatModeTabs() {
  const chatMode = useUIStore((s) => s.chatMode);
  const setChatMode = useUIStore((s) => s.setChatMode);
  const presentAs = useUIStore((s) => s.presentAs);
  const setPresentAs = useUIStore((s) => s.setPresentAs);
  const fullAccess = useUIStore((s) => s.fullAccessEnabled);
  const setFullAccessEnabled = useUIStore((s) => s.setFullAccessEnabled);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const active = currentPreset(chatMode, fullAccess);
  const codeAllowed = chatMode === "write";
  const codeActive = codeAllowed && presentAs === "code";

  const applyPreset = useCallback(
    async (key: PresetKey) => {
      if (key === "plan") {
        setChatMode("plan");
        return;
      }
      if (key === "observe") {
        setChatMode("read");
        if (fullAccess) {
          setFullAccessEnabled(false);
          if (activeSessionId) {
            try {
              await toggleFullAccess(activeSessionId, false);
            } catch {
              setFullAccessEnabled(true);
            }
          }
        }
        return;
      }
      setChatMode("write");
      const wantAuto = key === "auto";
      if (wantAuto !== fullAccess) {
        setFullAccessEnabled(wantAuto);
        if (activeSessionId) {
          try {
            await toggleFullAccess(activeSessionId, wantAuto);
          } catch {
            setFullAccessEnabled(!wantAuto);
          }
        }
      }
    },
    [activeSessionId, fullAccess, setChatMode, setFullAccessEnabled],
  );

  return (
    <div className="flex items-center gap-0.5 px-3 pt-1 pb-0" data-coach-id="coach-mode-presets">
      {PRESETS.map(({ key, label, icon: Icon }) => (
        <button
          key={key}
          onClick={() => void applyPreset(key)}
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
          <span className="relative z-10">{label}</span>
        </button>
      ))}
      <button
        type="button"
        disabled={!codeAllowed}
        onClick={() => setPresentAs(codeActive ? "native" : "code")}
        title={
          codeAllowed
            ? "Code Mode：只向模型暴露 run_code"
            : "观察/计划模式只能使用原生工具"
        }
        className={`relative ml-1 inline-flex items-center gap-1 px-2.5 h-7 rounded-lg text-xs font-medium transition-colors ${
          codeActive
            ? ""
            : "text-muted-foreground hover:text-foreground hover:bg-accent/40"
        } ${codeAllowed ? "" : "opacity-40 cursor-not-allowed hover:bg-transparent hover:text-muted-foreground"}`}
        style={codeActive ? { color: "var(--em-gold)" } : undefined}
        data-coach-id="coach-code-mode"
      >
        {codeActive && (
          <motion.div
            layoutId="code-mode-indicator"
            className="absolute inset-0 rounded-lg"
            style={{
              backgroundColor:
                "color-mix(in srgb, var(--em-gold) 15%, transparent)",
            }}
            transition={{ type: "spring", stiffness: 400, damping: 30 }}
          />
        )}
        <Code2 className="h-3 w-3 relative z-10" />
        <span className="relative z-10">代码</span>
      </button>
    </div>
  );
}
