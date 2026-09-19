export type CoachPhase =
  | "basic"
  | "transition"
  | "advanced"
  | "settingsTransition"
  | "settings"
  | "done";

export const LEGACY_ONBOARDING_STORAGE_KEY = "excelmanus-onboarding";

const VALID_PHASES = new Set<CoachPhase>([
  "basic",
  "transition",
  "advanced",
  "settingsTransition",
  "settings",
  "done",
]);

export interface OnboardingSnapshot {
  wizardCompleted: boolean;
  coachMarksCompleted: boolean;
  advancedGuideCompleted: boolean;
  settingsGuideCompleted: boolean;
  skippedAt: string | null;
  coachPhase: CoachPhase;
  coachStepIndex: number;
}

export interface OnboardingPayload {
  wizard_completed: boolean;
  coach_marks_completed: boolean;
  advanced_guide_completed: boolean;
  settings_guide_completed: boolean;
  skipped_at: string | null;
  coach_phase: CoachPhase;
  coach_step_index: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function asBool(value: unknown): boolean {
  return value === true;
}

function asPhase(value: unknown): CoachPhase {
  return typeof value === "string" && VALID_PHASES.has(value as CoachPhase)
    ? (value as CoachPhase)
    : "basic";
}

function asIndex(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.floor(n));
}

function asSkippedAt(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export function defaultOnboardingSnapshot(): OnboardingSnapshot {
  return {
    wizardCompleted: false,
    coachMarksCompleted: false,
    advancedGuideCompleted: false,
    settingsGuideCompleted: false,
    skippedAt: null,
    coachPhase: "basic",
    coachStepIndex: 0,
  };
}

function fromRecord(src: Record<string, unknown>): OnboardingSnapshot {
  return {
    wizardCompleted: asBool(src.wizardCompleted) || asBool(src.wizard_completed),
    coachMarksCompleted: asBool(src.coachMarksCompleted) || asBool(src.coach_marks_completed),
    advancedGuideCompleted:
      asBool(src.advancedGuideCompleted) || asBool(src.advanced_guide_completed),
    settingsGuideCompleted:
      asBool(src.settingsGuideCompleted) || asBool(src.settings_guide_completed),
    skippedAt: asSkippedAt(src.skippedAt ?? src.skipped_at),
    coachPhase: asPhase(src.coachPhase ?? src.coach_phase),
    coachStepIndex: asIndex(src.coachStepIndex ?? src.coach_step_index),
  };
}

/** 服务端缺失 onboarding 字段时：已配置则跳过密钥向导。 */
export function snapshotFromServer(raw: unknown, configured: boolean): OnboardingSnapshot {
  if (!isRecord(raw)) {
    return {
      ...defaultOnboardingSnapshot(),
      wizardCompleted: configured,
    };
  }
  return fromRecord(raw);
}

export function snapshotToPayload(snapshot: OnboardingSnapshot): OnboardingPayload {
  return {
    wizard_completed: snapshot.wizardCompleted,
    coach_marks_completed: snapshot.coachMarksCompleted,
    advanced_guide_completed: snapshot.advancedGuideCompleted,
    settings_guide_completed: snapshot.settingsGuideCompleted,
    skipped_at: snapshot.skippedAt,
    coach_phase: snapshot.coachPhase,
    coach_step_index: snapshot.coachStepIndex,
  };
}

function completionRank(snapshot: OnboardingSnapshot): number {
  return (
    Number(snapshot.wizardCompleted) +
    Number(snapshot.coachMarksCompleted) +
    Number(snapshot.advancedGuideCompleted) +
    Number(snapshot.settingsGuideCompleted)
  );
}

export function mergeOnboardingSnapshots(
  server: OnboardingSnapshot,
  local: OnboardingSnapshot | null,
): OnboardingSnapshot {
  if (!local) return server;
  const richer = completionRank(local) > completionRank(server) ? local : server;
  return {
    wizardCompleted: server.wizardCompleted || local.wizardCompleted,
    coachMarksCompleted: server.coachMarksCompleted || local.coachMarksCompleted,
    advancedGuideCompleted: server.advancedGuideCompleted || local.advancedGuideCompleted,
    settingsGuideCompleted: server.settingsGuideCompleted || local.settingsGuideCompleted,
    skippedAt: server.skippedAt || local.skippedAt,
    coachPhase: richer.coachPhase,
    coachStepIndex: richer.coachStepIndex,
  };
}

export function snapshotsEqual(a: OnboardingSnapshot, b: OnboardingSnapshot): boolean {
  return (
    a.wizardCompleted === b.wizardCompleted &&
    a.coachMarksCompleted === b.coachMarksCompleted &&
    a.advancedGuideCompleted === b.advancedGuideCompleted &&
    a.settingsGuideCompleted === b.settingsGuideCompleted &&
    a.skippedAt === b.skippedAt &&
    a.coachPhase === b.coachPhase &&
    a.coachStepIndex === b.coachStepIndex
  );
}

export function parseLegacyLocalOnboarding(raw: string | null): OnboardingSnapshot | null {
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed)) return null;
    const state = isRecord(parsed.state) ? parsed.state : parsed;
    const snapshot = fromRecord(state);
    if (completionRank(snapshot) === 0 && snapshot.coachStepIndex === 0 && !snapshot.skippedAt) {
      return null;
    }
    return snapshot;
  } catch {
    return null;
  }
}

export function readLegacyLocalOnboarding(): OnboardingSnapshot | null {
  if (typeof window === "undefined") return null;
  try {
    return parseLegacyLocalOnboarding(
      window.localStorage.getItem(LEGACY_ONBOARDING_STORAGE_KEY),
    );
  } catch {
    return null;
  }
}

export function clearLegacyLocalOnboarding(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(LEGACY_ONBOARDING_STORAGE_KEY);
  } catch {
    /* private mode / quota */
  }
}

export function shouldShowOnboardingWizard(
  userSynced: boolean,
  wizardCompleted: boolean,
  backendConfigured: boolean | null,
): boolean {
  return userSynced && (!wizardCompleted || backendConfigured === false);
}

export function shouldShowCoachMarks(
  userSynced: boolean,
  wizardCompleted: boolean,
  backendConfigured: boolean | null,
  coachMarksCompleted: boolean,
  advancedGuideCompleted: boolean,
  settingsGuideCompleted: boolean,
): boolean {
  return (
    userSynced &&
    wizardCompleted &&
    backendConfigured !== false &&
    (!coachMarksCompleted || !advancedGuideCompleted || !settingsGuideCompleted)
  );
}
