import { create } from "zustand";
import { persist } from "zustand/middleware";

type CoachPhase = "basic" | "transition" | "advanced" | "settingsTransition" | "settings" | "done";

interface OnboardingState {
  wizardCompleted: boolean;
  coachMarksCompleted: boolean;
  advancedGuideCompleted: boolean;
  settingsGuideCompleted: boolean;
  skippedAt: string | null;
  coachPhase: CoachPhase;
  coachStepIndex: number;
  /** Runtime-only: whether the backend has valid model config (from /health `configured` field). */
  backendConfigured: boolean | null;
  /** Runtime-only: true while the settings tour is active — prevents closing the settings dialog. */
  isGuideLocked: boolean;
  /** Runtime-only: incremented by resetToPhase so CoachMarks can detect external resets. */
  _resetGeneration: number;
  /** Runtime-only: true once the store has re-hydrated with the correct per-user key. */
  _userSynced: boolean;

  completeWizard: () => void;
  completeCoachMarks: () => void;
  completeAdvancedGuide: () => void;
  declineAdvancedGuide: () => void;
  completeSettingsGuide: () => void;
  declineSettingsGuide: () => void;
  skipWizard: () => void;
  resetOnboarding: () => void;
  resetToPhase: (target: "wizard" | "basic" | "advanced" | "settings") => void;
  setCoachProgress: (phase: CoachPhase, stepIndex: number) => void;
  setBackendConfigured: (value: boolean) => void;
  setGuideLocked: (locked: boolean) => void;
}

export const useOnboardingStore = create<OnboardingState>()(
  persist(
    (set) => ({
      wizardCompleted: false,
      coachMarksCompleted: false,
      advancedGuideCompleted: false,
      settingsGuideCompleted: false,
      skippedAt: null,
      coachPhase: "basic" as CoachPhase,
      coachStepIndex: 0,
      backendConfigured: null,
      isGuideLocked: false,
      _resetGeneration: 0,
      _userSynced: true,

      completeWizard: () => set({ wizardCompleted: true }),
      completeCoachMarks: () =>
        set({ coachMarksCompleted: true, coachPhase: "transition" as CoachPhase, coachStepIndex: 0 }),
      completeAdvancedGuide: () =>
        set({ advancedGuideCompleted: true, coachPhase: "settingsTransition" as CoachPhase, coachStepIndex: 0 }),
      declineAdvancedGuide: () =>
        set({ advancedGuideCompleted: true, coachPhase: "settingsTransition" as CoachPhase, coachStepIndex: 0 }),
      completeSettingsGuide: () =>
        set({ settingsGuideCompleted: true, coachPhase: "done" as CoachPhase, coachStepIndex: 0 }),
      declineSettingsGuide: () =>
        set({ settingsGuideCompleted: true, coachPhase: "done" as CoachPhase, coachStepIndex: 0 }),
      skipWizard: () =>
        set({ wizardCompleted: true, skippedAt: new Date().toISOString() }),
      resetOnboarding: () =>
        set({
          wizardCompleted: false,
          coachMarksCompleted: false,
          advancedGuideCompleted: false,
          settingsGuideCompleted: false,
          skippedAt: null,
          coachPhase: "basic" as CoachPhase,
          coachStepIndex: 0,
        }),
      resetToPhase: (target) => {
        const gen = useOnboardingStore.getState()._resetGeneration + 1;
        switch (target) {
          case "wizard":
            set({
              wizardCompleted: false,
              coachMarksCompleted: false,
              advancedGuideCompleted: false,
              settingsGuideCompleted: false,
              skippedAt: null,
              coachPhase: "basic" as CoachPhase,
              coachStepIndex: 0,
              isGuideLocked: false,
              _resetGeneration: gen,
            });
            break;
          case "basic":
            set({
              wizardCompleted: true,
              coachMarksCompleted: false,
              advancedGuideCompleted: false,
              settingsGuideCompleted: false,
              skippedAt: null,
              coachPhase: "basic" as CoachPhase,
              coachStepIndex: 0,
              isGuideLocked: false,
              _resetGeneration: gen,
            });
            break;
          case "advanced":
            set({
              wizardCompleted: true,
              coachMarksCompleted: true,
              advancedGuideCompleted: false,
              settingsGuideCompleted: false,
              skippedAt: null,
              coachPhase: "advanced" as CoachPhase,
              coachStepIndex: 0,
              isGuideLocked: false,
              _resetGeneration: gen,
            });
            break;
          case "settings":
            set({
              wizardCompleted: true,
              coachMarksCompleted: true,
              advancedGuideCompleted: true,
              settingsGuideCompleted: false,
              skippedAt: null,
              coachPhase: "settings" as CoachPhase,
              coachStepIndex: 0,
              isGuideLocked: false,
              _resetGeneration: gen,
            });
            break;
        }
      },
      setCoachProgress: (phase, stepIndex) =>
        set({ coachPhase: phase, coachStepIndex: stepIndex }),
      setBackendConfigured: (value) => set({ backendConfigured: value }),
      setGuideLocked: (locked) => set({ isGuideLocked: locked }),
    }),
    {
      name: "excelmanus-onboarding",
      partialize: (state) => {
        const { backendConfigured: _, isGuideLocked: _2, _resetGeneration: _3, _userSynced: _4, ...persisted } = state;
        return persisted;
      },
    }
  )
);

if (typeof window !== "undefined") {
  useOnboardingStore.setState({ _userSynced: true });
}
