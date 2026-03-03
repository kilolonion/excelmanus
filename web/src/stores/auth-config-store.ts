import { create } from "zustand";
import { buildApiUrl } from "@/lib/api";
import { useOnboardingStore } from "@/stores/onboarding-store";

interface LoginMethods {
  github_enabled: boolean;
  google_enabled: boolean;
  qq_enabled: boolean;
  email_verify_required: boolean;
  require_agreement: boolean;
}

export type DeployMode = "standalone" | "server" | "docker";

interface AuthConfigState {
  authEnabled: boolean | null;
  deployMode: DeployMode;
  loginMethods: LoginMethods;
  checked: boolean;
  checkAuthEnabled: () => Promise<boolean>;
}

const DEFAULT_LOGIN_METHODS: LoginMethods = {
  github_enabled: true,
  google_enabled: true,
  qq_enabled: false,
  email_verify_required: false,
  require_agreement: true,
};

export const useAuthConfigStore = create<AuthConfigState>((set, get) => ({
  authEnabled: null,
  deployMode: "standalone",
  loginMethods: { ...DEFAULT_LOGIN_METHODS },
  checked: false,

  checkAuthEnabled: async () => {
    if (get().checked) return get().authEnabled ?? false;
    try {
      const res = await fetch(buildApiUrl("/health"), { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        const enabled = data.auth_enabled === true;
        const lm = data.login_methods;
        const loginMethods: LoginMethods = lm
          ? {
              github_enabled: lm.github_enabled ?? true,
              google_enabled: lm.google_enabled ?? true,
              qq_enabled: lm.qq_enabled ?? false,
              email_verify_required: lm.email_verify_required ?? false,
              require_agreement: lm.require_agreement ?? true,
            }
          : { ...DEFAULT_LOGIN_METHODS };
        const deployMode: DeployMode =
          data.deploy_mode === "server" ? "server"
            : data.deploy_mode === "docker" ? "docker"
              : "standalone";
        set({ authEnabled: enabled, deployMode, loginMethods, checked: true });
        // Propagate backend config status to onboarding store
        if (typeof data.configured === "boolean") {
          useOnboardingStore.getState().setBackendConfigured(data.configured);
        }
        return enabled;
      }
    } catch {
      // 后端不可达 — 不标记 checked，让调用方可以重试
      throw new Error("backend_unreachable");
    }
    // 非 200 响应（后端可达但异常）— 也视为不可达
    throw new Error("backend_unhealthy");
  },
}));
