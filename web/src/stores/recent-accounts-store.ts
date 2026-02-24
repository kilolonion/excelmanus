import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface RecentAccount {
  email: string;
  displayName: string;
  avatarUrl: string | null;
  lastLoginAt: string;
}

const MAX_RECENT = 5;

interface RecentAccountsState {
  accounts: RecentAccount[];
  /** Call after successful login/register to remember this account. */
  recordLogin: (account: Omit<RecentAccount, "lastLoginAt">) => void;
  /** Remove a specific account from history. */
  removeAccount: (email: string) => void;
  clearAll: () => void;
}

export const useRecentAccountsStore = create<RecentAccountsState>()(
  persist(
    (set) => ({
      accounts: [],

      recordLogin: (account) =>
        set((state) => {
          const filtered = state.accounts.filter(
            (a) => a.email !== account.email,
          );
          const entry: RecentAccount = {
            ...account,
            lastLoginAt: new Date().toISOString(),
          };
          return { accounts: [entry, ...filtered].slice(0, MAX_RECENT) };
        }),

      removeAccount: (email) =>
        set((state) => ({
          accounts: state.accounts.filter((a) => a.email !== email),
        })),

      clearAll: () => set({ accounts: [] }),
    }),
    {
      name: "excelmanus-recent-accounts",
      partialize: (state) => ({ accounts: state.accounts }),
    },
  ),
);
