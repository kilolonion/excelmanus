"use client";

import { useState, useEffect, useCallback } from "react";
import { MotionConfig } from "framer-motion";
import { useOnboardingStore, type CoachPhase } from "@/stores/onboarding-store";
import { useUIStore } from "@/stores/ui-store";
import { useIsMobile } from "@/hooks/use-mobile";
import { getTourScenes } from "./tour-steps";
import { runEffect } from "./tour-effects";
import { TourOverlay } from "./TourOverlay";
import { TourTooltip } from "./TourTooltip";
import { TransitionCard } from "./TransitionCard";
import { findTourTarget, useTargetRect } from "./useTargetRect";

export { DEMO_SESSION_PREFIX } from "./demo-session";

export function CoachMarks() {
  const phase = useOnboardingStore((s) => s.coachPhase);
  const persistedStep = useOnboardingStore((s) => s.coachStepIndex);
  const setProgress = useOnboardingStore((s) => s.setCoachProgress);
  const skipAll = useOnboardingStore((s) => s.skipAll);
  const isMobile = useIsMobile();
  const scenes = getTourScenes(isMobile);
  const scene = scenes.find((item) => item.id === phase);
  const stepIndex = Math.min(persistedStep, (scene?.steps.length ?? 1) - 1);
  const step = scene?.steps[stepIndex];
  const stepId = `${phase}-${stepIndex}`;
  const [completedStep, setCompletedStep] = useState("");
  const interactionDone = completedStep === stepId;
  const targetRect = useTargetRect(step?.target ?? "", step?.expandTarget);

  useEffect(() => {
    const { sidebarOpen, sidebarTab, settingsOpen, settingsTab } = useUIStore.getState();
    return () => {
      useOnboardingStore.getState().setGuideLocked(false);
      useUIStore.setState({ sidebarOpen, sidebarTab, settingsOpen, settingsTab });
    };
  }, []);

  const onEnter = step?.onEnter;
  useEffect(() => {
    useOnboardingStore.getState().setGuideLocked(phase === "settings");
    if (phase !== "settings") useUIStore.getState().closeSettings();
    runEffect(onEnter);
  }, [phase, onEnter, isMobile]);

  const finishSection = useCallback(() => {
    const store = useOnboardingStore.getState();
    if (phase === "basic") store.completeCoachMarks();
    if (phase === "advanced") store.completeAdvancedGuide();
    if (phase === "settings") store.completeSettingsGuide();
  }, [phase]);

  const next = () => {
    if (!scene) return;
    if (stepIndex < scene.steps.length - 1) setProgress(scene.id, stepIndex + 1);
    else finishSection();
  };
  const previous = () => {
    if (stepIndex > 0) setProgress(phase, stepIndex - 1);
    else {
      const previousScene = scenes[scenes.findIndex((item) => item.id === phase) - 1];
      if (previousScene) setProgress(previousScene.id, previousScene.steps.length - 1);
    }
  };
  const goToSection = (nextPhase: CoachPhase) => setProgress(nextPhase, 0);
  const locate = () => {
    runEffect(onEnter);
    if (step) findTourTarget(step.target)?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
  };

  // No automatic advancement: finishing an exercise never steals focus, and
  // skipping cannot leave a delayed timer that advances another step.
  if (phase === "done") return null;
  return (
    <MotionConfig reducedMotion="user">
      {phase === "transition" ? (
        <TransitionCard variant="basic-to-advanced" onContinue={() => goToSection("advanced")} onDecline={() => goToSection("settings")} onSkip={skipAll} />
      ) : phase === "settingsTransition" ? (
        <TransitionCard variant="advanced-to-settings" onContinue={() => goToSection("settings")} onDecline={skipAll} onSkip={skipAll} />
      ) : step && scene ? (
        <>
          <TourOverlay targetRect={targetRect} padding={step.stagePadding ?? 6} />
          <TourTooltip
            step={step} stepIndex={stepIndex} totalSteps={scene.steps.length}
            phase={scene.id} phaseLabel={scene.label} interactionDone={interactionDone}
            targetRect={targetRect} onNext={next} onPrevious={previous}
            hasPrevious={phase !== "basic" || stepIndex > 0}
            onSkip={skipAll} onSkipSection={finishSection} onSectionChange={goToSection}
            onLocate={locate} onPracticeDone={() => setCompletedStep(stepId)} isMobile={isMobile}
          />
        </>
      ) : null}
    </MotionConfig>
  );
}
