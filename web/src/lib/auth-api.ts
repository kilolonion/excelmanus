import { useAuthStore, type AuthUser } from "@/stores/auth-store";
import { useRecentAccountsStore } from "@/stores/recent-accounts-store";
import { useSessionStore } from "@/stores/session-store";
import { useChatStore } from "@/stores/chat-store";
import { useExcelStore } from "@/stores/excel-store";
import { clearAllCachedMessages } from "@/lib/idb-cache";
import { buildApiUrl } from "./api";

// ── Session cookie（配合 Next.js middleware 服务端路由保护）──
const SESSION_COOKIE = "em-session";
const SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 30; // 30 天

function _setSessionCookie() {
  if (typeof document === "undefined") return;
  document.cookie = `${SESSION_COOKIE}=1; path=/; max-age=${SESSION_COOKIE_MAX_AGE}; samesite=lax`;
}

function _clearSessionCookie() {
  if (typeof document === "undefined") return;
  document.cookie = `${SESSION_COOKIE}=; path=/; max-age=0`;
}

/** 供外部（如 AuthProvider）在登出时清除会话 cookie。 */
export { _clearSessionCookie as clearSessionCookie };

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
    has_password: boolean;
    allowed_models: string[];
    oauth_providers: string[];
    created_at: string;
  };
}

export interface MergeRequiredInfo {
  merge_required: true;
  merge_token: string;
  existing_email: string;
  existing_display_name: string;
  existing_providers: string[];
  existing_has_password: boolean;
  new_provider: string;
  new_provider_display_name: string;
  new_provider_avatar_url: string | null;
}

export interface OAuthLinkInfo {
  provider: string;
  display_name: string | null;
  avatar_url: string | null;
  linked_at: string;
}

function mapUser(raw: TokenResponse["user"]): AuthUser {
  return {
    id: raw.id,
    email: raw.email,
    displayName: raw.display_name,
    role: raw.role,
    avatarUrl: raw.avatar_url,
    hasCustomLlmKey: raw.has_custom_llm_key,
    hasPassword: raw.has_password ?? true,
    allowedModels: raw.allowed_models ?? [],
    oauthProviders: raw.oauth_providers ?? [],
    createdAt: raw.created_at,
  };
}

function handleTokenResponse(data: TokenResponse) {
  const prevUser = useAuthStore.getState().user;
  const { setTokens, setUser } = useAuthStore.getState();
  setTokens(data.access_token, data.refresh_token);
  const user = mapUser(data.user);
  setUser(user);

  // 切换到不同账号时，清理前一个用户的本地缓存数据，防止跨账号污染。
  // refreshAccessToken 刷新同一用户的 token 时不触发清理。
  if (prevUser && prevUser.id !== user.id) {
    _clearUserSpecificStores();
  }

  useRecentAccountsStore.getState().recordLogin({
    email: user.email,
    displayName: user.displayName,
    avatarUrl: user.avatarUrl ?? null,
  });

  // 设置会话 cookie 供 middleware 服务端拦截使用
  _setSessionCookie();
}

/**
 * 清理所有用户特定的前端缓存，防止跨账号数据泄漏。
 * 在 logout() 和账号切换时调用。
 */
function _clearUserSpecificStores() {
  useSessionStore.getState().setSessions([]);
  useSessionStore.getState().setActiveSession(null);
  useChatStore.getState().switchSession(null);
  clearAllCachedMessages().catch(() => {});
  useExcelStore.getState().clearAllRecentFiles();
  useExcelStore.getState().clearSession();
  useExcelStore.setState({ dismissedPaths: new Set<string>() });
}

// ── JWT local helpers ─────────────────────────────────────

/**
 * Decode JWT payload without verification (client-side only).
 * Returns null if the token is malformed.
 */
function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const payload = atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(payload);
  } catch {
    return null;
  }
}

/**
 * Check whether a JWT access token is expired locally.
 * Uses a 60-second safety margin so we refresh slightly before actual expiry.
 * Returns `true` (= treat as expired) when the token is null / malformed / expired.
 */
