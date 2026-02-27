import { create } from "zustand";
import { buildApiUrl } from "@/lib/api";

interface LoginMethods {
  github_enabled: boolean;
  google_enabled: boolean;
  email_verify_required: boolean;
}

interface AuthConfigState {
  authEnabled: boolean | null;
  loginMethods: LoginMethods;
  checked: boolean;
  checkAuthEnabled: () => Promise<boolean>;
}

const DEFAULT_LOGIN_METHODS: LoginMethods = {
  github_enabled: true,
  google_enabled: true,
  email_verify_required: false,
};

export const useAuthConfigStore = create<AuthConfigState>((set, get) => ({
  authEnabled: null,
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
              email_verify_required: lm.email_verify_required ?? false,
            }
          : { ...DEFAULT_LOGIN_METHODS };
        set({ authEnabled: enabled, loginMethods, checked: true });
        return enabled;
      }
    } catch { /* 后端不可达 */ }
    set({ authEnabled: false, checked: true });
    return false;
  },
}));
