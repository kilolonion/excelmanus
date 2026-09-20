import { create } from "zustand";
import { buildDirectHealthUrl } from "@/lib/backend-origin";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { apiFetch, getAuthHeaders } from "@/lib/api";

export type DeployMode = "standalone" | "server";

interface AuthConfigState {
  deployMode: DeployMode;
  checked: boolean;
  authRequired: boolean;
  /** 探测后端是否可达，并同步 deploy_mode / configured。 */
  checkBackendHealth: (force?: boolean) => Promise<boolean>;
}

export const useAuthConfigStore = create<AuthConfigState>((set, get) => ({
  deployMode: "standalone",
  checked: false,
  authRequired: false,

  checkBackendHealth: async (force = false) => {
    if (get().checked && !force) return true;
    try {
      const res = await apiFetch(buildDirectHealthUrl(), { cache: "no-store", headers: getAuthHeaders(), signal: AbortSignal.timeout(10_000) });
      if (res.ok) {
        const data = await res.json();
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
      }
    } catch {
      throw new Error("backend_unreachable");
    }
    throw new Error("backend_unhealthy");
  },
}));
