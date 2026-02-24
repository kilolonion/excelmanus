import { useAuthStore, type AuthUser } from "@/stores/auth-store";
import { useRecentAccountsStore } from "@/stores/recent-accounts-store";
import { buildApiUrl } from "./api";

// ── Types ─────────────────────────────────────────────────

interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: {
    id: string;
    email: string;
    display_name: string;
    role: string;
    avatar_url: string | null;
    has_custom_llm_key: boolean;
    created_at: string;
  };
}

function mapUser(raw: TokenResponse["user"]): AuthUser {
  return {
    id: raw.id,
    email: raw.email,
    displayName: raw.display_name,
    role: raw.role,
    avatarUrl: raw.avatar_url,
    hasCustomLlmKey: raw.has_custom_llm_key,
    createdAt: raw.created_at,
  };
}

function handleTokenResponse(data: TokenResponse) {
  const { setTokens, setUser } = useAuthStore.getState();
  setTokens(data.access_token, data.refresh_token);
  const user = mapUser(data.user);
  setUser(user);

  useRecentAccountsStore.getState().recordLogin({
    email: user.email,
    displayName: user.displayName,
    avatarUrl: user.avatarUrl ?? null,
  });
}

// ── Auth API ──────────────────────────────────────────────

export interface RegisterResult {
  /** true when email verification is required before login */
  requires_verification: boolean;
  /** Set when requires_verification = false */
  token?: TokenResponse;
  /** Email address for display in verification step */
  email: string;
}

export async function register(
  email: string,
  password: string,
  displayName?: string,
): Promise<RegisterResult> {
  const res = await fetch(buildApiUrl("/auth/register"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, display_name: displayName || "" }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `注册失败: ${res.status}`);
  }
  const data = await res.json();
  if (data.requires_verification) {
    return { requires_verification: true, email: data.email };
  }
  handleTokenResponse(data as TokenResponse);
  return { requires_verification: false, token: data as TokenResponse, email };
}

export async function verifyEmail(email: string, code: string): Promise<void> {
  const res = await fetch(buildApiUrl("/auth/verify-email"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, code }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `验证失败: ${res.status}`);
  }
  const data: TokenResponse = await res.json();
  handleTokenResponse(data);
}

export async function resendCode(email: string, purpose: "register" | "reset_password" = "register"): Promise<void> {
  const res = await fetch(buildApiUrl("/auth/resend-code"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, purpose }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `发送失败: ${res.status}`);
  }
}

export async function forgotPassword(email: string): Promise<void> {
  const res = await fetch(buildApiUrl("/auth/forgot-password"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `请求失败: ${res.status}`);
  }
}

export async function resetPassword(email: string, code: string, newPassword: string): Promise<void> {
  const res = await fetch(buildApiUrl("/auth/reset-password"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, code, new_password: newPassword }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `重置失败: ${res.status}`);
  }
}

export async function login(email: string, password: string) {
  const res = await fetch(buildApiUrl("/auth/login"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `登录失败: ${res.status}`);
  }
  const data: TokenResponse = await res.json();
  handleTokenResponse(data);
  return data;
}

export async function refreshAccessToken(): Promise<boolean> {
  const { refreshToken, logout } = useAuthStore.getState();
  if (!refreshToken) {
    logout();
    return false;
  }

  try {
    const res = await fetch(buildApiUrl("/auth/refresh"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) {
      logout();
      return false;
    }
    const data: TokenResponse = await res.json();
    handleTokenResponse(data);
    return true;
  } catch {
    logout();
    return false;
  }
}

export async function fetchCurrentUser(): Promise<AuthUser | null> {
  const { accessToken } = useAuthStore.getState();
  if (!accessToken) return null;

  const res = await fetch(buildApiUrl("/auth/me"), {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) return null;

  const data = await res.json();
  const user = mapUser(data);
  useAuthStore.getState().setUser(user);
  return user;
}

export async function updateProfile(updates: {
  display_name?: string;
  avatar_url?: string;
  llm_api_key?: string;
  llm_base_url?: string;
  llm_model?: string;
}): Promise<AuthUser> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/me"), {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify(updates),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `更新失败: ${res.status}`);
  }
  const data = await res.json();
  const user = mapUser(data);
  useAuthStore.getState().setUser(user);
  return user;
}

// ── OAuth helpers ─────────────────────────────────────────

export async function getOAuthUrl(provider: "github" | "google"): Promise<string> {
  const res = await fetch(buildApiUrl(`/auth/oauth/${provider}`));
  if (!res.ok) throw new Error(`OAuth redirect failed: ${res.status}`);
  const data = await res.json();
  return data.authorize_url;
}

export async function handleOAuthCallback(
  provider: "github" | "google",
  code: string,
  state?: string
) {
  const params = new URLSearchParams({ code });
  if (state) params.set("state", state);
  const res = await fetch(buildApiUrl(`/auth/oauth/${provider}/callback?${params}`));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `OAuth 回调失败: ${res.status}`);
  }
  const data: TokenResponse = await res.json();
  handleTokenResponse(data);
  return data;
}

export function logout() {
  useAuthStore.getState().logout();
}

// ── Admin API ─────────────────────────────────────────────

export interface WorkspaceUsage {
  total_bytes: number;
  size_mb: number;
  file_count: number;
  max_size_mb: number;
  max_files: number;
  over_size: boolean;
  over_files: boolean;
  files: { path: string; name: string; size: number; modified_at: number }[];
}

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  role: string;
  avatar_url: string | null;
  has_custom_llm_key: boolean;
  created_at: string;
  is_active: boolean;
  daily_token_limit: number;
  monthly_token_limit: number;
  daily_tokens_used: number;
  monthly_tokens_used: number;
  workspace: WorkspaceUsage;
}

export async function fetchAdminUsers(): Promise<{ users: AdminUser[]; total: number }> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/admin/users"), {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `获取用户列表失败: ${res.status}`);
  }
  return res.json();
}

export async function adminUpdateUser(
  userId: string,
  updates: Record<string, unknown>,
): Promise<void> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl(`/auth/admin/users/${userId}`), {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify(updates),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `更新失败: ${res.status}`);
  }
}

export async function adminClearWorkspace(userId: string): Promise<{ deleted_files: number }> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl(`/auth/admin/users/${userId}/workspace`), {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `清除失败: ${res.status}`);
  }
  return res.json();
}

export async function adminEnforceQuota(userId: string): Promise<{ deleted: string[] }> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl(`/auth/admin/users/${userId}/enforce-quota`), {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `执行失败: ${res.status}`);
  }
  return res.json();
}

export async function fetchMyWorkspaceUsage(): Promise<WorkspaceUsage> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/me/workspace"), {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `获取失败: ${res.status}`);
  }
  return res.json();
}
