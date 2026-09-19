export type ModelSubTab = "providers" | "roles" | "subscription" | "diagnostics";

type Listener = (tab: ModelSubTab) => void;

const listeners = new Set<Listener>();
let pending: ModelSubTab | null = null;

export function requestModelSubTab(tab: ModelSubTab) {
  pending = tab;
  listeners.forEach((fn) => fn(tab));
}

export function takePendingModelSubTab(): ModelSubTab | null {
  const next = pending;
  pending = null;
  return next;
}

export function subscribeModelSubTab(fn: Listener) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}
