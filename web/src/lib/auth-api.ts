import { apiDelete, apiGet, apiPost } from "./api";

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

export async function fetchProviders(): Promise<{ providers: ProviderInfo[] }> {
  return apiGet("/auth/providers");
}

export async function codexOAuthStart(redirectUri?: string): Promise<{
  authorize_url: string;
  state: string;
  redirect_uri: string;
  mode: "popup" | "paste";
}> {
  return apiPost(
    "/auth/providers/openai-codex/oauth/start",
    redirectUri ? { redirect_uri: redirectUri } : {},
  );
}

export async function codexOAuthExchange(code: string, state: string): Promise<{
  status: string;
  provider: string;
  account_id: string;
  plan_type: string;
  expires_at: string;
}> {
  return apiPost("/auth/providers/openai-codex/oauth/exchange", { code, state });
}

export async function codexDeviceCodeStart(): Promise<{
  user_code: string;
  verification_url: string;
  interval: number;
  state: string;
  expires_in?: number;
}> {
  return apiPost("/auth/providers/openai-codex/device-code/start", {});
}

export async function codexDeviceCodePoll(state: string): Promise<{
  status: "pending" | "connected";
  provider?: string;
  account_id?: string;
  plan_type?: string;
  expires_at?: string;
}> {
  return apiPost("/auth/providers/openai-codex/device-code/poll", { state });
}

export async function connectCodex(tokenData: Record<string, unknown>): Promise<{
  status: string;
  provider: string;
  account_id: string;
  plan_type: string;
  expires_at: string;
}> {
  return apiPost("/auth/providers/openai-codex", { token_data: tokenData });
}

export async function disconnectCodex(): Promise<{ status: string }> {
  return apiDelete<{ status: string }>("/auth/providers/openai-codex");
}

export async function fetchCodexStatus(): Promise<CodexStatus> {
  return apiGet("/auth/providers/openai-codex/status");
}

export async function refreshCodexToken(): Promise<{
  status: string;
  expires_at: string;
}> {
  return apiPost("/auth/providers/openai-codex/refresh", {});
}
