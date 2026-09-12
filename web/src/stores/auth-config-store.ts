import { create } from "zustand";
import { buildApiUrl } from "@/lib/api";
import { useOnboardingStore } from "@/stores/onboarding-store";

export type DeployMode = "standalone" | "server";

interface AuthConfigState {
  deployMode: DeployMode;
  checked: boolean;
  authRequired: boolean;
  /** 探测后端是否可达，并同步 deploy_mode / configured。 */
  checkBackendHealth: () => Promise<boolean>;
}

export const useAuthConfigStore = create<AuthConfigState>((set, get) => ({
  deployMode: "standalone",
  checked: false,
  authRequired: false,

  checkBackendHealth: async () => {
    if (get().checked) return true;
    try {
      const res = await fetch(buildApiUrl("/health"), { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        const deployMode: DeployMode =
          data.deploy_mode === "server" ? "server" : "standalone";
        const authRequired = Boolean(data.auth_required);
        set({ deployMode, checked: true, authRequired });
        if (typeof data.configured === "boolean") {
          useOnboardingStore.getState().setBackendConfigured(data.configured);
        }
        return true;
      }
    } catch {
      throw new Error("backend_unreachable");
    }
    throw new Error("backend_unhealthy");
  },
}));
