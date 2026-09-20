"use client";

import { motion } from "framer-motion";
import { ArrowLeft, ArrowRight, Crown, Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PROVIDER_GUIDES } from "../provider-guides";
import { PROVIDER_LOGO_SLUG } from "../../settings/model/constants";
import type { ProviderGuide } from "../provider-guides";

const smoothEase: [number, number, number, number] = [0.4, 0, 0.2, 1];

const containerVariants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.045, delayChildren: 0.08 } },
};

const cardVariants = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.3, ease: smoothEase } },
};

function ProviderLogo({ id }: { id: string }) {
  const slug = PROVIDER_LOGO_SLUG[id];
  if (!slug) return null;
  return (
    <span
      className="inline-block h-7 w-7 shrink-0"
      role="img"
      aria-label={`${id} logo`}
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
  showOAuthConnectGuide?: boolean;
  checkingOAuthConnectStatus?: boolean;
  onGoConnectOAuth?: () => void;
}

export function ProviderSelectStep({
  onSelect,
  onBack,
  showOAuthConnectGuide = false,
  checkingOAuthConnectStatus = false,
  onGoConnectOAuth,
}: ProviderSelectStepProps) {
  return (
    <div className="em-onboarding-step em-onboarding-provider-select">
      <div className="em-onboarding-step-heading">
        <Button variant="ghost" size="sm" onClick={onBack} className="em-onboarding-back-button">
          <ArrowLeft aria-hidden="true" />
          返回
        </Button>
        <div className="em-onboarding-eyebrow">第 2 步 · 选择连接方式</div>
        <h2 tabIndex={-1}>连接一个 AI 模型</h2>
        <p>选择你常用的供应商，使用 API Key 或订阅授权连接。之后可在设置中随时切换或添加模型。</p>
      </div>

      {checkingOAuthConnectStatus && (
        <div className="em-onboarding-inline-notice" role="status">
          <Loader2 className="animate-spin" aria-hidden="true" />
          正在检查 OAuth 连接状态…
        </div>
      )}

      {!checkingOAuthConnectStatus && showOAuthConnectGuide && onGoConnectOAuth && (
        <div className="em-onboarding-oauth-banner">
          <span className="em-onboarding-feature-icon"><Sparkles aria-hidden="true" /></span>
          <div>
            <strong>可以直接使用 ChatGPT 订阅</strong>
            <p>连接 OpenAI Codex，无需手动填写 API Key。</p>
          </div>
          <Button onClick={onGoConnectOAuth} size="sm">去连接 <ArrowRight aria-hidden="true" /></Button>
        </div>
      )}

      <motion.div
        className="em-onboarding-provider-grid"
        variants={containerVariants}
        initial="hidden"
        animate="show"
        aria-label="AI 模型供应商"
      >
        {PROVIDER_GUIDES.map((provider) => (
          <motion.button
            key={provider.id}
            variants={cardVariants}
            whileHover={{ y: -2, transition: { duration: 0.15 } }}
            whileTap={{ scale: 0.985 }}
            onClick={() => onSelect(provider)}
            className={`em-onboarding-provider-card${provider.recommended ? " is-recommended" : ""}`}
            type="button"
            aria-label={`使用 ${provider.label}`}
          >
            <span className="em-onboarding-provider-logo"><ProviderLogo id={provider.id} /></span>
            <span className="em-onboarding-provider-copy">
              <span className="em-onboarding-provider-title">
                {provider.label}
                {provider.recommended && <span className="em-onboarding-recommended"><Crown aria-hidden="true" /> 推荐</span>}
              </span>
              <span className="em-onboarding-provider-description">{provider.description}</span>
              <span className="em-onboarding-provider-pricing">{provider.pricing}</span>
            </span>
            <ArrowRight className="em-onboarding-provider-arrow" aria-hidden="true" />
          </motion.button>
        ))}
      </motion.div>
    </div>
  );
}
