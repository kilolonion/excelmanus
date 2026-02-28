import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface RecentAccount {
  email: string;
  displayName: string;
  avatarUrl: string | null;
  lastLoginAt: string;
  // 自动登录相关
  savedPassword?: string; // 加密的密码
  autoLoginExpiresAt?: string; // 自动登录过期时间
}

// 自动登录有效期：7天
const AUTO_LOGIN_DAYS = 7;

const MAX_RECENT = 5;

interface RecentAccountsState {
  accounts: RecentAccount[];
  /** Call after successful login/register to remember this account. */
  recordLogin: (account: Omit<RecentAccount, "lastLoginAt" | "savedPassword" | "autoLoginExpiresAt"> & { password?: string; rememberMe?: boolean }) => void;
  /** Update saved password for an account */
  updateSavedPassword: (email: string, password: string, rememberMe: boolean) => void;
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
          const existing = state.accounts.find(
            (a) => a.email === account.email,
          );
          const filtered = state.accounts.filter(
            (a) => a.email !== account.email,
          );
          // 如果显式传了 rememberMe，按其值决定是否保存密码；
          // 如果未传（如 handleTokenResponse 仅更新头像/昵称），保留已有的密码信息。
          const hasExplicitRememberMe = account.rememberMe !== undefined;
          const entry: RecentAccount = {
            email: account.email,
            displayName: account.displayName,
            avatarUrl: account.avatarUrl,
            lastLoginAt: new Date().toISOString(),
            savedPassword: hasExplicitRememberMe
              ? (account.rememberMe ? account.password : undefined)
              : existing?.savedPassword,
            autoLoginExpiresAt: hasExplicitRememberMe
              ? (account.rememberMe
                  ? new Date(Date.now() + AUTO_LOGIN_DAYS * 24 * 60 * 60 * 1000).toISOString()
                  : undefined)
              : existing?.autoLoginExpiresAt,
          };
          return { accounts: [entry, ...filtered].slice(0, MAX_RECENT) };
        }),

      updateSavedPassword: (email, password, rememberMe) =>
        set((state) => ({
          accounts: state.accounts.map((a) =>
            a.email === email
              ? {
                  ...a,
                  savedPassword: rememberMe ? password : undefined,
                  autoLoginExpiresAt: rememberMe
                    ? new Date(Date.now() + AUTO_LOGIN_DAYS * 24 * 60 * 60 * 1000).toISOString()
                    : undefined,
                }
              : a
          ),
        })),

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

/**
 * 检查账号是否可以使用自动登录（密码未过期）
 */
export function canAutoLogin(account: RecentAccount): boolean {
  if (!account.savedPassword || !account.autoLoginExpiresAt) return false;
  const expiresAt = new Date(account.autoLoginExpiresAt).getTime();
  return Date.now() < expiresAt;
}
