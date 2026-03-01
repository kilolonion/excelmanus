"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useExcelStore } from "@/stores/excel-store";
import { useIsMobile } from "@/hooks/use-mobile";
import { getTourScenes, type TourStep, type TourScene } from "./tour-steps";
import { runEffect } from "./tour-effects";
import { TourOverlay } from "./TourOverlay";
import { TourTooltip } from "./TourTooltip";
import { TransitionCard } from "./TransitionCard";
import { useTargetRect } from "./useTargetRect";

// Re-export for SessionSync compatibility
export { DEMO_SESSION_PREFIX } from "./demo-session";

// ── Phase state machine ──

type Phase =
  | "basic"
  | "transition:basic-to-advanced"
  | "advanced"
  | "transition:advanced-to-settings"
  | "settings"
  | "done";

/** Map scene id to the CoachPhase string used in the persistent store. */
function phaseToStorePhase(phase: Phase): string {
  switch (phase) {
    case "basic": return "basic";
    case "transition:basic-to-advanced": return "transition";
    case "advanced": return "advanced";
    case "transition:advanced-to-settings": return "settingsTransition";
    case "settings": return "settings";
    case "done": return "done";
  }
}

function storePhaseToPhase(sp: string): Phase {
  switch (sp) {
    case "basic": return "basic";
    case "transition": return "transition:basic-to-advanced";
    case "advanced": return "advanced";
    case "settingsTransition": return "transition:advanced-to-settings";
    case "settings": return "settings";
    case "done": return "done";
    default: return "basic";
  }
}

function phaseToSceneId(phase: Phase): string | null {
  switch (phase) {
    case "basic": return "basic";
    case "advanced": return "advanced";
    case "settings": return "settings";
    default: return null;
  }
}

// ── Main Controller ──

