"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Loader2, ExternalLink, ChevronRight, ChevronDown, Lock,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { apiPost, apiDelete } from "@/lib/api";
import {
  codexOAuthStart,
  codexOAuthExchange,
  codexDeviceCodeStart,
  codexDeviceCodePoll,
  connectCodex,
  disconnectCodex,
  refreshCodexToken,
} from "@/lib/auth-api";
import { SubscriptionAccountCard } from "./SubscriptionAccountCard";
import { useSubscriptionAccount } from "./useSubscriptionAccount";
import { useOAuthLogin, usePollingLogin } from "./useSubscriptionLogin";
import { OAuthLoginProgress, PollingLoginProgress } from "./OAuthLoginProgress";
import { CODEX_OAUTH_PRESET, CODEX_MODELS } from "./constants";
import type { CodexModelEntry } from "./types";

const CODEX_AUTH_ORIGIN = "https://auth.openai.com";
const CODEX_CALLBACK_PATH = "/auth/callback";

function assertCodexAuthUrl(value: string): string {
  const parsed = new URL(value);
  if (parsed.protocol !== "https:" || parsed.origin !== CODEX_AUTH_ORIGIN || parsed.username || parsed.password) {
    throw new Error("授权地址无效");
  }
  return value;
}

export function CodexOAuthCard({
  onProfileCreated,
  existingProfileNames,
}: {
  onProfileCreated: () => void;
  existingProfileNames: string[];
}) {
  const [error, setError] = useState("");
  const [androidClient, setAndroidClient] = useState(false);
  useEffect(() => { setAndroidClient(window.excelManusAndroid?.version === 1); }, []);

  // Manual paste
  const [tokenInput, setTokenInput] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [showFallback, setShowFallback] = useState(false);
  const [showManual, setShowManual] = useState(false);

  // Disconnect / Refresh
  const [disconnecting, setDisconnecting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  // Multi-model profile management
  const [addingModel, setAddingModel] = useState<string | null>(null);
  const [removingModel, setRemovingModel] = useState<string | null>(null);
  const [codexModelsExpanded, setCodexModelsExpanded] = useState(false);

  // Build a Set of existing codex profile names for quick lookup.
  // Map legacy short names and old defaults to current full openai-codex/xxx format.
  const _LEGACY_NAME_MAP: Record<string, string> = {
    "Codex 5.3": "openai-codex/gpt-6-astra",
    "Codex Spark": "openai-codex/gpt-5.3-codex-spark",
    "codex-oauth": "openai-codex/gpt-6-astra",
    "codex-spark": "openai-codex/gpt-5.3-codex-spark",
    "codex-5.3": "openai-codex/gpt-6-astra",
    "codex-5.2": "openai-codex/gpt-5.2-codex",
    "codex-5.1": "openai-codex/gpt-5.1-codex",
    "codex-mini": "openai-codex/gpt-5.1-codex-mini",
    "codex-max": "openai-codex/gpt-5.1-codex-max",
    "codex-mini-latest": "openai-codex/gpt-5-codex-mini",
    "codex-gpt-5.2": "openai-codex/gpt-5.2",
    "codex-gpt-5.1": "openai-codex/gpt-5.1",
    "codex-gpt-6": "openai-codex/gpt-6-astra",
    "codex-gpt-5.6": "openai-codex/gpt-5.6",
  };
  const existingCodexProfiles = new Set<string>();
  for (const n of existingProfileNames) {
    const cm = CODEX_MODELS.find((m) => m.profileName === n);
    if (cm) existingCodexProfiles.add(cm.profileName);
    const mapped = _LEGACY_NAME_MAP[n];
    if (mapped) existingCodexProfiles.add(mapped);
  }
  // Check legacy short names that should be cleaned up
  const hasLegacyCodexProfile = existingProfileNames.some((n) => n in _LEGACY_NAME_MAP && !n.startsWith("openai-codex/"));

  const { status, setStatus, loading, statusError, reloadStatus, completeLogin } = useSubscriptionAccount("openai-codex", onProfileCreated);
  const oauth = useOAuthLogin({
    name: "codex-oauth", messageType: "codex-oauth-callback",
    start: () => codexOAuthStart(["localhost", "127.0.0.1"].includes(window.location.hostname) ? window.location.origin + CODEX_CALLBACK_PATH : undefined),
    validateUrl: assertCodexAuthUrl, exchange: codexOAuthExchange,
    onConnected: completeLogin, onError: setError,
  });
  const device = usePollingLogin({
    start: async () => {
      const data = await codexDeviceCodeStart();
      return { ...data, url: assertCodexAuthUrl(data.verification_url), code: data.user_code };
    },
    poll: codexDeviceCodePoll, onConnected: completeLogin, onError: setError,
  });
  const authorizing = device.busy;
  const handleDeviceCode = device.start;
  const oauthBusy = oauth.busy;
  const handleOAuthLogin = oauth.start;
  const busy = oauthBusy || authorizing || connecting || disconnecting || refreshing || !!addingModel || !!removingModel;

  const handleManualConnect = useCallback(async () => {
    if (!tokenInput.trim() || connecting) return;
    setConnecting(true); setError("");
    try {
      const parsed = JSON.parse(tokenInput.trim());
      await connectCodex(parsed);
      await completeLogin();
      setTokenInput(""); setShowManual(false);
    } catch (e) {
      setError(e instanceof SyntaxError ? "JSON 格式无效" : (e instanceof Error ? e.message : "连接失败"));
    } finally { setConnecting(false); }
  }, [tokenInput, connecting, completeLogin]);

  const handleDisconnect = useCallback(async () => {
    if (disconnecting) return;
    setDisconnecting(true); setError("");
    try { await disconnectCodex(); onProfileCreated(); setStatus({ status: "disconnected", provider: "openai-codex" }); }
    catch (e) { setError(e instanceof Error ? e.message : "断开失败"); }
    finally { setDisconnecting(false); }
  }, [disconnecting, onProfileCreated, setStatus]);

  const handleRefresh = useCallback(async () => {
    if (refreshing) return;
    setRefreshing(true); setError("");
    try {
      const result = await refreshCodexToken();
      setStatus((prev) => prev ? { ...prev, status: "connected", expires_at: result.expires_at } : prev);
      onProfileCreated();
    } catch (e) { setError(e instanceof Error ? e.message : "刷新失败"); }
    finally { setRefreshing(false); }
  }, [refreshing, onProfileCreated, setStatus]);

  const handleAddCodexModel = useCallback(async (entry: CodexModelEntry) => {
    setAddingModel(entry.profileName); setError("");
    try {
      await apiPost("/config/models/profiles", {
        name: entry.profileName,
        model: entry.publicId,
        api_key: "",
        base_url: CODEX_OAUTH_PRESET.base_url,
        description: `${entry.displayName} — OAuth 登录（无需 API Key）`,
        protocol: CODEX_OAUTH_PRESET.protocol,
        thinking_mode: CODEX_OAUTH_PRESET.thinking_mode,
        model_family: CODEX_OAUTH_PRESET.model_family,
        custom_extra_body: "",
        custom_extra_headers: "",
      }, { direct: true });
      onProfileCreated();
    } catch (e) { setError(e instanceof Error ? e.message : "创建档案失败"); }
    finally { setAddingModel(null); }
  }, [onProfileCreated]);

  const handleRemoveCodexModel = useCallback(async (profileName: string) => {
    setRemovingModel(profileName); setError("");
    try {
      await apiDelete(`/config/models/profiles/${encodeURIComponent(profileName)}`, { direct: true });
      onProfileCreated();
    } catch (e) { setError(e instanceof Error ? e.message : "删除档案失败"); }
    finally { setRemovingModel(null); }
  }, [onProfileCreated]);

  const isConnected = status?.status === "connected";
  const isExpired = status?.status === "expired";

  return (
    <SubscriptionAccountCard
      provider={"openai-codex"} title="GPT Codex" description="使用 ChatGPT 订阅连接" account={[status?.email, status?.plan_type].filter(Boolean).join(" · ")}
      status={status?.status} loading={loading} busy={busy} modelCount={existingCodexProfiles.size}
      error={error} statusError={statusError} onRetry={reloadStatus}
    >
      {/* ── Connected / Expired ── */}
      {!loading && (isConnected || isExpired) && (
        <>
          <div className="flex items-center gap-2">
            <div className={`h-2 w-2 rounded-full ${isExpired ? "bg-amber-500" : "bg-green-500"}`} />
            <span className="text-xs font-medium">{isExpired ? "Token 已过期" : "已连接"}</span>
          </div>
          <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-xs">
            {status?.email && (<><span className="text-muted-foreground">邮箱</span><span className="truncate">{status.email}</span></>)}
            {status?.account_id && (<><span className="text-muted-foreground">账户</span><span className="font-mono truncate">{status.account_id}</span></>)}
            {status?.plan_type && (<><span className="text-muted-foreground">订阅</span><span className="capitalize">{status.plan_type}</span></>)}
            {status?.expires_at && (<><span className="text-muted-foreground">有效期至</span><span>{new Date(status.expires_at).toLocaleString("zh-CN")}</span></>)}
          </div>

          {/* 模型列表 — 多模型添加 */}
          <div className="space-y-1">
            <button
              type="button"
              className="w-full flex items-center gap-1 text-left rounded-md border border-border/60 px-2 py-1 hover:bg-muted/40 transition-colors"
              aria-expanded={codexModelsExpanded}
              onClick={() => setCodexModelsExpanded((v) => !v)}
            >
              {codexModelsExpanded ? (
                <ChevronDown className="h-3 w-3 text-muted-foreground" />
              ) : (
                <ChevronRight className="h-3 w-3 text-muted-foreground" />
              )}
              <span className="text-[11px] text-muted-foreground">管理可用模型</span>
              <Badge variant="secondary" className="text-[10px] ml-auto">
                {existingCodexProfiles.size}/{CODEX_MODELS.length}
              </Badge>
            </button>
            {codexModelsExpanded && (
              <>
                <p className="text-[11px] text-muted-foreground">添加后可在模型选择器中使用；移除仅删除该模型配置。</p>
                <div className="space-y-1">
                  {CODEX_MODELS.map((m) => {
                    const isProLocked = m.proOnly && status?.plan_type !== "pro";
                    const isAdded = existingCodexProfiles.has(m.profileName);
                    const isBusy = addingModel === m.profileName || removingModel === m.profileName;
                    return (
                      <div
                        key={m.profileName}
                        className={`flex items-center gap-2 rounded-md border px-2 py-1 text-xs transition-colors ${
                          isProLocked
                            ? "border-border/50 bg-muted/30 opacity-50"
                            : isAdded
                              ? "border-green-500/40 bg-green-500/5"
                              : "border-border hover:border-[var(--em-primary)]/40"
                        }`}
                      >
                        <span className="font-medium truncate min-w-0 flex-1">{m.displayName}</span>
                        <code className="text-[10px] font-mono text-muted-foreground hidden sm:inline truncate max-w-[30%]">{m.modelId}</code>
                        {m.proOnly && <Badge variant="secondary" className="text-[10px] shrink-0">Pro</Badge>}
                        {isProLocked ? (
                          <Lock className="h-3 w-3 text-muted-foreground shrink-0" />
                        ) : isAdded ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-8 px-1.5 text-[11px] text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950 shrink-0"
                            onClick={() => handleRemoveCodexModel(m.profileName)}
                            disabled={busy || isExpired}
                          >
                            {isBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : "移除"}
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-8 px-1.5 text-[11px] shrink-0"
                            style={{ color: "var(--em-primary)" }}
                            onClick={() => handleAddCodexModel(m)}
                            disabled={busy || isExpired}
                          >
                            {isBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : "添加"}
                          </Button>
                        )}
                      </div>
                    );
                  })}
                </div>
                {hasLegacyCodexProfile && (
                  <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-1">
                    检测到旧版 codex-oauth 档案，建议删除后使用上方按钮重新添加
                  </p>
                )}
              </>
            )}
          </div>

          <div className="flex gap-1.5 flex-wrap">
            {status?.has_refresh_token && (
              <Button size="sm" variant="outline" className="h-8 text-[11px]" onClick={handleRefresh} disabled={busy}>
                {refreshing ? <Loader2 className="h-3 w-3 animate-spin" /> : "刷新授权"}
              </Button>
            )}
            <Button size="sm" variant="outline" className="h-8 text-[11px] text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950" onClick={handleDisconnect} disabled={busy}>
              {disconnecting ? <Loader2 className="h-3 w-3 animate-spin" /> : "断开连接"}
            </Button>
          </div>
        </>
      )}

      {/* ── Not connected ── */}
      {!loading && !isConnected && (
        <>
          <p className="text-xs text-muted-foreground leading-relaxed">
            使用 ChatGPT Plus/Pro 订阅，无需 API Key。登录后自动创建模型档案。
          </p>

          {!oauthBusy ? (
            <Button
              size="sm"
              className="w-full h-10 text-xs text-white font-medium gap-1.5"
              style={{ backgroundColor: "var(--em-primary)" }}
              onClick={() => {
                if (androidClient) {
                  setShowFallback(true);
                  void handleDeviceCode();
                } else {
                  void handleOAuthLogin();
                }
              }}
              disabled={busy}
            >
              <ExternalLink className="h-3 w-3" />
              {androidClient ? "使用设备码连接 ChatGPT" : "使用 ChatGPT 账号登录"}
            </Button>
          ) : (
            <OAuthLoginProgress login={oauth} />
          )}

          {/* 备选方式 */}
          {!oauthBusy && <div>
            <button type="button" disabled={authorizing || connecting} aria-expanded={showFallback} onClick={() => setShowFallback(!showFallback)} className="text-[11px] text-muted-foreground hover:text-foreground transition-colors">
              {showFallback ? "▾ 收起备选方式" : "▸ 其他连接方式"}
            </button>
            {showFallback && (
              <div className="mt-1.5 space-y-2.5">
                {/* Device Code */}
                <div className="space-y-1">
                  <p className="text-[11px] font-medium text-muted-foreground">设备码登录</p>
                  {!authorizing ? (
                    <Button size="sm" variant="outline" className="w-full h-9 text-xs" onClick={handleDeviceCode} disabled={busy}>使用设备码登录</Button>
                  ) : (
                    <PollingLoginProgress login={device} title="ChatGPT 设备码登录" />
                  )}
                </div>
                {/* Manual paste */}
                <div>
                  <button type="button" disabled={authorizing || connecting} aria-expanded={showManual} onClick={() => setShowManual(!showManual)} className="text-[11px] text-muted-foreground hover:text-foreground transition-colors">
                    {showManual ? "▾ 收起手动粘贴" : "▸ 导入已有凭证（高级）"}
                  </button>
                  {showManual && (
                    <div className="mt-1 space-y-1">
                      <div className="text-[11px] text-muted-foreground space-y-0.5">
                        <p>1. 运行 <code className="px-0.5 rounded bg-background font-mono">codex login</code></p>
                        <p>2. 复制 <code className="px-0.5 rounded bg-background font-mono">~/.codex/auth.json</code></p>
                      </div>
                      <textarea aria-label="已有账号凭证 JSON" autoComplete="off" spellCheck={false} disabled={busy} value={tokenInput} onChange={(e) => setTokenInput(e.target.value)} className="w-full h-14 rounded-md border border-input bg-background px-2 py-1 text-[11px] font-mono resize-none" placeholder='{"token":"...","refresh_token":"..."}' />
                      <Button size="sm" variant="outline" className="h-8 text-[11px]" onClick={handleManualConnect} disabled={busy || !tokenInput.trim()}>
                        {connecting ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}导入并连接
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>}
        </>
      )}

    </SubscriptionAccountCard>
  );
}
