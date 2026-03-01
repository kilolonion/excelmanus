"use client";

import { createPortal } from "react-dom";
import { motion } from "framer-motion";
import { Compass, Settings, Rocket } from "lucide-react";
import { Button } from "@/components/ui/button";

interface TransitionCardProps {
  variant: "basic-to-advanced" | "advanced-to-settings";
  onContinue: () => void;
  onDecline: () => void;
}

const CONFIG = {
  "basic-to-advanced": {
    icon: <Compass className="h-7 w-7" style={{ color: "var(--em-primary)" }} />,
    title: "基础引导完成！",
    description: "你已经了解了核心功能。\n想继续探索进阶技巧吗？",
    declineText: "稍后探索",
    continueText: "继续探索",
    continueIcon: <Rocket className="h-4 w-4" />,
  },
  "advanced-to-settings": {
    icon: <Settings className="h-7 w-7" style={{ color: "var(--em-primary)" }} />,
    title: "进阶引导完成！",
    description: "接下来带你了解设置面板的各项功能，\n掌握模型、规则、技能等高级配置。",
    declineText: "稍后再看",
    continueText: "探索设置",
    continueIcon: <Settings className="h-4 w-4" />,
  },
};

export function TransitionCard({ variant, onContinue, onDecline }: TransitionCardProps) {
  const cfg = CONFIG[variant];

  return createPortal(
    <div className="fixed inset-0 z-[10001] flex items-center justify-center" style={{ pointerEvents: "auto" }}>
      {/* Backdrop — clicking declines */}
      <div className="absolute inset-0 bg-black/50" onClick={onDecline} />
      <motion.div
        initial={{ opacity: 0, scale: 0.92, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 10 }}
        transition={{ duration: 0.3 }}
        className="relative z-10 w-[90vw] max-w-sm rounded-2xl bg-background border border-border shadow-2xl p-6 text-center"
      >
        <div
          className="mx-auto w-14 h-14 rounded-full flex items-center justify-center mb-4"
          style={{ backgroundColor: "var(--em-primary-alpha-10)" }}
        >
          {cfg.icon}
        </div>
        <h3 className="text-lg font-bold mb-1.5">{cfg.title}</h3>
        <p className="text-sm text-muted-foreground mb-5 leading-relaxed whitespace-pre-line">
          {cfg.description}
        </p>
        <div className="flex flex-col sm:flex-row gap-2.5">
          <Button variant="outline" className="flex-1 h-10 gap-1.5" onClick={onDecline}>
            {cfg.declineText}
          </Button>
          <Button
            className="flex-1 h-10 gap-1.5 text-white"
            style={{ backgroundColor: "var(--em-primary)" }}
            onClick={onContinue}
          >
            {cfg.continueIcon}
            {cfg.continueText}
          </Button>
        </div>
      </motion.div>
    </div>,
    document.body
  );
}
