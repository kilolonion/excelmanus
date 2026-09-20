import { create } from "zustand";
import { apiPut } from "@/lib/api";
import {
  type CoachPhase,
  type OnboardingSnapshot,
  clearLegacyLocalOnboarding,
  mergeOnboardingSnapshots,
  readLegacyLocalOnboarding,
  snapshotFromServer,
  snapshotToPayload,
  snapshotsEqual,
} from "@/stores/onboarding-state";

export type { CoachPhase };

interface OnboardingState extends OnboardingSnapshot {
  /** Runtime-only: whether the backend has valid model config (from /health `configured` field). */
  backendConfigured: boolean | null;
  /** Runtime-only: lets the settings tour card receive focus outside the settings dialog. */
  isGuideLocked: boolean;
  /** Runtime-only: incremented by resetToPhase so CoachMarks can detect external resets. */
  _resetGeneration: number;
  /** Runtime-only: true after /health has applied server onboarding state. */
  _userSynced: boolean;

  completeWizard: () => void;
  completeCoachMarks: () => void;
  completeAdvancedGuide: () => void;
  declineAdvancedGuide: () => void;
  completeSettingsGuide: () => void;
  declineSettingsGuide: () => void;
  skipWizard: () => void;
  skipAll: () => void;
  resetOnboarding: () => void;
  resetToPhase: (target: "wizard" | "basic" | "advanced" | "settings") => void;
  setCoachProgress: (phase: CoachPhase, stepIndex: number) => void;
  setBackendConfigured: (value: boolean) => void;
  setGuideLocked: (locked: boolean) => void;
  applyServerState: (raw: unknown, configured: boolean) => void;
}

function snapshotFromStore(state: OnboardingSnapshot): OnboardingSnapshot {
  return {
    wizardCompleted: state.wizardCompleted,
    coachMarksCompleted: state.coachMarksCompleted,
    advancedGuideCompleted: state.advancedGuideCompleted,
    settingsGuideCompleted: state.settingsGuideCompleted,
    skippedAt: state.skippedAt,
    coachPhase: state.coachPhase,
    coachStepIndex: state.coachStepIndex,
  };
}

export const useOnboardingStore = create<OnboardingState>()((set) => ({
  wizardCompleted: false,
  coachMarksCompleted: false,
  advancedGuideCompleted: false,
  settingsGuideCompleted: false,
  skippedAt: null,
  coachPhase: "basic",
  coachStepIndex: 0,
  backendConfigured: null,
  isGuideLocked: false,
  _resetGeneration: 0,
  _userSynced: false,

  completeWizard: () => {
    set({ wizardCompleted: true });
    persistOnboarding();
  },
  completeCoachMarks: () => {
    set({ coachMarksCompleted: true, coachPhase: "transition", coachStepIndex: 0 });
    persistOnboarding();
  },
  completeAdvancedGuide: () => {
    set({ advancedGuideCompleted: true, coachPhase: "settingsTransition", coachStepIndex: 0 });
    persistOnboarding();
  },
  declineAdvancedGuide: () => {
    set({ advancedGuideCompleted: true, coachPhase: "settingsTransition", coachStepIndex: 0 });
    persistOnboarding();
  },
  completeSettingsGuide: () => {
    set({ coachMarksCompleted: true, advancedGuideCompleted: true, settingsGuideCompleted: true, coachPhase: "done", coachStepIndex: 0, isGuideLocked: false });
    persistOnboarding();
  },
  declineSettingsGuide: () => {
    set({ settingsGuideCompleted: true, coachPhase: "done", coachStepIndex: 0 });
    persistOnboarding();
  },
  skipWizard: () => {
    set({ wizardCompleted: true, skippedAt: new Date().toISOString() });
    persistOnboarding();
  },
  skipAll: () => {
    set({
      wizardCompleted: true,
      coachMarksCompleted: true,
      advancedGuideCompleted: true,
      settingsGuideCompleted: true,
      coachPhase: "done",
      coachStepIndex: 0,
      isGuideLocked: false,
      skippedAt: new Date().toISOString(),
    });
    persistOnboarding();
  },
  resetOnboarding: () => {
    set({
      wizardCompleted: false,
      coachMarksCompleted: false,
      advancedGuideCompleted: false,
      settingsGuideCompleted: false,
      skippedAt: null,
      coachPhase: "basic",
      coachStepIndex: 0,
    });
    persistOnboarding();
  },
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
          coachPhase: "basic",
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
          coachPhase: "basic",
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
          coachPhase: "advanced",
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
          coachPhase: "settings",
          coachStepIndex: 0,
          isGuideLocked: false,
          _resetGeneration: gen,
        });
        break;
    }
    persistOnboarding();
  },
  setCoachProgress: (phase, stepIndex) => {
    set({ coachPhase: phase, coachStepIndex: stepIndex });
    persistOnboarding();
  },
  setBackendConfigured: (value) => set({ backendConfigured: value }),
  setGuideLocked: (locked) => set({ isGuideLocked: locked }),
  applyServerState: (raw, configured) => {
    const server = snapshotFromServer(raw, configured);
    const local = readLegacyLocalOnboarding();
    const merged = mergeOnboardingSnapshots(server, local);
    set({ ...merged, backendConfigured: configured, _userSynced: true });
    if (local && !snapshotsEqual(merged, server)) {
      persistOnboarding({ clearLegacyOnSuccess: true });
    } else {
      clearLegacyLocalOnboarding();
    }
  },
}));

// Serialize and coalesce progress writes: a slow previous step must never
// overwrite a later skip/completion on the server.
let pendingWrite: { payload: ReturnType<typeof snapshotToPayload>; clearLegacy: boolean } | null = null;
let writing = false;
const persistOnboarding = (options?: { clearLegacyOnSuccess?: boolean }): void => {
  pendingWrite = {
    payload: snapshotToPayload(snapshotFromStore(useOnboardingStore.getState())),
    clearLegacy: options?.clearLegacyOnSuccess !== false,
  };
  if (writing) return;
  writing = true;
  void (async () => {
    try {
      while (pendingWrite) {
        const next = pendingWrite;
        pendingWrite = null;
        try {
          await apiPut("/onboarding", next.payload);
          if (next.clearLegacy) clearLegacyLocalOnboarding();
        } catch {
          /* Continue with the latest progress; retry on the next user action. */
        }
      }
    } finally {
      writing = false;
    }
  })();
};
