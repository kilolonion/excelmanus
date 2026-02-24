import { create } from "zustand";
import { buildApiUrl } from "@/lib/api";

interface AuthConfigState {
  authEnabled: boolean | null;
  checked: boolean;
  checkAuthEnabled: () => Promise<boolean>;
}

export const useAuthConfigStore = create<AuthConfigState>((set, get) => ({
  authEnabled: null,
  checked: false,

  checkAuthEnabled: async () => {
    if (get().checked) return get().authEnabled ?? false;
    try {
      const res = await fetch(buildApiUrl("/health"), { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        const enabled = data.auth_enabled === true;
        set({ authEnabled: enabled, checked: true });
        return enabled;
      }
    } catch { /* backend unreachable */ }
    set({ authEnabled: false, checked: true });
    return false;
  },
}));
