import { create } from "zustand";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { apiGet } from "@/lib/api";

export type DeployMode = "standalone" | "server";

interface AuthConfigState {
  deployMode: DeployMode;
  checked: boolean;
  authRequired: boolean;
  /** 探测后端是否可达，并同步 deploy_mode / configured。 */
  checkBackendHealth: (force?: boolean) => Promise<boolean>;
}

let healthRequest: Promise<boolean> | null = null;
let healthGeneration = 0;

export const useAuthConfigStore = create<AuthConfigState>((set, get) => ({
  deployMode: "standalone",
  checked: false,
  authRequired: false,

  checkBackendHealth: async (force = false) => {
    if (get().checked && !force) return true;
    if (healthRequest && !force) return healthRequest;

    const generation = ++healthGeneration;
    const request = (async () => {
      try {
        const data = await apiGet<{
          status?: string;
          deploy_mode?: string;
          auth_required?: boolean;
          authenticated?: boolean;
          configured?: boolean;
          onboarding?: unknown;
        }>("/health", {
          direct: true,
          timeoutMs: 10_000,
          cache: "no-store",
        });
        if (data.status === "draining") {
          throw new Error("backend_draining");
        }

        // A forced probe may supersede the initial probe. Do not let the late
        // initial response roll the auth/config state back to an older view.
        if (generation !== healthGeneration) return true;
        const deployMode: DeployMode =
          data.deploy_mode === "server" ? "server" : "standalone";
        const authRequired = Boolean(data.auth_required);
        set({ deployMode, checked: true, authRequired });
        if (data.authenticated !== false && (data.status === "ok" || data.status == null)) {
          const configured = data.configured === true;
          const onboarding = useOnboardingStore.getState();
          onboarding.applyServerState(data.onboarding, configured);
          if (typeof data.configured === "boolean") {
            onboarding.setBackendConfigured(data.configured);
          }
        }
        return true;
      } catch {
        throw new Error("backend_unreachable");
      }
    })();
    if (!force) healthRequest = request;
    try {
      return await request;
    } finally {
      if (healthRequest === request) healthRequest = null;
    }
  },
}));
