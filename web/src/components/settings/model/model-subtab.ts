/** diagnostics is kept as a compatibility alias; the UI routes it to roles. */
export type ModelSubTab = "providers" | "roles" | "subscription" | "diagnostics";

type Listener = (tab: ModelSubTab) => void;

const listeners = new Set<Listener>();
let pending: ModelSubTab | null = null;
let pendingProfile: string | null = null;
let pendingNewModelSource: string | null = null;

export function requestModelSubTab(tab: ModelSubTab, profileName?: string) {
  pending = listeners.size === 0 ? tab : null;
  pendingProfile = profileName ?? null;
  pendingNewModelSource = null;
  listeners.forEach((fn) => fn(tab));
}

/** Open model configuration and start adding a model using an existing connection. */
export function requestNewModelConfig(sourceProfileName: string) {
  requestModelSubTab("roles");
  pendingNewModelSource = sourceProfileName;
}

export function takePendingModelSubTab(): ModelSubTab | null {
  const next = pending;
  pending = null;
  return next;
}

export function takePendingModelProfile(): string | null {
  const next = pendingProfile;
  pendingProfile = null;
  return next;
}

export function takePendingNewModelSource(): string | null {
  const next = pendingNewModelSource;
  pendingNewModelSource = null;
  return next;
}

export function subscribeModelSubTab(fn: Listener) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}
