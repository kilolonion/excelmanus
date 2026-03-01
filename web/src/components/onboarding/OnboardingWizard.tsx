"use client";

import { useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useAuthStore } from "@/stores/auth-store";
import { useAuthConfigStore } from "@/stores/auth-config-store";
import { WelcomeStep } from "./steps/WelcomeStep";
import { ProviderSelectStep } from "./steps/ProviderSelectStep";
import { ProviderGuideStep } from "./steps/ProviderGuideStep";
import { CompletionStep } from "./steps/CompletionStep";
import type { ProviderGuide } from "./provider-guides";

const slideVariants = {
  enter: (dir: number) => ({
    x: dir > 0 ? 80 : -80,
    opacity: 0,
  }),
  center: { x: 0, opacity: 1 },
  exit: (dir: number) => ({
    x: dir > 0 ? -80 : 80,
    opacity: 0,
  }),
};

export function OnboardingWizard() {
  const [step, setStep] = useState(0);
  const [direction, setDirection] = useState(1);
  const [selectedProvider, setSelectedProvider] = useState<ProviderGuide | null>(null);

  const completeWizard = useOnboardingStore((s) => s.completeWizard);
  const skipWizard = useOnboardingStore((s) => s.skipWizard);
  const backendConfigured = useOnboardingStore((s) => s.backendConfigured);
  const user = useAuthStore((s) => s.user);
  const authEnabled = useAuthConfigStore((s) => s.authEnabled);
  const isAdmin = !authEnabled || !user || user.role === "admin";

  // When backend config is missing, skip is not allowed — user must configure to proceed
  const configRequired = backendConfigured === false;

  const goNext = useCallback(() => {
    setDirection(1);
    setStep((s) => s + 1);
  }, []);

  const goBack = useCallback(() => {
    setDirection(-1);
    setStep((s) => Math.max(0, s - 1));
  }, []);

  const handleSelectProvider = useCallback(
    (provider: ProviderGuide) => {
      setSelectedProvider(provider);
      goNext();
    },
    [goNext]
  );

  const handleComplete = useCallback(() => {
    completeWizard();
  }, [completeWizard]);

  const handleSkip = useCallback(() => {
    if (configRequired) return; // Cannot skip when config is required
    skipWizard();
  }, [skipWizard, configRequired]);

  const skipHandler = configRequired ? undefined : handleSkip;

  const steps = [
    <WelcomeStep key="welcome" onNext={goNext} onSkip={skipHandler} isAdmin={isAdmin} />,
    <ProviderSelectStep
      key="provider-select"
      onSelect={handleSelectProvider}
      onBack={goBack}
    />,
    selectedProvider ? (
      <ProviderGuideStep
        key="provider-guide"
        provider={selectedProvider}
        isAdmin={isAdmin}
        onBack={goBack}
        onComplete={goNext}
        onSkip={skipHandler}
      />
    ) : null,
    <CompletionStep key="completion" onFinish={handleComplete} />,
  ].filter(Boolean);

  const currentStep = steps[step] || steps[steps.length - 1];
  const totalSteps = steps.length;

  return (
    <div className="fixed inset-0 z-[200] bg-background flex flex-col overflow-hidden">
      {/* Progress bar */}
      <div className="flex-shrink-0 px-6 pt-4">
        <div className="max-w-2xl mx-auto">
          <div className="flex items-center gap-1.5">
            {Array.from({ length: totalSteps }).map((_, i) => (
              <div
                key={i}
                className="flex-1 h-1 rounded-full transition-colors duration-300"
                style={{
                  backgroundColor:
                    i <= step
                      ? "var(--em-primary)"
                      : "var(--em-primary-alpha-15)",
                }}
              />
            ))}
          </div>
          <div className="flex justify-between mt-1.5">
            <span className="text-[11px] text-muted-foreground">
              步骤 {step + 1} / {totalSteps}
            </span>
            {step < totalSteps - 1 && !configRequired && (
              <button
                type="button"
                onClick={handleSkip}
                className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
              >
                跳过，稍后配置
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Step content */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <AnimatePresence mode="wait" custom={direction}>
          <motion.div
            key={step}
            custom={direction}
            variants={slideVariants}
            initial="enter"
            animate="center"
            exit="exit"
            transition={{ duration: 0.3, ease: "easeOut" }}
            className="h-full"
          >
            {currentStep}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}
