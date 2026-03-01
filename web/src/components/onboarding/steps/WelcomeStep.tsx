"use client";

import { motion } from "framer-motion";
import { Sparkles, FileSpreadsheet, Bot, BarChart3, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";

const smoothEase: [number, number, number, number] = [0.4, 0, 0.2, 1];

const containerVariants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.1, delayChildren: 0.2 } },
};

const fadeUp = {
  hidden: { opacity: 0, y: 20 },
  show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: smoothEase } },
};

const FEATURES = [
  {
    icon: FileSpreadsheet,
    title: "智能读写",
    desc: "自然语言驱动的 Excel 操作",
    color: "var(--em-primary)",
  },
  {
    icon: BarChart3,
    title: "数据分析",
    desc: "自动洞察趋势与异常",
    color: "#3b82f6",
  },
  {
    icon: Bot,
    title: "AI 代理",
    desc: "多步骤任务自主规划执行",
    color: "#8b5cf6",
  },
  {
    icon: Sparkles,
    title: "公式生成",
    desc: "用自然语言描述即可生成公式",
    color: "#f59e0b",
  },
];

interface WelcomeStepProps {
  onNext: () => void;
  onSkip?: () => void;
  isAdmin: boolean;
}

export function WelcomeStep({ onNext, onSkip, isAdmin }: WelcomeStepProps) {
  return (
    <motion.div
      className="flex flex-col items-center justify-center min-h-full px-6 py-12"
      variants={containerVariants}
      initial="hidden"
      animate="show"
    >
      {/* Decorative orbs */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="onboarding-orb onboarding-orb-1" />
        <div className="onboarding-orb onboarding-orb-2" />
        <div className="onboarding-orb onboarding-orb-3" />
      </div>

      {/* Logo */}
      <motion.div variants={fadeUp} className="relative mb-4 sm:mb-6">
        <div className="absolute inset-0 -m-6 rounded-full bg-[var(--em-primary-alpha-10)] blur-2xl" />
        <img
          src="/logo.svg"
          alt="ExcelManus"
          className="relative h-12 sm:h-16 w-auto drop-shadow-md"
        />
      </motion.div>

      {/* Title */}
      <motion.h1
        variants={fadeUp}
        className="relative text-2xl sm:text-3xl md:text-4xl font-bold tracking-tight text-center mb-2 sm:mb-3"
      >
        欢迎使用{" "}
        <span
          className="bg-clip-text text-transparent"
          style={{
            backgroundImage:
              "linear-gradient(135deg, var(--em-primary), #8b5cf6)",
          }}
        >
          ExcelManus
        </span>
      </motion.h1>

      <motion.p
        variants={fadeUp}
        className="relative text-muted-foreground text-center max-w-md mb-6 sm:mb-10 text-xs sm:text-sm md:text-base px-2"
      >
        {isAdmin
          ? "让我们花 1 分钟完成初始配置，之后就可以用 AI 处理 Excel 了"
          : "配置你的 AI 模型，即可开始用自然语言处理 Excel"}
      </motion.p>

      {/* Feature cards */}
      <motion.div
        variants={fadeUp}
        className="relative grid grid-cols-1 sm:grid-cols-2 gap-2.5 sm:gap-3 max-w-lg w-full mb-6 sm:mb-10"
      >
        {FEATURES.map((f) => (
          <div
            key={f.title}
            className="flex items-start gap-3 p-3.5 rounded-xl border border-border/60 bg-background/60 backdrop-blur-sm"
          >
            <div
              className="flex-shrink-0 w-9 h-9 rounded-lg flex items-center justify-center"
              style={{ backgroundColor: `${f.color}15` }}
            >
              <f.icon
                className="h-[18px] w-[18px]"
                style={{ color: f.color }}
              />
            </div>
            <div className="min-w-0">
              <p className="text-sm font-semibold">{f.title}</p>
              <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
                {f.desc}
              </p>
            </div>
          </div>
        ))}
      </motion.div>

      {/* CTA */}
      <motion.div variants={fadeUp} className="relative flex flex-col items-center gap-3">
        <Button
          size="lg"
          onClick={onNext}
          className="h-11 sm:h-12 px-6 sm:px-8 text-sm sm:text-base font-semibold text-white gap-2 shadow-lg"
          style={{ backgroundColor: "var(--em-primary)" }}
        >
          开始配置
          <ArrowRight className="h-4 w-4" />
        </Button>
        {onSkip && (
          <button
            type="button"
            onClick={onSkip}
            className="text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            跳过，稍后在设置中配置
          </button>
        )}
      </motion.div>
    </motion.div>
  );
}
