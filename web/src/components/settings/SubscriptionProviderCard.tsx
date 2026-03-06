"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { ChevronRight, ChevronDown, Loader2, ExternalLink, Copy, RefreshCw, LogOut, Key } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  providerOAuthStart,
  providerOAuthExchange,
  providerDeviceCodeStart,
  providerDeviceCodePoll,
  connectProvider,
  disconnectProvider,
  fetchProviderStatus,
  refreshProviderToken,
  type ProviderStatus,
  type ProviderDescriptor,
  type ProviderModelEntry,
} from "@/lib/auth-api";

function ProviderLogo({ id }: { id: string }) {
  const slug: Record<string, string> = {
    "openai-codex": "openai",
    "google-gemini": "gemini",
    openai: "openai",
    anthropic: "anthropic",
    gemini: "gemini",
    deepseek: "deepseek",
  };
  const s = slug[id] || id;
  return (
    <img
      src={`/provider-logos/${s}.svg`}
      alt={id}
      className="h-4 w-4 rounded"
      onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
    />
  );
}

export interface SubscriptionProviderCardProps {
  descriptor: ProviderDescriptor;
  onModelSelected?: (model: ProviderModelEntry) => void;
  onProfileCreated?: () => void;
  defaultCollapsed?: boolean;
}