export function isTokenExpired(token: string | null): boolean {
  if (!token) return true;
  const payload = decodeJwtPayload(token);
  if (!payload || typeof payload.exp !== "number") return true;
  const nowSec = Math.floor(Date.now() / 1000);
  return nowSec >= payload.exp - 60;
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
  // 密码已重置，清除旧的保存密码以避免自动登录失败
  useRecentAccountsStore.getState().updateSavedPassword(email, "", false);
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
    _clearSessionCookie();
    return false;
  }

  try {
    const res = await fetch(buildApiUrl("/auth/refresh"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) {
      // 服务端明确拒绝（401/403 等）→ token 已失效，执行登出
      logout();
      _clearSessionCookie();
      return false;
    }
    const data: TokenResponse = await res.json();
    handleTokenResponse(data);
    return true;
  } catch (err) {
    // 网络错误（Failed to fetch / DNS / timeout）→ 不登出，仅返回 false，
    // 允许后续请求重试。避免临时网络波动把用户踢出登录。
    console.warn("[auth] token refresh network error, will retry later:", err);
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

export async function getOAuthUrl(provider: "github" | "google" | "qq"): Promise<string> {
  const res = await fetch(buildApiUrl(`/auth/oauth/${provider}`));
  if (!res.ok) throw new Error(`OAuth redirect failed: ${res.status}`);
  const data = await res.json();
  return data.authorize_url;
}

export async function handleOAuthCallback(
  provider: "github" | "google" | "qq",
  code: string,
  state?: string
): Promise<TokenResponse | MergeRequiredInfo> {
  const params = new URLSearchParams({ code });
  if (state) params.set("state", state);
  // 直连后端交换 token，绕过 CDN→Nginx→Next.js rewrite 代理链
  const res = await fetch(buildApiUrl(`/auth/oauth/${provider}/callback?${params}`, { direct: true }), {
    headers: { "Accept": "application/json" },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `OAuth 回调失败: ${res.status}`);
  }
  const data = await res.json();
  // 检测合并场景
  if (data.merge_required) {
    return data as MergeRequiredInfo;
  }
  handleTokenResponse(data as TokenResponse);
  return data as TokenResponse;
}

/**
 * 确认 OAuth 账号合并：将新的 OAuth 登录绑定到已有账号。
 */
export async function confirmAccountMerge(mergeToken: string): Promise<void> {
  const res = await fetch(buildApiUrl("/auth/oauth/confirm-merge", { direct: true }), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ merge_token: mergeToken }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `合并失败: ${res.status}`);
  }
  const data: TokenResponse = await res.json();
  handleTokenResponse(data);
}

/**
 * 获取当前用户已绑定的 OAuth 登录方式。
 */
export async function fetchOAuthLinks(): Promise<{ links: OAuthLinkInfo[]; has_password: boolean }> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/me/oauth-links"), {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `获取绑定列表失败: ${res.status}`);
  }
  return res.json();
}

/**
 * 解绑某个 OAuth 登录方式。
 */
export async function unlinkOAuth(provider: string): Promise<{ links: OAuthLinkInfo[] }> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl(`/auth/me/oauth-links/${provider}`), {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `解绑失败: ${res.status}`);
  }
  return res.json();
}

export function logout() {
  useAuthStore.getState().logout();
  _clearUserSpecificStores();
  // 清除会话 cookie，使 middleware 在下次请求时重定向到登录页
  _clearSessionCookie();
  // 阻止登录页自动登录 —— 用户主动退出不应立刻被自动登录回去
  if (typeof window !== "undefined") {
    sessionStorage.setItem("suppress-auto-login", "1");
  }
}

// ── Set password (OAuth users) ────────────────────────────

export async function setPassword(newPassword: string): Promise<AuthUser> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/me/set-password"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ new_password: newPassword }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `设置密码失败: ${res.status}`);
  }
  const data = await res.json();
  const user = mapUser(data.user);
  useAuthStore.getState().setUser(user);
  return user;
}

// ── Profile API (change password, email, avatar) ──────────

export async function changePassword(oldPassword: string, newPassword: string): Promise<AuthUser> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/me/change-password"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `修改密码失败: ${res.status}`);
  }
  const data = await res.json();
  const user = mapUser(data.user);
  useAuthStore.getState().setUser(user);
  // 密码已变更，清除旧的保存密码以避免自动登录失败
  useRecentAccountsStore.getState().updateSavedPassword(user.email, "", false);
  return user;
}

export async function changeEmail(newEmail: string, password: string): Promise<AuthUser> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/me/change-email"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ new_email: newEmail, password }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `修改邮箱失败: ${res.status}`);
  }
  const data = await res.json();
  const user = mapUser(data.user);
  useAuthStore.getState().setUser(user);
  return user;
}

export async function uploadAvatar(file: File): Promise<AuthUser> {
  const { accessToken } = useAuthStore.getState();
  const formData = new FormData();
  formData.append("avatar", file);
  const res = await fetch(buildApiUrl("/auth/me/avatar"), {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
    },
    body: formData,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `上传头像失败: ${res.status}`);
  }
  const data = await res.json();
  const user = mapUser(data.user);
  useAuthStore.getState().setUser(user);
  return user;
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

export interface AdminModelUsage {
  model: string;
  display_name: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  last_used_at: string;
}

export interface AdminProviderUsage {
  provider: string;
  display_name: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  models: AdminModelUsage[];
}

export interface AdminLlmUsage {
  total_calls: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  providers: AdminProviderUsage[];
}

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  role: string;
  avatar_url: string | null;
  has_custom_llm_key: boolean;
  allowed_models: string[];
  created_at: string;
  is_active: boolean;
  daily_token_limit: number;
  monthly_token_limit: number;
  daily_tokens_used: number;
  monthly_tokens_used: number;
  max_storage_mb: number;
  max_files: number;
  workspace: WorkspaceUsage;
  llm_usage?: AdminLlmUsage;
}

