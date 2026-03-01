"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { CheckCircle2, Sparkles, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";

const smoothEase: [number, number, number, number] = [0.4, 0, 0.2, 1];

interface CompletionStepProps {
  onFinish: () => void;
}

export function CompletionStep({ onFinish }: CompletionStepProps) {
  const [showConfetti, setShowConfetti] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setShowConfetti(true), 400);
    return () => clearTimeout(t);
  }, []);

  return (
    <div className="flex flex-col items-center justify-center min-h-full px-6 py-12">
      {/* Decorative orbs */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="onboarding-orb onboarding-orb-1" />
        <div className="onboarding-orb onboarding-orb-2" />
      </div>

      {/* Success icon */}
      <motion.div
        initial={{ scale: 0, rotate: -180 }}
        animate={{ scale: 1, rotate: 0 }}
        transition={{ duration: 0.6, ease: smoothEase, delay: 0.1 }}
        className="relative mb-8"
      >
        <div
          className="w-24 h-24 rounded-full flex items-center justify-center"
          style={{ backgroundColor: "var(--em-primary-alpha-10)" }}
        >
          <CheckCircle2
            className="h-12 w-12"
            style={{ color: "var(--em-primary)" }}
          />
        </div>

        {/* Confetti particles */}
        {showConfetti &&
          Array.from({ length: 12 }).map((_, i) => {
            const angle = (i / 12) * 360;
            const rad = (angle * Math.PI) / 180;
            const distance = 60 + Math.random() * 30;
            const colors = [
              "var(--em-primary)",
              "#8b5cf6",
              "#3b82f6",
              "#f59e0b",
              "#10b981",
              "#ef4444",
            ];
            return (
              <motion.div
                key={i}
                className="absolute top-1/2 left-1/2 w-2 h-2 rounded-full"
                style={{ backgroundColor: colors[i % colors.length] }}
                initial={{ x: 0, y: 0, scale: 0, opacity: 1 }}
                animate={{
                  x: Math.cos(rad) * distance,
                  y: Math.sin(rad) * distance,
                  scale: [0, 1.2, 0],
                  opacity: [1, 1, 0],
                }}
                transition={{
                  duration: 0.8,
                  delay: i * 0.03,
                  ease: "easeOut",
                }}
              />
            );
          })}
      </motion.div>

      {/* Text */}
      <motion.h1
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3, duration: 0.5, ease: smoothEase }}
        className="relative text-3xl font-bold tracking-tight text-center mb-3"
      >
        配置完成！
      </motion.h1>

      <motion.p
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.4, duration: 0.5, ease: smoothEase }}
        className="relative text-muted-foreground text-center max-w-sm mb-4 text-sm"
      >
        一切就绪，现在你可以用自然语言处理 Excel 了
      </motion.p>

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.5, duration: 0.5, ease: smoothEase }}
        className="relative flex flex-col items-center gap-3"
      >
        {/* Tips */}
        <div className="flex items-center gap-4 text-xs text-muted-foreground mb-4">
          <span className="flex items-center gap-1.5">
            <Sparkles className="h-3.5 w-3.5 text-amber-500" />
            上传文件或输入需求开始
          </span>
          <span className="flex items-center gap-1.5">
            <Sparkles className="h-3.5 w-3.5 text-purple-500" />
            支持拖拽上传
          </span>
        </div>

        <Button
          size="lg"
          onClick={onFinish}
          className="h-12 px-8 text-base font-semibold text-white gap-2 shadow-lg"
          style={{ backgroundColor: "var(--em-primary)" }}
        >
          开始使用
          <ArrowRight className="h-4 w-4" />
        </Button>
      </motion.div>
    </div>
  );
}
