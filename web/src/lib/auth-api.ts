import { buildApiUrl } from "./api";

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
  email?: string;
}

async function _json<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error((data as { detail?: string; error?: string }).detail
      || (data as { error?: string }).error
      || `${fallback}: ${res.status}`);
  }
  return res.json();
}

export async function fetchProviders(): Promise<{ providers: ProviderInfo[] }> {
  const res = await fetch(buildApiUrl("/auth/providers"));
  return _json(res, "获取提供商列表失败");
}

export async function codexOAuthStart(redirectUri?: string): Promise<{
  authorize_url: string;
  state: string;
  redirect_uri: string;
  mode: "popup" | "paste";
}> {
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex/oauth/start"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(redirectUri ? { redirect_uri: redirectUri } : {}),
  });
  return _json(res, "发起 OAuth 失败");
}

export async function codexOAuthExchange(code: string, state: string): Promise<{
  status: string;
  provider: string;
  account_id: string;
  plan_type: string;
  expires_at: string;
}> {
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex/oauth/exchange"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, state }),
  });
  return _json(res, "OAuth 交换失败");
}

export async function codexDeviceCodeStart(): Promise<{
  user_code: string;
  verification_url: string;
  interval: number;
  state: string;
  expires_in?: number;
}> {
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex/device-code/start"), {
    method: "POST",
  });
  return _json(res, "发起设备码登录失败");
}

export async function codexDeviceCodePoll(state: string): Promise<{
  status: "pending" | "connected";
  provider?: string;
  account_id?: string;
  plan_type?: string;
  expires_at?: string;
}> {
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex/device-code/poll"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state }),
  });
  return _json(res, "轮询授权状态失败");
}

export async function connectCodex(tokenData: Record<string, unknown>): Promise<{
  status: string;
  provider: string;
  account_id: string;
  plan_type: string;
  expires_at: string;
}> {
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token_data: tokenData }),
  });
  return _json(res, "连接 Codex 失败");
}

export async function disconnectCodex(): Promise<{ status: string }> {
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex"), {
    method: "DELETE",
  });
  return _json(res, "断开连接失败");
}

export async function fetchCodexStatus(): Promise<CodexStatus> {
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex/status"));
  return _json(res, "获取状态失败");
}

export async function refreshCodexToken(): Promise<{
  status: string;
  expires_at: string;
}> {
  const res = await fetch(buildApiUrl("/auth/providers/openai-codex/refresh"), {
    method: "POST",
  });
  return _json(res, "刷新 Token 失败");
}
