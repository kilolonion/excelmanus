/**
 * 号池管理 API 封装层。
 * 所有端点均为管理员专用（/api/v1/admin/pool/*）。
 */

import { apiGet, apiPost, apiPatch } from "./api";

// ── Types ─────────────────────────────────────────────────

export interface PoolAccount {
  id: string;
  label: string;
  provider: string;
  account_id: string;
  plan_type: string;
  status: "active" | "disabled" | "depleted";
  daily_budget_tokens: number;
  weekly_budget_tokens: number;
  timezone: string;
  health_signal: "ok" | "depleted" | "rate_limited" | "transient";
  health_confidence: number;
  health_updated_at: string;
  created_at: string;
  updated_at: string;
}

export interface PoolBudgetSnapshot {
  pool_account_id: string;
  day_window_tokens: number;
  week_window_tokens: number;
  daily_remaining: number;
  weekly_remaining: number;
  snapshot_at: string;
}

export interface PoolAccountSummary {
  id: string;
  label: string;
  provider: string;
  account_id: string;
  plan_type: string;
  status: "active" | "disabled" | "depleted";
  daily_budget_tokens: number;
  weekly_budget_tokens: number;
  timezone: string;
  health_signal: "ok" | "depleted" | "rate_limited" | "transient";
  health_confidence: number;
  health_updated_at: string;
  created_at: string;
  updated_at: string;
  budget: PoolBudgetSnapshot | null;
  is_active: boolean;
}

export interface PoolManualActive {
  provider: string;
  model_pattern: string;
  pool_account_id: string;
  activated_by: string;
  activated_at: string;
}

export interface PoolAutoPolicy {
  id: string;
  provider: string;
  model_pattern: string;
  enabled: boolean;
  low_watermark: number;
  rate_limit_threshold: number;
  transient_threshold: number;
  error_window_minutes: number;
  cooldown_seconds: number;
  fallback_to_default: boolean;
  hysteresis_delta: number;
  min_dwell_seconds: number;
  breaker_open_seconds: number;
  created_at: string;
  updated_at: string;
}

export interface PoolRotationEvent {
  id: number;
  provider: string;
  model_pattern: string;
  from_account_id: string;
  to_account_id: string;
  reason: string;
  trigger: "hard" | "soft" | "manual" | "fallback";
  fallback_used: boolean;
  created_at: string;
}

export interface ProbeResult {
  status: "ok" | "error";
  message: string;
  http_status?: number;
  detail?: string;
}

// ── API 函数 ──────────────────────────────────────────────

const BASE = "/admin/pool";

/** 号池总览（账号 + 快照 + 激活状态）。 */
export async function fetchPoolSummary(): Promise<{
  accounts: PoolAccountSummary[];
  total: number;
}> {
  return apiGet(`${BASE}/summary`);
}

/** 列出所有池账号。 */
export async function fetchPoolAccounts(): Promise<{
  accounts: PoolAccount[];
  total: number;
}> {
  return apiGet(`${BASE}/accounts`);
}

/** 导入池账号（OAuth token JSON）。 */
export async function importPoolAccount(body: {
  token_data: Record<string, unknown>;
  label?: string;
  daily_budget_tokens?: number;
  weekly_budget_tokens?: number;
  timezone?: string;
}): Promise<{ status: string; account: PoolAccount }> {
  return apiPost(`${BASE}/accounts/oauth`, body);
}

/** 更新池账号字段。 */
export async function updatePoolAccount(
  accountId: string,
  body: Partial<{
    label: string;
    status: string;
    daily_budget_tokens: number;
    weekly_budget_tokens: number;
    timezone: string;
  }>,
): Promise<{ status: string; account: PoolAccount }> {
  return apiPatch(`${BASE}/accounts/${accountId}`, body);
}

/** 探测池账号连通性。 */
export async function probePoolAccount(
  accountId: string,
): Promise<ProbeResult> {
  return apiPost(`${BASE}/accounts/${accountId}/probe`, {});
}

/** 设置人工激活映射。 */
export async function setManualActive(body: {
  provider?: string;
  model_pattern?: string;
  pool_account_id: string;
}): Promise<{ status: string; mapping: PoolManualActive }> {
  return apiPost(`${BASE}/manual-active`, body);
}

/** 查看当前激活映射。 */
export async function fetchManualActive(): Promise<{
  mappings: PoolManualActive[];
  total: number;
}> {
  return apiGet(`${BASE}/manual-active`);
}

// ── 自动轮换 ──────────────────────────────────────────────

/** 列出所有自动轮换策略。 */
export async function fetchAutoPolicies(): Promise<{
  policies: PoolAutoPolicy[];
  total: number;
}> {
  return apiGet(`${BASE}/auto/policies`);
}

/** 创建/更新自动轮换策略。 */
export async function upsertAutoPolicy(
  body: Partial<Omit<PoolAutoPolicy, "id" | "created_at" | "updated_at">>,
): Promise<{ status: string; policy: PoolAutoPolicy }> {
  return apiPost(`${BASE}/auto/policies`, body);
}

/** 手动触发一次自动轮换评估。 */
export async function runAutoEvaluate(): Promise<{
  status: string;
  results: Array<Record<string, unknown>>;
}> {
  return apiPost(`${BASE}/auto/run`, {});
}

/** 查看轮换审计事件。 */
export async function fetchRotationEvents(opts?: {
  limit?: number;
  provider?: string;
  model_pattern?: string;
}): Promise<{ events: PoolRotationEvent[]; total: number }> {
  const params = new URLSearchParams();
  if (opts?.limit) params.set("limit", String(opts.limit));
  if (opts?.provider) params.set("provider", opts.provider);
  if (opts?.model_pattern) params.set("model_pattern", opts.model_pattern);
  const qs = params.toString();
  return apiGet(`${BASE}/auto/events${qs ? `?${qs}` : ""}`);
}