export function SubscriptionProviderCard({
  descriptor,
  onModelSelected,
  onProfileCreated,
  defaultCollapsed = true,
}: SubscriptionProviderCardProps) {
  const providerId = descriptor.id;
  const flows = descriptor.supported_flows;

  const [status, setStatus] = useState<ProviderStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [collapsed, setCollapsed] = useState(defaultCollapsed);

  const [oauthBusy, setOauthBusy] = useState(false);
  const [oauthState, setOauthState] = useState("");
  const [pasteUrl, setPasteUrl] = useState("");
  const [oauthMode, setOauthMode] = useState<"popup" | "paste" | null>(null);
  const popupRef = useRef<Window | null>(null);
  const popupTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [deviceState, setDeviceState] = useState<string | null>(null);
  const [userCode, setUserCode] = useState("");
  const [verificationUrl, setVerificationUrl] = useState("");
  const [authorizing, setAuthorizing] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [tokenInput, setTokenInput] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [refreshingToken, setRefreshingToken] = useState(false);
  const [showFallback, setShowFallback] = useState(false);
  const [showManualPaste, setShowManualPaste] = useState(false);
  const [selectedModel, setSelectedModel] = useState("");

  const availableModels = descriptor.models.filter(
    (m) => !m.pro_only || status?.plan_type === "pro",
  );
  const defaultModel = availableModels[0];

  useEffect(() => {
    let cancelled = false;
    fetchProviderStatus(providerId)
      .then((data) => { if (!cancelled) setStatus(data); })
      .catch((e) => {
        if (!cancelled) {
          console.warn(`[SubscriptionProvider] fetchProviderStatus(${providerId}) failed:`, e);
          setStatus({ status: "disconnected", provider: providerId });
          setError(e instanceof Error ? e.message : "获取订阅状态失败，请刷新页面重试");
        }
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [providerId]);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (popupTimerRef.current) clearInterval(popupTimerRef.current);
    };
  }, []);

  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const t = event.data?.type;
      if (t !== "codex-oauth-callback" && t !== "provider-oauth-callback") return;
      if (event.data.provider && event.data.provider !== providerId) return;
      if (popupTimerRef.current) { clearInterval(popupTimerRef.current); popupTimerRef.current = null; }
      if (event.data.error) {
        setOauthBusy(false); setOauthMode(null); setError(event.data.error);
        return;
      }
      const { code, state: cbState } = event.data;
      if (code && cbState) {
        providerOAuthExchange(providerId, code, cbState)
          .then((result) => {
            setStatus({
              status: "connected", provider: providerId,
              account_id: result.account_id, plan_type: result.plan_type,
              expires_at: result.expires_at, is_active: true, has_refresh_token: true,
            });
            onProfileCreated?.();
          })
          .catch((e: unknown) => setError(e instanceof Error ? e.message : "OAuth 交换失败"))
          .finally(() => { setOauthBusy(false); setOauthMode(null); setOauthState(""); });
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [providerId, onProfileCreated]);

  useEffect(() => {
    if (status?.status === "connected" && !selectedModel && defaultModel) {
      setSelectedModel(defaultModel.public_id);
    }
  }, [status, selectedModel, defaultModel]);

  // ── OAuth PKCE ──
  const handleOAuth = useCallback(async () => {
    if (oauthBusy || !flows.includes("pkce")) return;
    setOauthBusy(true); setPasteUrl(""); setError("");
    try {
      const isLocal = ["localhost", "127.0.0.1"].includes(window.location.hostname) && window.location.port === "1455";
      const providerCallbackPaths: Record<string, string> = {
        "google-gemini": "/auth/gemini/callback",
      };
      const callbackPath = providerCallbackPaths[providerId] || "/auth/callback";
      const redirectUri = isLocal ? `${window.location.origin}${callbackPath}` : undefined;
      const data = await providerOAuthStart(providerId, redirectUri);
      setOauthState(data.state); setOauthMode(data.mode);
      const w = 600, h = 700;
      const left = window.screenX + (window.outerWidth - w) / 2;
      const top = window.screenY + (window.outerHeight - h) / 2;
      const popup = window.open(data.authorize_url, `${providerId}-oauth`, `width=${w},height=${h},left=${left},top=${top},toolbar=no,menubar=no`);
      popupRef.current = popup;
      if (data.mode === "popup" && popup) {
        popupTimerRef.current = setInterval(() => {
          if (popup.closed) {
            if (popupTimerRef.current) { clearInterval(popupTimerRef.current); popupTimerRef.current = null; }
            setTimeout(() => setOauthBusy((busy) => { if (busy) { setOauthMode(null); setOauthState(""); return false; } return busy; }), 2000);
          }
        }, 500);
      }
    } catch (e) {
      setOauthBusy(false); setOauthMode(null);
      setError(e instanceof Error ? e.message : "无法发起 OAuth 登录");
    }
  }, [oauthBusy, providerId, flows]);

  const handlePasteSubmit = useCallback(async () => {
    if (!pasteUrl.trim() || !oauthState) return;
    setError("");
    try {
      const url = new URL(pasteUrl.trim());
      const code = url.searchParams.get("code");
      const state = url.searchParams.get("state");
      if (!code || !state) { setError("URL 中缺少 code 或 state 参数"); return; }
      const result = await providerOAuthExchange(providerId, code, state);
      setStatus({ status: "connected", provider: providerId, account_id: result.account_id, plan_type: result.plan_type, expires_at: result.expires_at, is_active: true, has_refresh_token: true });
      onProfileCreated?.();
    } catch (e) { setError(e instanceof Error ? e.message : "连接失败"); }
    finally { setOauthBusy(false); setOauthMode(null); setOauthState(""); setPasteUrl(""); }
  }, [pasteUrl, oauthState, providerId, onProfileCreated]);

  const cancelOAuth = useCallback(() => {
    if (popupRef.current && !popupRef.current.closed) popupRef.current.close();
    if (popupTimerRef.current) { clearInterval(popupTimerRef.current); popupTimerRef.current = null; }
    setOauthBusy(false); setOauthMode(null); setOauthState(""); setPasteUrl(""); setError("");
  }, []);

  // ── Device Code ──
  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setAuthorizing(false); setDeviceState(null); setUserCode(""); setVerificationUrl("");
  }, []);

  const handleDeviceCode = useCallback(async () => {
    if (authorizing || !flows.includes("device_code")) return;
    setAuthorizing(true); setError("");
    try {
      const data = await providerDeviceCodeStart(providerId);
      setUserCode(data.user_code); setVerificationUrl(data.verification_url); setDeviceState(data.state);
      const interval = Math.max(data.interval, 3) * 1000;
      pollRef.current = setInterval(async () => {
        try {
          const result = await providerDeviceCodePoll(providerId, data.state);
          if (result.status === "connected") { stopPolling(); fetchProviderStatus(providerId).then(setStatus).catch(() => {}); onProfileCreated?.(); }
        } catch { /* polling errors don't interrupt */ }
      }, interval);
      setTimeout(() => { if (pollRef.current) { stopPolling(); setError("设备码已过期，请重试"); } }, 15 * 60 * 1000);
    } catch (e) { setAuthorizing(false); setError(e instanceof Error ? e.message : "无法发起设备码登录"); }
  }, [authorizing, providerId, flows, stopPolling, onProfileCreated]);

  // ── Manual paste ──
  const handleTokenConnect = useCallback(async () => {
    const raw = tokenInput.trim();
    if (!raw || connecting) return;
    setConnecting(true); setError("");
    try {
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      const result = await connectProvider(providerId, parsed);
      setStatus({ status: "connected", provider: providerId, account_id: result.account_id, plan_type: result.plan_type, expires_at: result.expires_at, is_active: true, has_refresh_token: true });
      setTokenInput(""); setShowManualPaste(false); onProfileCreated?.();
    } catch (e) {
      if (e instanceof SyntaxError) setError("JSON 格式无效");
      else setError(e instanceof Error ? e.message : "连接失败");
    } finally { setConnecting(false); }
  }, [tokenInput, connecting, providerId, onProfileCreated]);

  const handleDisconnect = useCallback(async () => {
    if (disconnecting) return;
    setDisconnecting(true); setError("");
    try { await disconnectProvider(providerId); setStatus({ status: "disconnected", provider: providerId }); }
    catch (e) { setError(e instanceof Error ? e.message : "断开连接失败"); }
    finally { setDisconnecting(false); }
  }, [disconnecting, providerId]);

  const handleRefresh = useCallback(async () => {
    if (refreshingToken) return;
    setRefreshingToken(true); setError("");
    try {
      const result = await refreshProviderToken(providerId);
      setStatus((prev) => prev ? { ...prev, status: "connected", expires_at: result.expires_at } : prev);
    } catch (e) { setError(e instanceof Error ? e.message : "刷新 token 失败"); }
    finally { setRefreshingToken(false); }
  }, [refreshingToken, providerId]);

  const handleApplyModel = useCallback(() => {
    const model = availableModels.find((m) => m.public_id === selectedModel) || defaultModel;
    if (model) onModelSelected?.(model);
  }, [selectedModel, availableModels, defaultModel, onModelSelected]);

  const isConnected = status?.status === "connected" || status?.status === "expired";

  return (
    <div className="border rounded-lg p-3 space-y-2">
      {/* Header */}
      <button
        type="button"
        className="w-full flex items-center gap-2 text-left"
        onClick={() => { if (!loading && isConnected) setCollapsed((v) => !v); }}
      >
        <ProviderLogo id={providerId} />
        <p className="text-xs font-semibold">{descriptor.label} 订阅登录</p>
        {!loading && isConnected && status?.plan_type && (
          <span className="text-[10px] text-muted-foreground capitalize">{status.plan_type}</span>
        )}
        <Badge variant="secondary" className="text-[10px] ml-auto">
          {loading ? "检测中" : status?.status === "connected" ? "已连接" : status?.status === "expired" ? "已过期" : "未连接"}
        </Badge>
        {!loading && isConnected && (
          collapsed ? <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0" /> : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
        )}
      </button>

      {loading && (
        <div className="flex items-center justify-center py-3">
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Connected / Expired state */}
      {!loading && !collapsed && isConnected && (
        <>
          <div className="flex items-center gap-2 mb-1">
            <div className={`h-2 w-2 rounded-full ${status!.status === "expired" ? "bg-amber-500" : "bg-green-500"}`} />
            <span className="text-xs font-medium">{status!.status === "expired" ? "Token 已过期" : "已连接"}</span>
          </div>
          <div className="grid grid-cols-2 gap-y-1 text-[11px]">
            {status!.account_id && (<><span className="text-muted-foreground">账户</span><span className="truncate">{status!.account_id}</span></>)}
            {status!.plan_type && (<><span className="text-muted-foreground">计划</span><span className="capitalize">{status!.plan_type}</span></>)}
            {status!.expires_at && (<><span className="text-muted-foreground">过期</span><span>{new Date(status!.expires_at).toLocaleString()}</span></>)}
          </div>

          {/* Model selector */}
          {descriptor.models.length > 0 && (
            <div className="flex items-center gap-1.5 mt-2">
              <select
                className="flex-1 h-7 rounded border bg-background px-2 text-xs"
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
              >
                {availableModels.map((m) => (
                  <option key={m.public_id} value={m.public_id}>{m.display_name}</option>
                ))}
              </select>
              <Button size="sm" variant="secondary" className="h-7 text-xs" onClick={handleApplyModel}>
                使用此模型
              </Button>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-1.5 mt-2">
            <Button size="sm" variant="outline" className="h-7 text-[11px]" disabled={refreshingToken} onClick={handleRefresh}>
              <RefreshCw className={`h-3 w-3 mr-1 ${refreshingToken ? "animate-spin" : ""}`} />刷新
            </Button>
            <Button size="sm" variant="outline" className="h-7 text-[11px] text-destructive" disabled={disconnecting} onClick={handleDisconnect}>
              <LogOut className="h-3 w-3 mr-1" />断开
            </Button>
          </div>
        </>
      )}

      {/* Disconnected state - connection options */}
      {!loading && status?.status === "disconnected" && (
        <div className="space-y-2">
          {/* OAuth PKCE */}
          {flows.includes("pkce") && !authorizing && !oauthBusy && (
            <Button size="sm" className="w-full h-8 text-xs" onClick={handleOAuth}>
              <ExternalLink className="h-3 w-3 mr-1.5" />浏览器登录
            </Button>
          )}

          {/* OAuth PKCE in progress */}
          {oauthBusy && (
            <div className="space-y-2">
              {oauthMode === "popup" && (
                <div className="text-xs text-muted-foreground text-center">在弹出窗口中完成授权...</div>
              )}
              {oauthMode === "paste" && (
                <div className="space-y-1.5">
                  <p className="text-xs text-muted-foreground">请复制浏览器地址栏中的完整 URL 粘贴到下方：</p>
                  <Textarea className="h-16 text-xs" value={pasteUrl} onChange={(e) => setPasteUrl(e.target.value)} placeholder="粘贴回调 URL..." />
                  <Button size="sm" className="w-full h-7 text-xs" disabled={!pasteUrl.trim()} onClick={handlePasteSubmit}>提交</Button>
                </div>
              )}
              <Button size="sm" variant="ghost" className="w-full h-7 text-xs" onClick={cancelOAuth}>取消</Button>
            </div>
          )}

          {/* Device Code */}
          {flows.includes("device_code") && !oauthBusy && !authorizing && (
            <Button size="sm" variant="outline" className="w-full h-8 text-xs" onClick={handleDeviceCode}>
              <Key className="h-3 w-3 mr-1.5" />设备码登录
            </Button>
          )}

          {authorizing && (
            <div className="space-y-2 text-center">
              <p className="text-xs font-medium">设备码: <code className="bg-muted px-1.5 py-0.5 rounded text-sm font-bold">{userCode}</code></p>
              {verificationUrl && (
                <a href={verificationUrl} target="_blank" rel="noopener noreferrer" className="text-xs text-primary hover:underline inline-flex items-center gap-1">
                  打开验证页面 <ExternalLink className="h-3 w-3" />
                </a>
              )}
              <div className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" />等待授权...
              </div>
              <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={stopPolling}>取消</Button>
            </div>
          )}

          {/* Manual paste */}
          {flows.includes("token_paste") && !oauthBusy && !authorizing && (
            <>
              {!showManualPaste && (
                <Button size="sm" variant="ghost" className="w-full h-7 text-[11px] text-muted-foreground" onClick={() => setShowManualPaste(true)}>
                  手动粘贴 Token
                </Button>
              )}
              {showManualPaste && (
                <div className="space-y-1.5">
                  <Textarea className="h-20 text-xs font-mono" value={tokenInput} onChange={(e) => setTokenInput(e.target.value)} placeholder="粘贴 auth.json 内容..." />
                  <div className="flex gap-1.5">
                    <Button size="sm" className="flex-1 h-7 text-xs" disabled={!tokenInput.trim() || connecting} onClick={handleTokenConnect}>
                      {connecting ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}连接
                    </Button>
                    <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => { setShowManualPaste(false); setTokenInput(""); }}>取消</Button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
