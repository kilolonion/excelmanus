import { beforeEach, describe, expect, it, vi } from "vitest";

const apiPut = vi.fn().mockResolvedValue({});

vi.mock("@/lib/api", () => ({
  apiPut: (...args: unknown[]) => apiPut(...args),
}));

const { useOnboardingStore } = await import("@/stores/onboarding-store");
const { LEGACY_ONBOARDING_STORAGE_KEY } = await import("@/stores/onboarding-state");

describe("onboarding store server hydrate", () => {
  beforeEach(() => {
    apiPut.mockClear();
    useOnboardingStore.setState({
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
    });
  });

  it("hydrates from health and marks the store synced", () => {
    useOnboardingStore.getState().applyServerState(
      { wizard_completed: true, coach_marks_completed: true, coach_phase: "done" },
      true,
    );
    const state = useOnboardingStore.getState();
    expect(state._userSynced).toBe(true);
    expect(state.wizardCompleted).toBe(true);
    expect(state.coachMarksCompleted).toBe(true);
    expect(state.coachPhase).toBe("done");
    expect(apiPut).not.toHaveBeenCalled();
  });

  it("writes through completeWizard to /onboarding", () => {
    useOnboardingStore.getState().completeWizard();
    expect(useOnboardingStore.getState().wizardCompleted).toBe(true);
    expect(apiPut).toHaveBeenCalledWith(
      "/onboarding",
      expect.objectContaining({ wizard_completed: true }),
    );
  });
});

describe("legacy localStorage promotion", () => {
  const memory = new Map<string, string>();

  beforeEach(() => {
    apiPut.mockClear();
    memory.clear();
    vi.stubGlobal("window", {
      localStorage: {
        getItem: (key: string) => memory.get(key) ?? null,
        setItem: (key: string, value: string) => {
          memory.set(key, value);
        },
        removeItem: (key: string) => {
          memory.delete(key);
        },
      },
    });
    useOnboardingStore.setState({
      wizardCompleted: false,
      coachMarksCompleted: false,
      advancedGuideCompleted: false,
      settingsGuideCompleted: false,
      skippedAt: null,
      coachPhase: "basic",
      coachStepIndex: 0,
      _userSynced: false,
    });
  });

  it("uploads completed local flags and keeps them", () => {
    memory.set(
      LEGACY_ONBOARDING_STORAGE_KEY,
      JSON.stringify({
        state: { wizardCompleted: true, coachMarksCompleted: true, coachPhase: "done" },
        version: 0,
      }),
    );
    useOnboardingStore.getState().applyServerState(
      { wizard_completed: true, coach_marks_completed: false },
      true,
    );
    expect(useOnboardingStore.getState().coachMarksCompleted).toBe(true);
    expect(apiPut).toHaveBeenCalledWith(
      "/onboarding",
      expect.objectContaining({
        wizard_completed: true,
        coach_marks_completed: true,
      }),
    );
  });
});
