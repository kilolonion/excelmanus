import { describe, expect, it } from "vitest";
import {
  defaultOnboardingSnapshot,
  mergeOnboardingSnapshots,
  parseLegacyLocalOnboarding,
  shouldShowCoachMarks,
  shouldShowOnboardingWizard,
  snapshotFromServer,
  snapshotToPayload,
} from "@/stores/onboarding-state";

describe("snapshotFromServer", () => {
  it("infers wizard completed when backend is configured and payload is missing", () => {
    expect(snapshotFromServer(undefined, true).wizardCompleted).toBe(true);
    expect(snapshotFromServer(undefined, true).coachMarksCompleted).toBe(false);
    expect(snapshotFromServer(undefined, false)).toEqual(defaultOnboardingSnapshot());
  });

  it("reads snake_case server payload without forcing wizard complete", () => {
    const snap = snapshotFromServer(
      { wizard_completed: false, coach_phase: "advanced", coach_step_index: 3 },
      true,
    );
    expect(snap.wizardCompleted).toBe(false);
    expect(snap.coachPhase).toBe("advanced");
    expect(snap.coachStepIndex).toBe(3);
  });
});

describe("legacy localStorage merge", () => {
  it("parses zustand persist envelope and unions completed flags", () => {
    const local = parseLegacyLocalOnboarding(
      JSON.stringify({
        state: {
          wizardCompleted: true,
          coachMarksCompleted: true,
          coachPhase: "settings",
          coachStepIndex: 1,
        },
        version: 0,
      }),
    );
    expect(local?.wizardCompleted).toBe(true);
    const merged = mergeOnboardingSnapshots(snapshotFromServer(undefined, true), local);
    expect(merged.wizardCompleted).toBe(true);
    expect(merged.coachMarksCompleted).toBe(true);
    expect(merged.coachPhase).toBe("settings");
  });
});

describe("visibility", () => {
  it("hides wizard on a new browser when server already completed it", () => {
    expect(shouldShowOnboardingWizard(true, true)).toBe(false);
  });

  it("respects a completed or skipped wizard even without model config", () => {
    expect(shouldShowOnboardingWizard(true, true)).toBe(false);
    expect(shouldShowCoachMarks(true, true, false, false, false)).toBe(true);
    expect(shouldShowCoachMarks(true, true, true, true, true)).toBe(false);
  });

  it("waits until health hydrate before overlay", () => {
    expect(shouldShowOnboardingWizard(false, false)).toBe(false);
    expect(shouldShowCoachMarks(false, true, false, false, false)).toBe(false);
  });

  it("keeps replay working when wizardCompleted is reset", () => {
    expect(shouldShowOnboardingWizard(true, false)).toBe(true);
  });
});

describe("snapshotToPayload", () => {
  it("round-trips camelCase to API snake_case", () => {
    expect(snapshotToPayload(defaultOnboardingSnapshot()).wizard_completed).toBe(false);
    expect(snapshotToPayload({
      ...defaultOnboardingSnapshot(),
      wizardCompleted: true,
      skippedAt: "2026-09-12T00:00:00Z",
    }).skipped_at).toBe("2026-09-12T00:00:00Z");
  });
});
