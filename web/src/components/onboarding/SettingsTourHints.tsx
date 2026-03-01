"use client";

import { useState, useEffect } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import { Info, AlertTriangle, Lightbulb } from "lucide-react";
import { isSettingsDemoActive, onSettingsDemoChange } from "./demo-settings";
import { useOnboardingStore } from "@/stores/onboarding-store";

// ── Hint definitions ──

interface HintDef {
  /** CSS selector or data-coach-id for the anchor element. */
  anchor: string;
  /** Which settings tab this hint belongs to. */
  tab: string;
  /** Content of the hint bubble. */
  text: string;
  /** Hint icon type. */
  variant: "info" | "tip" | "warn";
  /** Placement relative to anchor. */
  placement: "right" | "bottom" | "top-right";
}

const HINTS: HintDef[] = [
  {
    anchor: "[data-coach-id='coach-settings-profiles']",
    tab: "model",
    text: "可创建多个模型配置，一键切换",
    variant: "tip",
    placement: "right",
  },
  {
    anchor: "[data-coach-id='coach-settings-rules-list']",
    tab: "rules",
    text: "规则越具体越有效，如「金额列保留两位小数」",
    variant: "tip",
    placement: "right",
  },
  {
    anchor: "[data-coach-id='coach-settings-mcp-add-btn']",
    tab: "mcp",
    text: "MCP 扩展 AI 的能力边界",
    variant: "info",
    placement: "bottom",
  },
  {
    anchor: "[data-coach-id='coach-settings-advanced-toggle']",
    tab: "runtime",
    text: "建议保持默认值",
    variant: "warn",
    placement: "right",
  },
];

// ── Variant styles ──

const VARIANT_CONFIG = {
  info: {
    icon: <Info className="h-3 w-3" />,
    bg: "bg-blue-50 dark:bg-blue-950/50",
    border: "border-blue-200 dark:border-blue-800",
    text: "text-blue-700 dark:text-blue-300",
    iconColor: "text-blue-500",
  },
  tip: {
    icon: <Lightbulb className="h-3 w-3" />,
    bg: "bg-amber-50 dark:bg-amber-950/50",
    border: "border-amber-200 dark:border-amber-800",
    text: "text-amber-700 dark:text-amber-300",
    iconColor: "text-amber-500",
  },
  warn: {
    icon: <AlertTriangle className="h-3 w-3" />,
    bg: "bg-orange-50 dark:bg-orange-950/50",
    border: "border-orange-200 dark:border-orange-800",
    text: "text-orange-700 dark:text-orange-300",
    iconColor: "text-orange-500",
  },
};

// ── Single hint bubble ──

function HintBubble({ def }: { def: HintDef }) {
  const [rect, setRect] = useState<DOMRect | null>(null);

  useEffect(() => {
    const update = () => {
      const el = document.querySelector(def.anchor);
      if (el) {
        setRect(el.getBoundingClientRect());
      } else {
        setRect(null);
      }
    };

    update();
    const interval = setInterval(update, 500);
    return () => clearInterval(interval);
  }, [def.anchor]);

  if (!rect) return null;

  const cfg = VARIANT_CONFIG[def.variant];

  // Position based on placement
  let style: React.CSSProperties;
  switch (def.placement) {
    case "right":
      style = {
        top: rect.top + rect.height / 2,
        left: rect.right + 8,
        transform: "translateY(-50%)",
      };
      break;
    case "bottom":
      style = {
        top: rect.bottom + 6,
        left: rect.left + rect.width / 2,
        transform: "translateX(-50%)",
      };
      break;
    case "top-right":
      style = {
        top: rect.top - 4,
        left: rect.right + 8,
        transform: "translateY(-100%)",
      };
      break;
  }

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.9 }}
      transition={{ duration: 0.2, delay: 0.3 }}
      className={`
        fixed z-[10001] pointer-events-none
        inline-flex items-center gap-1.5 px-2.5 py-1.5
        rounded-lg border shadow-sm
        settings-tour-hint
        ${cfg.bg} ${cfg.border}
      `}
      style={{ ...style, maxWidth: 200 }}
    >
      <span className={`flex-shrink-0 ${cfg.iconColor}`}>{cfg.icon}</span>
      <span className={`text-[11px] font-medium leading-tight ${cfg.text}`}>
        {def.text}
      </span>
    </motion.div>
  );
}

// ── Main component ──

interface SettingsTourHintsProps {
  activeTab: string;
}

export function SettingsTourHints({ activeTab }: SettingsTourHintsProps) {
  const isGuideLocked = useOnboardingStore((s) => s.isGuideLocked);
  const [demoActive, setDemoActive] = useState(isSettingsDemoActive);

  useEffect(() => {
    return onSettingsDemoChange(() => setDemoActive(isSettingsDemoActive()));
  }, []);

  // Only render hints when settings tour is active
  if (!isGuideLocked && !demoActive) return null;

  const activeHints = HINTS.filter((h) => h.tab === activeTab);
  if (activeHints.length === 0) return null;

  return createPortal(
    <AnimatePresence>
      {activeHints.map((hint) => (
        <HintBubble key={hint.anchor} def={hint} />
      ))}
    </AnimatePresence>,
    document.body,
  );
}
