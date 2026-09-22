"use client";

import { useState, useCallback } from "react";
import { Loader2, ExternalLink, ChevronRight, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { apiPost, apiDelete } from "@/lib/api";
import {
  subscriptionBrowserLoginStart,
  subscriptionBrowserLoginPoll,
  connectSubscriptionProvider,
  disconnectSubscriptionProvider,
  refreshSubscriptionToken,
  fetchSubscriptionModels,
  type SubscriptionModelEntry,
} from "@/lib/auth-api";
import { SubscriptionAccountCard } from "./SubscriptionAccountCard";
import { useSubscriptionAccount } from "./useSubscriptionAccount";
import { usePollingLogin } from "./useSubscriptionLogin";
import { PollingLoginProgress } from "./OAuthLoginProgress";


type WorkBuddyRealm = "cn" | "global";

const REALM_CONFIG: Record<WorkBuddyRealm, { provider: string; baseUrl: string; title: string; hint: string }> = {
  cn: {
    provider: "workbuddy-cn",
    baseUrl: "https://copilot.tencent.com/v2",
    title: "WorkBuddy 国内版",
    hint: "使用腾讯 CodeBuddy / WorkBuddy 国内版账号订阅（copilot.tencent.com），无需 API Key。登录后自动创建模型档案。",
  },
  global: {
    provider: "workbuddy-global",
    baseUrl: "https://www.workbuddy.ai/v2",
    title: "WorkBuddy Global",
    hint: "使用 WorkBuddy 国际版账号订阅（workbuddy.ai），无需 API Key。登录后自动创建模型档案。",
  },
};

export function WorkBuddyOAuthCard({
  realm,
  onProfileCreated,
  existingProfileNames,
}: {
  realm: WorkBuddyRealm;
  onProfileCreated: () => void;
  existingProfileNames: string[];
}) {
  const { provider: PROVIDER, baseUrl: REALM_BASE, title: CARD_TITLE, hint: CARD_HINT } = REALM_CONFIG[realm];
  const [error, setError] = useState("");

  // Manual paste
  const [tokenInput, setTokenInput] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [showManual, setShowManual] = useState(false);

  // Disconnect / Refresh
  const [disconnecting, setDisconnecting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  // Model catalog
  const [catalog, setCatalog] = useState<SubscriptionModelEntry[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [modelsExpanded, setModelsExpanded] = useState(false);
  const [addingModel, setAddingModel] = useState<string | null>(null);
  const [removingModel, setRemovingModel] = useState<string | null>(null);

  const existingProfiles = new Set(
    existingProfileNames.filter((n) => n.startsWith(`${PROVIDER}/`)),
  );


  const loadCatalog = useCallback(async () => {
    setCatalogLoading(true);
    try {
      const res = await fetchSubscriptionModels(PROVIDER);
      setCatalog(res.models || []);
    } catch {
      setCatalog([]);
    } finally {
      setCatalogLoading(false);
    }
  }, [PROVIDER]);

  const { status, setStatus, loading, statusError, reloadStatus, completeLogin } = useSubscriptionAccount(PROVIDER, onProfileCreated, loadCatalog);
  const isConnected = status?.status === "connected";
  const isExpired = status?.status === "expired";
  const browserLogin = usePollingLogin({
    start: async () => {
      const data = await subscriptionBrowserLoginStart(PROVIDER);
      return { ...data, url: data.auth_url };
    },
    poll: (state) => subscriptionBrowserLoginPoll(PROVIDER, state),
    onConnected: completeLogin, onError: setError, openBrowser: true,
  });
  const loginBusy = browserLogin.busy;
  const handleBrowserLogin = browserLogin.start;
  const busy = loginBusy || connecting || disconnecting || refreshing || !!addingModel || !!removingModel;

  const handleManualConnect = useCallback(async () => {
    if (!tokenInput.trim() || connecting) return;
    setConnecting(true);
    setError("");
    try {
      const parsed = JSON.parse(tokenInput.trim());
      await connectSubscriptionProvider(PROVIDER, parsed);
      await completeLogin();
      setTokenInput("");
      setShowManual(false);
    } catch (e) {
      setError(e instanceof SyntaxError ? "JSON 格式无效" : (e instanceof Error ? e.message : "连接失败"));
    } finally {
      setConnecting(false);
    }
  }, [tokenInput, connecting, completeLogin, PROVIDER]);

  const handleDisconnect = useCallback(async () => {
    if (disconnecting) return;
    setDisconnecting(true);
    setError("");
    try {
      await disconnectSubscriptionProvider(PROVIDER);
      onProfileCreated();
      setStatus({ status: "disconnected", provider: PROVIDER });
      setCatalog([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "断开失败");
    } finally {
      setDisconnecting(false);
    }
  }, [disconnecting, PROVIDER, onProfileCreated, setStatus]);

  const handleRefresh = useCallback(async () => {
    if (refreshing) return;
    setRefreshing(true);
    setError("");
    try {
      const result = await refreshSubscriptionToken(PROVIDER);
      setStatus((prev) => prev ? { ...prev, status: "connected", expires_at: result.expires_at } : prev);
      onProfileCreated();
      void loadCatalog();
    } catch (e) {
      setError(e instanceof Error ? e.message : "刷新失败");
    } finally {
      setRefreshing(false);
    }
  }, [refreshing, PROVIDER, onProfileCreated, loadCatalog, setStatus]);

  const handleAddModel = useCallback(async (entry: SubscriptionModelEntry) => {
    const profileName = entry.profile_name || `${PROVIDER}/${entry.model}`;
    setAddingModel(profileName);
    setError("");
    try {
      await apiPost("/config/models/profiles", {
        name: profileName,
        model: entry.public_model_id || profileName,
        api_key: "",
        base_url: REALM_BASE,
        description: `${entry.display_name || entry.model} — WorkBuddy 订阅登录（无需 API Key）`,
        protocol: "openai",
        thinking_mode: "auto",
        model_family: "",
        custom_extra_body: "",
        custom_extra_headers: "",
      }, { direct: true });
      onProfileCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建档案失败");
    } finally {
      setAddingModel(null);
    }
  }, [onProfileCreated, REALM_BASE, PROVIDER]);

  const handleRemoveModel = useCallback(async (profileName: string) => {
    setRemovingModel(profileName);
    setError("");
    try {
      await apiDelete(`/config/models/profiles/${encodeURIComponent(profileName)}`, { direct: true });
      onProfileCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除档案失败");
    } finally {
      setRemovingModel(null);
    }
  }, [onProfileCreated]);

  return (
    <SubscriptionAccountCard
      provider={PROVIDER} title={CARD_TITLE} description={realm === "cn" ? "国内账号 · copilot.tencent.com" : "国际账号 · workbuddy.ai"} account={status?.nickname || status?.email}
      status={status?.status} loading={loading} busy={busy} modelCount={existingProfiles.size}
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
            {status?.nickname && (<><span className="text-muted-foreground">昵称</span><span className="truncate">{status.nickname}</span></>)}
            {(status?.uid || status?.account_id) && (<><span className="text-muted-foreground">账户</span><span className="font-mono truncate">{status.uid || status.account_id}</span></>)}
            <span className="text-muted-foreground">区域</span><span>{realm === "global" ? "Global" : "CN"}</span>
            {status?.expires_at && (<><span className="text-muted-foreground">有效期至</span><span>{new Date(status.expires_at).toLocaleString("zh-CN")}</span></>)}
          </div>

          <div className="space-y-1">
            <button
              type="button"
              className="w-full flex items-center gap-1 text-left rounded-md border border-border/60 px-2 py-1 hover:bg-muted/40 transition-colors"
              aria-expanded={modelsExpanded}
              onClick={() => setModelsExpanded((v) => !v)}
            >
              {modelsExpanded
                ? <ChevronDown className="h-3 w-3 text-muted-foreground" />
                : <ChevronRight className="h-3 w-3 text-muted-foreground" />}
              <span className="text-[11px] text-muted-foreground">管理可用模型</span>
              <Badge variant="secondary" className="text-[10px] ml-auto">
                {existingProfiles.size}/{catalog.length || "…"}
              </Badge>
            </button>
            {modelsExpanded && (
              <>
                <p className="text-[11px] text-muted-foreground">添加后可在模型选择器中使用；移除仅删除该模型配置。</p>
                {catalogLoading ? (
                  <div className="flex items-center justify-center py-2">
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                  </div>
                ) : catalog.length === 0 ? (
                  <div className="flex items-center justify-between gap-2 py-1 text-xs text-muted-foreground"><span>暂未获取到模型目录</span><Button size="sm" variant="outline" onClick={loadCatalog}>重新获取</Button></div>
                ) : (
                  <div className="space-y-1">
                    {catalog.map((m) => {
                      const profileName = m.profile_name || `${PROVIDER}/${m.model}`;
                      const isAdded = existingProfiles.has(profileName);
                      const isBusy = addingModel === profileName || removingModel === profileName;
                      return (
                        <div
                          key={profileName}
                          className={`flex items-center gap-2 rounded-md border px-2 py-1 text-xs transition-colors ${
                            isAdded
                              ? "border-green-500/40 bg-green-500/5"
                              : "border-border hover:border-[var(--em-primary)]/40"
                          }`}
                        >
                          <span className="font-medium truncate min-w-0 flex-1">{m.display_name || m.model}</span>
                          <code className="text-[10px] font-mono text-muted-foreground hidden sm:inline truncate max-w-[30%]">{m.model}</code>
                          {isAdded ? (
                            <Button
                              size="sm" variant="ghost"
                              className="h-8 px-1.5 text-[11px] text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950 shrink-0"
                              onClick={() => handleRemoveModel(profileName)}
                              disabled={busy || isExpired}
                            >
                              {isBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : "移除"}
                            </Button>
                          ) : (
                            <Button
                              size="sm" variant="ghost"
                              className="h-8 px-1.5 text-[11px] shrink-0"
                              style={{ color: "var(--em-primary)" }}
                              onClick={() => handleAddModel(m)}
                              disabled={busy || isExpired}
                            >
                              {isBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : "添加"}
                            </Button>
                          )}
                        </div>
                      );
                    })}
                  </div>
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
            {CARD_HINT}
          </p>

          {!loginBusy ? (
            <Button
              size="sm"
              className="w-full h-10 text-xs text-white font-medium gap-1.5"
              style={{ backgroundColor: "var(--em-primary)" }}
              onClick={handleBrowserLogin}
              disabled={busy}
            >
              <ExternalLink className="h-3 w-3" />
              使用{CARD_TITLE}账号登录
            </Button>
          ) : (
            <PollingLoginProgress login={browserLogin} title={CARD_TITLE + " 登录"} />
          )}

          {/* 备选方式：手动粘贴 */}
          {!loginBusy && <div>
            <button type="button" disabled={connecting} aria-expanded={showManual} onClick={() => setShowManual(!showManual)} className="text-[11px] text-muted-foreground hover:text-foreground transition-colors">
              {showManual ? "▾ 收起手动粘贴" : "▸ 导入已有凭证（高级）"}
            </button>
            {showManual && (
              <div className="mt-1 space-y-1">
                <div className="text-[11px] text-muted-foreground space-y-0.5">
                  <p>粘贴 CPA <code className="px-0.5 rounded bg-background font-mono">workbuddy-*.json</code> 或包含 accessToken 的 JSON</p>
                </div>
                <textarea aria-label="已有账号凭证 JSON" autoComplete="off" spellCheck={false} disabled={busy}
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  className="w-full h-14 rounded-md border border-input bg-background px-2 py-1 text-[11px] font-mono resize-none"
                  placeholder='{"auth":{"accessToken":"...","refreshToken":"..."}}'
                />
                <Button size="sm" variant="outline" className="h-8 text-[11px]" onClick={handleManualConnect} disabled={busy || !tokenInput.trim()}>
                  {connecting ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}导入并连接
                </Button>
              </div>
            )}
          </div>}
        </>
      )}

    </SubscriptionAccountCard>
  );
}