export function CoachMarks() {
  const persistedPhase = useOnboardingStore((s) => s.coachPhase);
  const persistedStep = useOnboardingStore((s) => s.coachStepIndex);
  const resetGeneration = useOnboardingStore((s) => s._resetGeneration);
  const completeCoachMarks = useOnboardingStore((s) => s.completeCoachMarks);
  const completeAdvancedGuide = useOnboardingStore((s) => s.completeAdvancedGuide);
  const completeSettingsGuide = useOnboardingStore((s) => s.completeSettingsGuide);
  const setCoachProgress = useOnboardingStore((s) => s.setCoachProgress);
  const clearDemoFile = useExcelStore((s) => s.clearDemoFile);
  const isMobile = useIsMobile();

  const [mounted, setMounted] = useState(false);
  const [phase, setPhase] = useState<Phase>(() => storePhaseToPhase(persistedPhase));
  const [stepIndex, setStepIndex] = useState(persistedStep);
  const [interactionDone, setInteractionDone] = useState(false);

  const autoAdvanceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const interactionCleanup = useRef<(() => void) | null>(null);
  const handleNextRef = useRef<() => void>(() => {});
  const sceneEnterRan = useRef(false);
  const prevResetGen = useRef(resetGeneration);

  const scenes = getTourScenes(isMobile);

  // Delayed mount to avoid hydration flash
  useEffect(() => {
    const t = setTimeout(() => setMounted(true), 500);
    return () => clearTimeout(t);
  }, []);

  // B2: Run onSceneEnter on initial mount when restoring from persistence.
  // Without this, refreshing mid-tour skips scene setup (e.g. demo session creation).
  useEffect(() => {
    if (!mounted || sceneEnterRan.current) return;
    sceneEnterRan.current = true;
    const sid = phaseToSceneId(phase);
    if (sid) {
      const s = scenes.find((sc) => sc.id === sid);
      if (s?.onSceneEnter) runEffect(s.onSceneEnter);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mounted]);

  // Detect external resets (e.g. OnboardingReplayCard calling resetToPhase)
  // and sync local state from the store so the tour restarts without a page refresh.
  useEffect(() => {
    if (resetGeneration <= prevResetGen.current) return;
    prevResetGen.current = resetGeneration;

    cleanup();

    const newPhase = storePhaseToPhase(persistedPhase);
    const newStepIndex = persistedStep;

    // Run scene enter for the new phase
    const sid = phaseToSceneId(newPhase);
    if (sid) {
      const s = scenes.find((sc) => sc.id === sid);
      if (s?.onSceneEnter) runEffect(s.onSceneEnter);
    }

    setPhase(newPhase);
    setStepIndex(newStepIndex);
    setInteractionDone(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetGeneration]);

  // Sync to persistent store
  useEffect(() => {
    setCoachProgress(phaseToStorePhase(phase) as any, stepIndex);
  }, [phase, stepIndex, setCoachProgress]);

  // ── Get current scene & step ──
  const sceneId = phaseToSceneId(phase);
  const scene = sceneId ? scenes.find((s) => s.id === sceneId) : null;
  const currentStep = scene ? scene.steps[stepIndex] : null;

  // ── Target rect tracking (single RAF loop shared by overlay + tooltip) ──
  const targetRect = useTargetRect(currentStep?.target ?? "", currentStep?.expandTarget);

  // Cleanup helper
  const cleanup = useCallback(() => {
    if (autoAdvanceTimer.current) {
      clearTimeout(autoAdvanceTimer.current);
      autoAdvanceTimer.current = null;
    }
    if (interactionCleanup.current) {
      interactionCleanup.current();
      interactionCleanup.current = null;
    }
  }, []);

  // Fire onEnter when step changes
  useEffect(() => {
    if (!mounted || !currentStep) {
      cleanup();
      return;
    }

    runEffect(currentStep.onEnter);
    setInteractionDone(false);

    // B5: return cleanup so React clears timers when deps change
    return () => { cleanup(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mounted, phase, stepIndex]);

  // ── Interaction listeners ──
  useEffect(() => {
    if (!currentStep?.interaction || interactionDone) return;
    const ia = currentStep.interaction;
    const selector = currentStep.target.startsWith("[")
      ? currentStep.target
      : `[data-coach-id="${currentStep.target}"]`;

    let cancelled = false;
    let cleanupFn: (() => void) | null = null;

    const triggerDone = () => {
      if (cancelled) return;
      runEffect(currentStep.onInteractionDone);
      setInteractionDone(true);
      const delay = ia.autoAdvanceMs ?? 800;
      autoAdvanceTimer.current = setTimeout(() => handleNextRef.current(), delay);
    };

    /** Attach listeners once the target element is in the DOM. */
    const attach = (targetEl: Element) => {
      if (cancelled) return;

      if (ia.type === "click" || ia.type === "navigate") {
        const handler = () => triggerDone();
        targetEl.addEventListener("click", handler, { once: true, capture: true });
        cleanupFn = () => targetEl.removeEventListener("click", handler, { capture: true });
        interactionCleanup.current = cleanupFn;
        return;
      }

      if (ia.type === "input") {
        const trigger = ia.inputTrigger;
        const findInput = (root: Element) =>
          (root.querySelector("textarea") ?? root.querySelector("input")) as HTMLTextAreaElement | HTMLInputElement | null;
        if (trigger) {
          const inputEl = findInput(targetEl);
          const current = inputEl?.value ?? "";
          // Don't clear if current input is already a prefix of the new trigger (e.g. "/" → "/read")
          if (!current || !trigger.startsWith(current)) {
            window.dispatchEvent(new Event("coach-clear-input"));
          }
        }
        const inputEl = findInput(targetEl);
        inputEl?.focus();

        const interval = setInterval(() => {
          const el = findInput(targetEl);
          if (!el) return;
          const val = el.value ?? "";
          const matched = trigger ? val.includes(trigger) : val.length > 0;
          if (matched) {
            clearInterval(interval);
            triggerDone();
          }
        }, 150);
        cleanupFn = () => clearInterval(interval);
        interactionCleanup.current = cleanupFn;
        return;
      }

      if (ia.type === "drag") {
        const interval = setInterval(() => {
          const textarea = document.querySelector(
            '[data-coach-id="coach-chat-input"] textarea'
          ) as HTMLTextAreaElement | null;
          if (!textarea) return;
          if ((textarea.value ?? "").includes("@file:")) {
            clearInterval(interval);
            triggerDone();
          }
        }, 150);
        cleanupFn = () => clearInterval(interval);
        interactionCleanup.current = cleanupFn;
        return;
      }
    };

    // Try immediately; if not found, poll up to 2s (handles tab-switch animation delay)
    const el = document.querySelector(selector);
    if (el) {
      attach(el);
    } else {
      let retries = 0;
      const poll = setInterval(() => {
        if (cancelled) { clearInterval(poll); return; }
        const found = document.querySelector(selector);
        if (found) {
          clearInterval(poll);
          attach(found);
        } else if (++retries > 20) {
          clearInterval(poll);
        }
      }, 100);
      cleanupFn = () => clearInterval(poll);
      interactionCleanup.current = cleanupFn;
    }

    return () => {
      cancelled = true;
      if (cleanupFn) cleanupFn();
      interactionCleanup.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentStep, stepIndex, interactionDone]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      cleanup();
    };
  }, [cleanup]);

  // ── Navigation ──

  const handleNext = useCallback(() => {
    if (autoAdvanceTimer.current) {
      clearTimeout(autoAdvanceTimer.current);
      autoAdvanceTimer.current = null;
    }

    if (!scene) return;

    if (stepIndex < scene.steps.length - 1) {
      setStepIndex((s) => s + 1);
      if (scene.id === "basic") clearDemoFile();
    } else {
      // Scene completed
      cleanup();
      runEffect(scene.onSceneExit);

      if (scene.id === "basic") {
        clearDemoFile();
        completeCoachMarks();
        setPhase("transition:basic-to-advanced");
        setStepIndex(0);
      } else if (scene.id === "advanced") {
        completeAdvancedGuide();
        setPhase("transition:advanced-to-settings");
        setStepIndex(0);
      } else if (scene.id === "settings") {
        completeSettingsGuide();
        setPhase("done");
        setStepIndex(0);
      }
    }
  }, [scene, stepIndex, cleanup, clearDemoFile, completeCoachMarks, completeAdvancedGuide, completeSettingsGuide]);

  // B3: Keep handleNext ref fresh so setTimeout closures never go stale
  handleNextRef.current = handleNext;

  const handleSkip = useCallback(() => {
    cleanup();
    if (scene) runEffect(scene.onSceneExit);
    clearDemoFile();
    completeCoachMarks();
    completeAdvancedGuide();
    completeSettingsGuide();
    setPhase("done");
  }, [cleanup, scene, clearDemoFile, completeCoachMarks, completeAdvancedGuide, completeSettingsGuide]);

  // ── Transition card handlers ──

  const handleStartAdvanced = useCallback(() => {
    const advancedScene = scenes.find((s) => s.id === "advanced");
    if (advancedScene) runEffect(advancedScene.onSceneEnter);
    setPhase("advanced");
    setStepIndex(0);
  }, [scenes]);

  const handleDeclineAdvanced = useCallback(() => {
    completeAdvancedGuide();
    setPhase("transition:advanced-to-settings");
    setStepIndex(0);
  }, [completeAdvancedGuide]);

  const handleStartSettings = useCallback(() => {
    const settingsScene = scenes.find((s) => s.id === "settings");
    if (settingsScene) runEffect(settingsScene.onSceneEnter);
    setPhase("settings");
    setStepIndex(0);
  }, [scenes]);

  const handleDeclineSettings = useCallback(() => {
    completeSettingsGuide();
    setPhase("done");
  }, [completeSettingsGuide]);

  // ── Render ──

  if (!mounted || phase === "done") return null;

  // Transition cards
  if (phase === "transition:basic-to-advanced") {
    return <TransitionCard variant="basic-to-advanced" onContinue={handleStartAdvanced} onDecline={handleDeclineAdvanced} />;
  }
  if (phase === "transition:advanced-to-settings") {
    return <TransitionCard variant="advanced-to-settings" onContinue={handleStartSettings} onDecline={handleDeclineSettings} />;
  }

  // Active tour step
  if (currentStep && scene) {
    const isInteractive = !!currentStep.interaction;
    const interactionType = currentStep.interaction?.type;
    // Settings phase: overlay is always visual-only (passThrough) because the
    // Dialog has its own stacking context and pointer-event handling that
    // conflicts with the 4-div overlay approach.
    // drag/input steps also need passThrough so gestures cross overlay boundaries.
    const isSettingsPhase = phase === "settings";
    const needsPassThrough = isSettingsPhase
      || (isInteractive && !interactionDone
        && (interactionType === "drag" || interactionType === "input" || interactionType === "navigate"));
    return (
      <>
        <TourOverlay
          targetRect={targetRect}
          padding={currentStep.stagePadding ?? 6}
          allowInteraction={isInteractive && !interactionDone}
          pulse={isInteractive && !interactionDone}
          passThrough={needsPassThrough}
        />
        <TourTooltip
          step={currentStep}
          stepIndex={stepIndex}
          totalSteps={scene.steps.length}
          phaseLabel={scene.label}
          interactionDone={interactionDone}
          targetRect={targetRect}
          onNext={handleNext}
          onSkip={handleSkip}
          isMobile={isMobile}
        />
      </>
    );
  }

  return null;
}
