"use client";

import { motion } from "framer-motion";
import { ArrowLeft, Crown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PROVIDER_GUIDES, PROVIDER_LOGO_SLUG } from "../provider-guides";
import type { ProviderGuide } from "../provider-guides";

const smoothEase: [number, number, number, number] = [0.4, 0, 0.2, 1];

const containerVariants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.05, delayChildren: 0.1 } },
};

const cardVariants = {
  hidden: { opacity: 0, y: 16, scale: 0.96 },
  show: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { duration: 0.35, ease: smoothEase },
  },
};

function ProviderLogo({ id }: { id: string }) {
  const slug = PROVIDER_LOGO_SLUG[id];
  if (!slug) return null;
  return (
    <span
      className="inline-block h-6 w-6 shrink-0"
      role="img"
      aria-label={id}
      style={{
        backgroundColor: "currentColor",
        maskImage: `url(/providers/${slug}.svg)`,
        WebkitMaskImage: `url(/providers/${slug}.svg)`,
        maskSize: "contain",
        WebkitMaskSize: "contain",
        maskRepeat: "no-repeat",
        WebkitMaskRepeat: "no-repeat",
        maskPosition: "center",
        WebkitMaskPosition: "center",
      }}
    />
  );
}

interface ProviderSelectStepProps {
  onSelect: (provider: ProviderGuide) => void;
  onBack: () => void;
}

export function ProviderSelectStep({
  onSelect,
  onBack,
}: ProviderSelectStepProps) {
  return (
    <div className="flex flex-col items-center min-h-full px-4 sm:px-6 py-6 sm:py-8">
      {/* Header */}
      <div className="max-w-2xl w-full mb-6 sm:mb-8">
        <Button
          variant="ghost"
          size="sm"
          onClick={onBack}
          className="gap-1.5 text-muted-foreground -ml-2 mb-4"
        >
          <ArrowLeft className="h-4 w-4" />
          返回
        </Button>

        <h2 className="text-2xl font-bold tracking-tight mb-2">
          选择你的 AI 模型供应商
        </h2>
        <p className="text-sm text-muted-foreground">
          选择一个供应商获取 API Key，之后随时可以在设置中修改或添加更多模型
        </p>
      </div>

      {/* Provider cards grid */}
      <motion.div
        className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-2xl w-full"
        variants={containerVariants}
        initial="hidden"
        animate="show"
      >
        {PROVIDER_GUIDES.map((provider) => (
          <motion.button
            key={provider.id}
            variants={cardVariants}
            whileHover={{ y: -2, transition: { duration: 0.15 } }}
            whileTap={{ scale: 0.98 }}
            onClick={() => onSelect(provider)}
            className="group relative flex items-start gap-3.5 rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm p-4 text-left transition-all duration-200 hover:border-[var(--em-primary)]/40 hover:shadow-md hover:bg-[var(--em-primary-alpha-06)] cursor-pointer"
          >
            {/* Recommended badge */}
            {provider.recommended && (
              <div
                className="absolute -top-2 right-3 flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold text-white"
                style={{ backgroundColor: "var(--em-primary)" }}
              >
                <Crown className="h-2.5 w-2.5" />
                推荐
              </div>
            )}

            {/* Logo */}
            <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-muted/50 flex items-center justify-center group-hover:bg-[var(--em-primary-alpha-10)] transition-colors">
              <ProviderLogo id={provider.id} />
            </div>

            {/* Info */}
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold group-hover:text-foreground transition-colors">
                {provider.label}
              </p>
              <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed line-clamp-2">
                {provider.description}
              </p>
              <p className="text-[11px] text-muted-foreground/70 mt-1.5">
                {provider.pricing}
              </p>
            </div>
          </motion.button>
        ))}
      </motion.div>
    </div>
  );
}
