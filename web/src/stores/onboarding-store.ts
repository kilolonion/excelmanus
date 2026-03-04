import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { useAuthStore } from "@/stores/auth-store";

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

/** Get current user ID for per-user localStorage isolation. */
function _getUserId(): string {
  return useAuthStore.getState().user?.id ?? "anonymous";
}

/** Custom storage that scopes localStorage keys by user ID. */
const _perUserStorage = createJSONStorage<Partial<OnboardingState>>(() => ({
  getItem(name: string) {
    return localStorage.getItem(`${name}:${_getUserId()}`);
  },
  setItem(name: string, value: string) {
    localStorage.setItem(`${name}:${_getUserId()}`, value);
  },
  removeItem(name: string) {
    localStorage.removeItem(`${name}:${_getUserId()}`);
  },
}));

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
      _userSynced: false,

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
      storage: _perUserStorage,
      partialize: (state) => {
        // Exclude runtime-only fields from localStorage persistence
        const { backendConfigured: _, isGuideLocked: _2, _resetGeneration: _3, _userSynced: _4, ...persisted } = state;
        return persisted;
      },
    }
  )
);

// ── Auth-aware rehydration ──
// On first load the store may hydrate with the "anonymous" key before auth
// resolves. Once the real user id is known we re-hydrate from the correct
// per-user key and flip _userSynced so the UI can safely render.
let _prevUserId: string | undefined;
let _initialSyncDone = false;

function _onAuthResolved(): void {
  if (_initialSyncDone) return;
  _initialSyncDone = true;
  const uid = useAuthStore.getState().user?.id ?? "anonymous";
  _prevUserId = uid;
  if (uid !== "anonymous") {
    // Store may have hydrated with the "anonymous" key — re-hydrate with the
    // real user key so we read the correct persisted state.
    Promise.resolve(useOnboardingStore.persist.rehydrate()).then(() => {
      useOnboardingStore.setState({ _userSynced: true });
    });
  } else {
    useOnboardingStore.setState({ _userSynced: true });
  }
}

// Trigger initial sync once auth store finishes hydrating.
// Guard: persist APIs are unavailable during SSR (no localStorage).
if (typeof window !== "undefined") {
  if (useAuthStore.persist.hasHydrated()) {
    _onAuthResolved();
  } else {
    useAuthStore.persist.onFinishHydration(() => _onAuthResolved());
  }

  // Re-hydrate when user identity changes at runtime (login / logout / switch).
  useAuthStore.subscribe((state) => {
    const uid = state.user?.id ?? "anonymous";
    if (_prevUserId !== undefined && _prevUserId !== uid) {
      _prevUserId = uid;
      // Temporarily block rendering to avoid a flash of stale onboarding state
      // while rehydrating from the new per-user localStorage key.
      useOnboardingStore.setState({ _userSynced: false });
      Promise.resolve(useOnboardingStore.persist.rehydrate()).then(() => {
        useOnboardingStore.setState({ _userSynced: true });
      });
    }
  });
}
