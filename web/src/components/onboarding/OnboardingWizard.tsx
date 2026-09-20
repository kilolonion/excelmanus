"use client";

import { useState, useRef } from "react";
import { motion, AnimatePresence, MotionConfig, useReducedMotion } from "framer-motion";
import { Dialog } from "radix-ui";
import { Check, Circle } from "lucide-react";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { WelcomeStep } from "./steps/WelcomeStep";
import { ProviderSelectStep } from "./steps/ProviderSelectStep";
import { ProviderGuideStep } from "./steps/ProviderGuideStep";
import { CompletionStep } from "./steps/CompletionStep";
import { useGuideViewport } from "./useTargetRect";
import type { ProviderGuide } from "./provider-guides";

const LABELS = ["欢迎", "选择模型", "连接模型", "开始体验"];

export function OnboardingWizard() {
  const rootRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const reduceMotion = useReducedMotion();
  const viewport = useGuideViewport();
  const [step, setStep] = useState(0);
  const [direction, setDirection] = useState(1);
  const [selectedProvider, setSelectedProvider] = useState<ProviderGuide | null>(null);
  const completeWizard = useOnboardingStore((s) => s.completeWizard);
  const skipWizard = useOnboardingStore((s) => s.skipWizard);
  const skipAll = useOnboardingStore((s) => s.skipAll);
  const backendConfigured = useOnboardingStore((s) => s.backendConfigured);

  const navigate = (next: number) => {
    setDirection(next > step ? 1 : -1);
    setStep(next);
  };
  const skipStep = () => navigate(step === 0 ? 1 : 3);

  return (
    <MotionConfig reducedMotion="user">
      <Dialog.Root open onOpenChange={(open) => { if (!open) skipAll(); }}>
        <Dialog.Portal>
          <Dialog.Content
            ref={rootRef}
            aria-describedby={undefined}
            tabIndex={-1}
            onEscapeKeyDown={(event) => { event.preventDefault(); skipAll(); }}
            onOpenAutoFocus={(event) => { event.preventDefault(); rootRef.current?.focus(); }}
            className="em-onboarding fixed z-[200] flex flex-col overflow-hidden outline-none"
            style={{ top: viewport.top, left: viewport.left, width: viewport.width, height: viewport.height }}
          >
            <Dialog.Title className="sr-only">ExcelManus 首次使用设置</Dialog.Title>
            <header className="em-onboarding-header flex-shrink-0">
              <div className="em-onboarding-header-inner">
                <div className="em-onboarding-brand">
                  <div className="em-onboarding-brand-mark" aria-hidden="true"><span style={{ backgroundImage: "url('/brand-icon.svg')" }} /></div>
                  <div><p className="em-onboarding-brand-name">ExcelManus</p><p className="em-onboarding-brand-caption">按你的节奏开始</p></div>
                </div>
                <nav className="em-onboarding-progress" aria-label="设置进度">
                  <ol className="em-onboarding-progress-track">
                    {LABELS.map((label, i) => (
                      <li key={label} aria-current={i === step ? "step" : undefined} className={`em-onboarding-progress-item${i === step ? " is-active" : ""}${i < step ? " is-complete" : ""}`}>
                        <span className="em-onboarding-progress-dot">{i < step ? <Check aria-hidden="true" /> : <Circle aria-hidden="true" />}</span>
                        <span className="em-onboarding-progress-label">{label}</span>
                      </li>
                    ))}
                  </ol>
                  <span className="em-onboarding-progress-count" aria-live="polite">{step + 1} / 4 · {LABELS[step]}</span>
                </nav>
                <div className="em-onboarding-header-actions">
                  {step < 3 && <button type="button" onClick={skipStep} className="em-onboarding-skip">跳过此步</button>}
                  <button type="button" onClick={skipAll} className="em-onboarding-skip">结束引导</button>
                </div>
              </div>
            </header>
            <div ref={contentRef} className="em-onboarding-content flex-1 min-h-0 overflow-y-auto">
              <AnimatePresence mode="wait" initial={false} onExitComplete={() => contentRef.current?.scrollTo(0, 0)}>
                <motion.div
                  key={step}
                  initial={{ opacity: 0, x: reduceMotion ? 0 : direction * 18 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: reduceMotion ? 0 : direction * -12 }}
                  transition={{ duration: reduceMotion ? 0 : 0.18, ease: "easeOut" }}
                  className="em-onboarding-step-shell"
                  onAnimationComplete={(definition) => {
                    if (typeof definition === "object" && "opacity" in definition && definition.opacity === 1) {
                      contentRef.current?.querySelector<HTMLElement>("h1, h2")?.focus({ preventScroll: true });
                    }
                  }}
                >
                  {step === 0 && <WelcomeStep onNext={() => navigate(1)} onSkip={() => navigate(3)} />}
                  {step === 1 && <ProviderSelectStep onSelect={(provider) => { setSelectedProvider(provider); navigate(2); }} onBack={() => navigate(0)} />}
                  {step === 2 && selectedProvider && <ProviderGuideStep key={selectedProvider.id} provider={selectedProvider} onBack={() => navigate(1)} onComplete={() => navigate(3)} onSkip={() => navigate(3)} />}
                  {step === 3 && <CompletionStep configured={backendConfigured === true} onFinish={backendConfigured === true ? completeWizard : skipWizard} onSkip={skipAll} onBack={() => navigate(1)} />}
                </motion.div>
              </AnimatePresence>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </MotionConfig>
  );
}
