"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { Loader2, ExternalLink, ChevronRight, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { apiPost, apiDelete } from "@/lib/api";
import {
  fetchSubscriptionStatus,
  subscriptionBrowserLoginStart,
  subscriptionBrowserLoginPoll,
  connectSubscriptionProvider,
  disconnectSubscriptionProvider,
  refreshSubscriptionToken,
  fetchSubscriptionModels,
  type SubscriptionStatus,
  type SubscriptionModelEntry,
} from "@/lib/auth-api";
import { ProviderLogo } from "./ProviderLogo";

const POLL_INTERVAL_MS = 3000;

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
  const [status, setStatus] = useState<SubscriptionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Browser-poll login
  const [loginBusy, setLoginBusy] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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
  const [cardCollapsed, setCardCollapsed] = useState(true);

  const existingProfiles = new Set(
    existingProfileNames.filter((n) => n.startsWith(`${PROVIDER}/`)),
  );

  const isConnected = status?.status === "connected";
  const isExpired = status?.status === "expired";

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

  useEffect(() => {
    let cancelled = false;
    fetchSubscriptionStatus(PROVIDER)
      .then((data) => {
        if (cancelled) return;
        setStatus(data);
        if (data.status === "connected") void loadCatalog();
      })
      .catch(() => {
        if (!cancelled) setStatus({ status: "disconnected", provider: PROVIDER });
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [loadCatalog, PROVIDER]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (timeoutRef.current) { clearTimeout(timeoutRef.current); timeoutRef.current = null; }
    setLoginBusy(false);
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const handleBrowserLogin = useCallback(async () => {
    if (loginBusy) return;
    setLoginBusy(true);
    setError("");
    try {
      const data = await subscriptionBrowserLoginStart(PROVIDER);
      const parsed = new URL(data.auth_url);
      if (parsed.protocol !== "https:") throw new Error("授权地址无效");
      window.open(data.auth_url, "_blank", "noopener,noreferrer");
      pollRef.current = setInterval(async () => {
        try {
          const result = await subscriptionBrowserLoginPoll(PROVIDER, data.state);
          if (result.status === "connected") {
            stopPolling();
            onProfileCreated();
            fetchSubscriptionStatus(PROVIDER)
              .then((s) => { setStatus(s); void loadCatalog(); })
              .catch(() => {});
          }
        } catch (e) {
          const msg = e instanceof Error ? e.message : "轮询登录状态失败";
          if (/无效|过期|不存在/.test(msg)) {
            stopPolling();
            setError(msg);
          }
        }
      }, POLL_INTERVAL_MS);
      const expiresMs = Math.max((data.expires_in ?? 600) * 1000, 30_000);
      timeoutRef.current = setTimeout(() => {
        if (pollRef.current) { stopPolling(); setError("登录超时，请重试"); }
      }, expiresMs);
    } catch (e) {
      setLoginBusy(false);
      setError(e instanceof Error ? e.message : "无法发起登录");
    }
  }, [loginBusy, onProfileCreated, stopPolling, loadCatalog, PROVIDER]);

  const handleManualConnect = useCallback(async () => {
    if (!tokenInput.trim() || connecting) return;
    setConnecting(true);
    setError("");
    try {
      const parsed = JSON.parse(tokenInput.trim());
      await connectSubscriptionProvider(PROVIDER, parsed);
      onProfileCreated();
      const next = await fetchSubscriptionStatus(PROVIDER);
      setStatus(next);
      void loadCatalog();
      setTokenInput("");
      setShowManual(false);
    } catch (e) {
      setError(e instanceof SyntaxError ? "JSON 格式无效" : (e instanceof Error ? e.message : "连接失败"));
    } finally {
      setConnecting(false);
    }
  }, [tokenInput, connecting, onProfileCreated, loadCatalog, PROVIDER]);

  const handleDisconnect = useCallback(async () => {
    if (disconnecting) return;
    setDisconnecting(true);
    setError("");
    try {
      await disconnectSubscriptionProvider(PROVIDER);
      setStatus({ status: "disconnected", provider: PROVIDER });
      setCatalog([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "断开失败");
    } finally {
      setDisconnecting(false);
    }
  }, [disconnecting, PROVIDER]);

  const handleRefresh = useCallback(async () => {
    if (refreshing) return;
    setRefreshing(true);
    setError("");
    try {
      const result = await refreshSubscriptionToken(PROVIDER);
      setStatus((prev) => prev ? { ...prev, status: "connected", expires_at: result.expires_at } : prev);
    } catch (e) {
      setError(e instanceof Error ? e.message : "刷新失败");
    } finally {
      setRefreshing(false);
    }
  }, [refreshing, PROVIDER]);

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
    <div
      className={`rounded-lg border px-2.5 py-2.5 space-y-2 ${
        isConnected
          ? "border-green-500/40 bg-green-500/5"
          : "border-[var(--em-primary)]/30 bg-[var(--em-primary)]/5"
      }`}
    >
      <button
        type="button"
        className="w-full flex items-center gap-1.5 text-left"
        onClick={() => { if (!loading && (isConnected || isExpired)) setCardCollapsed((v) => !v); }}
      >
        <ProviderLogo id={PROVIDER} />
        <p className="text-xs font-semibold">{CARD_TITLE} 订阅登录</p>
        <Badge variant="secondary" className="text-[10px] ml-auto">
          {loading ? "检测中" : isConnected ? "已连接" : isExpired ? "已过期" : "未连接"}
        </Badge>
        {!loading && (isConnected || isExpired) && (
          cardCollapsed
            ? <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
            : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
        )}
      </button>

      {loading && (
        <div className="flex items-center justify-center py-2">
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* ── Connected / Expired ── */}
      {!loading && !cardCollapsed && (isConnected || isExpired) && (
        <>
          <div className="flex items-center gap-2">
            <div className={`h-2 w-2 rounded-full ${isExpired ? "bg-amber-500" : "bg-green-500"}`} />
            <span className="text-[11px] font-medium">{isExpired ? "Token 已过期" : "已连接"}</span>
          </div>
          <div className="grid grid-cols-2 gap-y-1 text-[11px]">
            {status?.nickname && (<><span className="text-muted-foreground">昵称</span><span className="truncate">{status.nickname}</span></>)}
            {(status?.uid || status?.account_id) && (<><span className="text-muted-foreground">账户</span><span className="font-mono truncate">{status.uid || status.account_id}</span></>)}
            <span className="text-muted-foreground">区域</span><span>{realm === "global" ? "Global" : "CN"}</span>
            {status?.expires_at && (<><span className="text-muted-foreground">有效期至</span><span>{new Date(status.expires_at).toLocaleString("zh-CN")}</span></>)}
          </div>

          <div className="space-y-1">
            <button
              type="button"
              className="w-full flex items-center gap-1 text-left rounded-md border border-border/60 px-2 py-1 hover:bg-muted/40 transition-colors"
              onClick={() => setModelsExpanded((v) => !v)}
            >
              {modelsExpanded
                ? <ChevronDown className="h-3 w-3 text-muted-foreground" />
                : <ChevronRight className="h-3 w-3 text-muted-foreground" />}
              <span className="text-[10px] text-muted-foreground">可用 WorkBuddy 模型（点击展开）</span>
              <Badge variant="secondary" className="text-[9px] ml-auto">
                {existingProfiles.size}/{catalog.length || "…"}
              </Badge>
            </button>
            {modelsExpanded && (
              <>
                <p className="text-[10px] text-muted-foreground">点击添加/移除</p>
                {catalogLoading ? (
                  <div className="flex items-center justify-center py-2">
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                  </div>
                ) : catalog.length === 0 ? (
                  <p className="text-[10px] text-muted-foreground py-1">未能获取模型目录</p>
                ) : (
                  <div className="space-y-1">
                    {catalog.map((m) => {
                      const profileName = m.profile_name || `${PROVIDER}/${m.model}`;
                      const isAdded = existingProfiles.has(profileName);
                      const isBusy = addingModel === profileName || removingModel === profileName;
                      return (
                        <div
                          key={profileName}
                          className={`flex items-center gap-2 rounded-md border px-2 py-1 text-[11px] transition-colors ${
                            isAdded
                              ? "border-green-500/40 bg-green-500/5"
                              : "border-border hover:border-[var(--em-primary)]/40"
                          }`}
                        >
                          <span className="font-medium truncate min-w-0 flex-1">{m.display_name || m.model}</span>
                          <code className="text-[9px] font-mono text-muted-foreground hidden sm:inline truncate max-w-[30%]">{m.model}</code>
                          {isAdded ? (
                            <Button
                              size="sm" variant="ghost"
                              className="h-5 px-1.5 text-[10px] text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950 shrink-0"
                              onClick={() => handleRemoveModel(profileName)}
                              disabled={isBusy}
                            >
                              {isBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : "移除"}
                            </Button>
                          ) : (
                            <Button
                              size="sm" variant="ghost"
                              className="h-5 px-1.5 text-[10px] shrink-0"
                              style={{ color: "var(--em-primary)" }}
                              onClick={() => handleAddModel(m)}
                              disabled={isBusy}
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
              <Button size="sm" variant="outline" className="h-6 text-[10px]" onClick={handleRefresh} disabled={refreshing}>
                {refreshing ? <Loader2 className="h-3 w-3 animate-spin" /> : "刷新 Token"}
              </Button>
            )}
            <Button size="sm" variant="outline" className="h-6 text-[10px] text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950" onClick={handleDisconnect} disabled={disconnecting}>
              {disconnecting ? <Loader2 className="h-3 w-3 animate-spin" /> : "断开连接"}
            </Button>
          </div>
        </>
      )}

      {/* ── Not connected ── */}
      {!loading && !isConnected && !isExpired && (
        <>
          <p className="text-[11px] text-muted-foreground leading-relaxed">
            {CARD_HINT}
          </p>

          {!loginBusy ? (
            <Button
              size="sm"
              className="w-full h-8 text-xs text-white font-medium gap-1.5"
              style={{ backgroundColor: "var(--em-primary)" }}
              onClick={handleBrowserLogin}
            >
              <ExternalLink className="h-3 w-3" />
              使用{CARD_TITLE}账号登录
            </Button>
          ) : (
            <div className="space-y-2 rounded-md border border-border bg-muted/30 p-2.5">
              <div className="text-center space-y-1">
                <Loader2 className="h-5 w-5 mx-auto animate-spin text-muted-foreground" />
                <p className="text-[11px] text-muted-foreground">请在浏览器中完成 WorkBuddy 登录...</p>
                <p className="text-[10px] text-muted-foreground">授权完成后自动更新</p>
              </div>
              <div className="flex justify-center">
                <button type="button" className="text-[10px] text-muted-foreground hover:text-foreground transition-colors" onClick={stopPolling}>取消</button>
              </div>
            </div>
          )}

          {/* 备选方式：手动粘贴 */}
          <div>
            <button type="button" onClick={() => setShowManual(!showManual)} className="text-[10px] text-muted-foreground hover:text-foreground transition-colors">
              {showManual ? "▾ 收起手动粘贴" : "▸ 手动粘贴 Token"}
            </button>
            {showManual && (
              <div className="mt-1 space-y-1">
                <div className="text-[10px] text-muted-foreground space-y-0.5">
                  <p>粘贴 CPA <code className="px-0.5 rounded bg-background font-mono">workbuddy-*.json</code> 或包含 accessToken 的 JSON</p>
                </div>
                <textarea
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  className="w-full h-14 rounded-md border border-input bg-background px-2 py-1 text-[10px] font-mono resize-none"
                  placeholder='{"auth":{"accessToken":"...","refreshToken":"..."}}'
                />
                <Button size="sm" variant="outline" className="h-6 text-[10px]" onClick={handleManualConnect} disabled={connecting || !tokenInput.trim()}>
                  {connecting ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}粘贴连接
                </Button>
              </div>
            )}
          </div>
        </>
      )}

      {error ? <p className="text-[10px] text-destructive">{error}</p> : null}
    </div>
  );
}
