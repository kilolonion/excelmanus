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

export async function register(email: string, password: string, displayName?: string) {
  const res = await fetch(buildApiUrl("/auth/register"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, display_name: displayName || "" }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `注册失败: ${res.status}`);
  }
  const data: TokenResponse = await res.json();
  handleTokenResponse(data);
  return data;
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
