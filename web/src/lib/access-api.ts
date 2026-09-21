import { apiGet, apiPost, apiPut, setManageToken } from "./api";

export interface AccessStatus {
  auth_required: boolean;
  authenticated: boolean;
  login_method: "password" | "token" | "none";
  username: string | null;
}

export interface AccessSettings {
  enabled: boolean;
  username: string;
  password_configured: boolean;
  manage_token_configured: boolean;
  session_hours: number;
}

export function fetchAccessStatus(options?: { signal?: AbortSignal; timeoutMs?: number }): Promise<AccessStatus> {
  return apiGet("/auth/status", { ...options, direct: true, cache: "no-store" });
}

export async function loginToInstance(username: string, password: string): Promise<AccessStatus> {
  await apiPost("/auth/login", { username, password }, { direct: true });
  setManageToken("");
  // Do not render the workspace until the browser actually returns the cookie.
  const status = await fetchAccessStatus();
  if (!status.authenticated) throw new Error("登录会话未保存，请检查浏览器 Cookie 设置或使用同源访问地址。");
  return status;
}

export async function logoutFromInstance(): Promise<void> {
  await apiPost("/auth/logout", {}, { direct: true });
  setManageToken("");
  // Discard mounted workspace state, streams and cached private UI in this tab.
  window.location.reload();
}

export function fetchAccessSettings(): Promise<AccessSettings> {
  return apiGet("/auth/settings");
}

export function saveAccessSettings(settings: { enabled: boolean; username: string; password: string }): Promise<AccessSettings> {
  return apiPut("/auth/settings", settings);
}