export interface AdminSession {
  id: string;
  title: string;
  message_count: number;
  status: string;
  updated_at: string;
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

export async function adminDeleteUser(
  userId: string,
): Promise<{ deleted_files: number; deleted_sessions: number }> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl(`/auth/admin/users/${userId}`), {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `删除失败: ${res.status}`);
  }
  return res.json();
}

export async function adminListUserSessions(
  userId: string,
): Promise<{ sessions: AdminSession[]; total: number }> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl(`/auth/admin/users/${userId}/sessions`), {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `获取会话列表失败: ${res.status}`);
  }
  return res.json();
}

export async function adminDeleteUserSessions(
  userId: string,
): Promise<{ deleted_sessions: number }> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl(`/auth/admin/users/${userId}/sessions`), {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `删除会话失败: ${res.status}`);
  }
  return res.json();
}

export async function adminDeleteUserSession(
  userId: string,
  sessionId: string,
): Promise<void> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(
    buildApiUrl(`/auth/admin/users/${userId}/sessions/${sessionId}`),
    {
      method: "DELETE",
      headers: { Authorization: `Bearer ${accessToken}` },
    },
  );
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `删除会话失败: ${res.status}`);
  }
}

// ── Admin Login Config API ────────────────────────────────

export interface LoginConfig {
  login_github_enabled: boolean;
  login_google_enabled: boolean;
  login_qq_enabled: boolean;
  email_verify_required: boolean;
  require_agreement: boolean;
  // GitHub OAuth
  github_client_id: string;
  github_client_secret: string;
  github_redirect_uri: string;
  // Google OAuth
  google_client_id: string;
  google_client_secret: string;
  google_redirect_uri: string;
  // QQ OAuth
  qq_client_id: string;
  qq_client_secret: string;
  qq_redirect_uri: string;
  // Email
  email_resend_api_key: string;
  email_smtp_host: string;
  email_smtp_port: string;
  email_smtp_user: string;
  email_smtp_password: string;
  email_from: string;
}

export async function fetchLoginConfig(): Promise<LoginConfig> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/admin/login-config"), {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `获取登录配置失败: ${res.status}`);
  }
  return res.json();
}

export async function updateLoginConfig(
  updates: Partial<LoginConfig>,
): Promise<LoginConfig> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/admin/login-config"), {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify(updates),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `更新登录配置失败: ${res.status}`);
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

// ── 订阅提供商管理 ─────────────────────────────────────────

export interface ProviderInfo {
  provider: string;
  profile_name: string;
  credential_type: string;
  account_id: string | null;
  plan_type: string | null;
  expires_at: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface CodexStatus {
  status: "connected" | "disconnected" | "expired";
  provider: string;
  account_id?: string;
  plan_type?: string;
  expires_at?: string;
  is_active?: boolean;
  access_token_preview?: string;
  has_refresh_token?: boolean;
}

export async function fetchProviders(): Promise<{ providers: ProviderInfo[] }> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/providers"), {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `获取提供商列表失败: ${res.status}`);
  }
  return res.json();
}

// ── Codex OAuth PKCE Browser Flow ─────────────────────────

export async function codexOAuthStart(redirectUri?: string): Promise<{
  authorize_url: string;
  state: string;
  redirect_uri: string;
  mode: "popup" | "paste";
}> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex/oauth/start"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify(redirectUri ? { redirect_uri: redirectUri } : {}),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `发起 OAuth 失败: ${res.status}`);
  }
  return res.json();
}

export async function codexOAuthExchange(code: string, state: string): Promise<{
  status: string;
  provider: string;
  account_id: string;
  plan_type: string;
  expires_at: string;
}> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex/oauth/exchange"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ code, state }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `OAuth 交换失败: ${res.status}`);
  }
  return res.json();
}

export async function codexDeviceCodeStart(): Promise<{
  user_code: string;
  verification_url: string;
  interval: number;
  state: string;
}> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex/device-code/start"), {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `发起设备码登录失败: ${res.status}`);
  }
  return res.json();
}

export async function codexDeviceCodePoll(state: string): Promise<{
  status: "pending" | "connected";
  provider?: string;
  account_id?: string;
  plan_type?: string;
  expires_at?: string;
}> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex/device-code/poll"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ state }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `轮询授权状态失败: ${res.status}`);
  }
  return res.json();
}

export async function connectCodex(tokenData: Record<string, unknown>): Promise<{
  status: string;
  provider: string;
  account_id: string;
  plan_type: string;
  expires_at: string;
}> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ token_data: tokenData }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `连接 Codex 失败: ${res.status}`);
  }
  return res.json();
}

export async function disconnectCodex(): Promise<{ status: string }> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex"), {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `断开连接失败: ${res.status}`);
  }
  return res.json();
}

export async function fetchCodexStatus(): Promise<CodexStatus> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex/status"), {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `获取状态失败: ${res.status}`);
  }
  return res.json();
}

export async function refreshCodexToken(): Promise<{
  status: string;
  expires_at: string;
}> {
  const { accessToken } = useAuthStore.getState();
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex/refresh"), {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `刷新 Token 失败: ${res.status}`);
  }
  return res.json();
}
