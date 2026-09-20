"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  Loader2, ExternalLink, ChevronRight, ChevronDown, Lock,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { apiPost, apiDelete } from "@/lib/api";
import {
  fetchCodexStatus,
  codexOAuthStart,
  codexOAuthExchange,
  codexDeviceCodeStart,
  codexDeviceCodePoll,
  connectCodex,
  disconnectCodex,
  refreshCodexToken,
  type CodexStatus,
} from "@/lib/auth-api";
import { ProviderLogo } from "./ProviderLogo";
import { CODEX_OAUTH_PRESET, CODEX_MODELS } from "./constants";
import type { CodexModelEntry } from "./types";

const CODEX_AUTH_ORIGIN = "https://auth.openai.com";
const CODEX_CALLBACK_PATH = "/auth/callback";
const CODEX_LOOPBACK_ORIGIN = "http://localhost:1455";

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
  const [status, setStatus] = useState<CodexStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // OAuth PKCE
  const [oauthBusy, setOauthBusy] = useState(false);
  const [oauthState, setOauthState] = useState("");
  const [pasteUrl, setPasteUrl] = useState("");
  const [oauthMode, setOauthMode] = useState<"popup" | "paste" | null>(null);
  const popupRef = useRef<Window | null>(null);
  const popupTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Device Code
  const [userCode, setUserCode] = useState("");
  const [verificationUrl, setVerificationUrl] = useState("");
  const [authorizing, setAuthorizing] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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
  const [cardCollapsed, setCardCollapsed] = useState(true);

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

  useEffect(() => {
    let cancelled = false;
    fetchCodexStatus()
      .then((data) => { if (!cancelled) setStatus(data); })
      .catch(() => { if (!cancelled) setStatus({ status: "disconnected", provider: "openai-codex" }); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (popupTimerRef.current) clearInterval(popupTimerRef.current);
    };
  }, []);

  // postMessage listener for popup auto-callback
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.origin !== window.location.origin && event.origin !== CODEX_LOOPBACK_ORIGIN) return;
      if (event.data?.type !== "codex-oauth-callback") return;
      if (popupTimerRef.current) { clearInterval(popupTimerRef.current); popupTimerRef.current = null; }
      if (event.data.error) {
        setOauthBusy(false); setOauthMode(null); setError(event.data.error);
        return;
      }
      const { code, state: cbState } = event.data;
      if (code && cbState && cbState === oauthState) {
        codexOAuthExchange(code, cbState)
          .then(() => {
            onProfileCreated();
            return fetchCodexStatus().then(setStatus);
          })
          .catch((e: unknown) => { setError(e instanceof Error ? e.message : "OAuth 交换失败"); })
          .finally(() => { setOauthBusy(false); setOauthMode(null); setOauthState(""); });
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [oauthState, onProfileCreated]);

  const handleOAuthLogin = useCallback(async () => {
    if (oauthBusy) return;
    setOauthBusy(true); setPasteUrl(""); setError("");
    try {
      const isLocal = ["localhost", "127.0.0.1"].includes(window.location.hostname);
      const redirectUri = isLocal ? `${window.location.origin}${CODEX_CALLBACK_PATH}` : undefined;
      const data = await codexOAuthStart(redirectUri);
      const authorizeUrl = assertCodexAuthUrl(data.authorize_url);
      setOauthState(data.state); setOauthMode(data.mode);
      const w = 600, h = 700;
      const left = window.screenX + (window.outerWidth - w) / 2;
      const top = window.screenY + (window.outerHeight - h) / 2;
      const popup = window.open(authorizeUrl, "codex-oauth", `width=${w},height=${h},left=${left},top=${top},toolbar=no,menubar=no`);
      popupRef.current = popup;
      if (!popup) {
        setOauthMode("paste");
        return;
      }
      if (data.mode === "popup" && popup) {
        popupTimerRef.current = setInterval(() => {
          if (popup.closed) {
            if (popupTimerRef.current) { clearInterval(popupTimerRef.current); popupTimerRef.current = null; }
            setTimeout(() => { setOauthBusy((b) => { if (b) { setOauthMode(null); setOauthState(""); return false; } return b; }); }, 2000);
          }
        }, 500);
      }
    } catch (e) {
      setOauthBusy(false); setOauthMode(null);
      setError(e instanceof Error ? e.message : "无法发起 OAuth 登录");
    }
  }, [oauthBusy]);

  const handlePasteUrlSubmit = useCallback(async () => {
    if (!pasteUrl.trim() || !oauthState) return;
    setError("");
    try {
      const url = new URL(pasteUrl.trim());
      const code = url.searchParams.get("code");
      const state = url.searchParams.get("state");
      if (!code || !state) { setError("URL 中缺少 code 或 state 参数"); return; }
      await codexOAuthExchange(code, state);
      onProfileCreated();
      const next = await fetchCodexStatus();
      setStatus(next);
    } catch (e) { setError(e instanceof Error ? e.message : "连接失败"); }
    finally { setOauthBusy(false); setOauthMode(null); setOauthState(""); setPasteUrl(""); }
  }, [pasteUrl, oauthState, onProfileCreated]);

  const cancelOAuth = useCallback(() => {
    if (popupRef.current && !popupRef.current.closed) popupRef.current.close();
    if (popupTimerRef.current) { clearInterval(popupTimerRef.current); popupTimerRef.current = null; }
    setOauthBusy(false); setOauthMode(null); setOauthState(""); setPasteUrl(""); setError("");
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setAuthorizing(false); setUserCode(""); setVerificationUrl("");
  }, []);

  const handleDeviceCode = useCallback(async () => {
    if (authorizing) return;
    setAuthorizing(true); setError("");
    try {
      const data = await codexDeviceCodeStart();
      setUserCode(data.user_code); setVerificationUrl(data.verification_url);
      const interval = Math.max(data.interval, 3) * 1000;
      pollRef.current = setInterval(async () => {
        try {
          const result = await codexDeviceCodePoll(data.state);
          if (result.status === "connected") {
            stopPolling();
            onProfileCreated();
            fetchCodexStatus().then(setStatus).catch(() => {});
          }
        } catch (e) {
          const msg = e instanceof Error ? e.message : "轮询授权状态失败";
          if (/无效|不匹配|过期|Token 交换/.test(msg)) {
            stopPolling();
            setError(msg);
          }
        }
      }, interval);
      const expiresMs = Math.max((data.expires_in ?? 15 * 60) * 1000, 30_000);
      setTimeout(() => { if (pollRef.current) { stopPolling(); setError("设备码已过期，请重试"); } }, expiresMs);
    } catch (e) { setAuthorizing(false); setError(e instanceof Error ? e.message : "无法发起设备码登录"); }
  }, [authorizing, onProfileCreated, stopPolling]);

  const handleManualConnect = useCallback(async () => {
    if (!tokenInput.trim() || connecting) return;
    setConnecting(true); setError("");
    try {
      const parsed = JSON.parse(tokenInput.trim());
      await connectCodex(parsed);
      onProfileCreated();
      const next = await fetchCodexStatus();
      setStatus(next);
      setTokenInput(""); setShowManual(false);
    } catch (e) {
      setError(e instanceof SyntaxError ? "JSON 格式无效" : (e instanceof Error ? e.message : "连接失败"));
    } finally { setConnecting(false); }
  }, [tokenInput, connecting, onProfileCreated]);

  const handleDisconnect = useCallback(async () => {
    if (disconnecting) return;
    setDisconnecting(true); setError("");
    try { await disconnectCodex(); setStatus({ status: "disconnected", provider: "openai-codex" }); }
    catch (e) { setError(e instanceof Error ? e.message : "断开失败"); }
    finally { setDisconnecting(false); }
  }, [disconnecting]);

  const handleRefresh = useCallback(async () => {
    if (refreshing) return;
    setRefreshing(true); setError("");
    try {
      const result = await refreshCodexToken();
      setStatus((prev) => prev ? { ...prev, status: "connected", expires_at: result.expires_at } : prev);
    } catch (e) { setError(e instanceof Error ? e.message : "刷新失败"); }
    finally { setRefreshing(false); }
  }, [refreshing]);

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
    <div
      className={`rounded-lg border px-2.5 py-2.5 space-y-2 ${
        isConnected
          ? "border-green-500/40 bg-green-500/5"
          : "border-[var(--em-primary)]/30 bg-[var(--em-primary)]/5"
      }`}
    >
      {/* Header — clickable to toggle collapse when connected */}
      <button
        type="button"
        className="w-full flex items-center gap-1.5 text-left"
        onClick={() => { if (!loading && (isConnected || isExpired)) setCardCollapsed((v) => !v); }}
      >
        <ProviderLogo id={CODEX_OAUTH_PRESET.id} />
        <p className="text-xs font-semibold">GPT Codex 订阅登录</p>
        {!loading && (isConnected || isExpired) && status?.plan_type && (
          <span className="text-[10px] text-muted-foreground capitalize">{status.plan_type}</span>
        )}
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
              onClick={() => setCodexModelsExpanded((v) => !v)}
            >
              {codexModelsExpanded ? (
                <ChevronDown className="h-3 w-3 text-muted-foreground" />
              ) : (
                <ChevronRight className="h-3 w-3 text-muted-foreground" />
              )}
              <span className="text-[10px] text-muted-foreground">可用 Codex 模型（点击展开）</span>
              <Badge variant="secondary" className="text-[9px] ml-auto">
                {existingCodexProfiles.size}/{CODEX_MODELS.length}
              </Badge>
            </button>
            {codexModelsExpanded && (
              <>
                <p className="text-[10px] text-muted-foreground">点击添加/移除</p>
                <div className="space-y-1">
                  {CODEX_MODELS.map((m) => {
                    const isProLocked = m.proOnly && status?.plan_type !== "pro";
                    const isAdded = existingCodexProfiles.has(m.profileName);
                    const isBusy = addingModel === m.profileName || removingModel === m.profileName;
                    return (
                      <div
                        key={m.profileName}
                        className={`flex items-center gap-2 rounded-md border px-2 py-1 text-[11px] transition-colors ${
                          isProLocked
                            ? "border-border/50 bg-muted/30 opacity-50"
                            : isAdded
                              ? "border-green-500/40 bg-green-500/5"
                              : "border-border hover:border-[var(--em-primary)]/40"
                        }`}
                      >
                        <span className="font-medium truncate min-w-0 flex-1">{m.displayName}</span>
                        <code className="text-[9px] font-mono text-muted-foreground hidden sm:inline truncate max-w-[30%]">{m.modelId}</code>
                        {m.proOnly && <Badge variant="secondary" className="text-[9px] shrink-0">Pro</Badge>}
                        {isProLocked ? (
                          <Lock className="h-3 w-3 text-muted-foreground shrink-0" />
                        ) : isAdded ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-5 px-1.5 text-[10px] text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950 shrink-0"
                            onClick={() => handleRemoveCodexModel(m.profileName)}
                            disabled={isBusy}
                          >
                            {isBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : "移除"}
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-5 px-1.5 text-[10px] shrink-0"
                            style={{ color: "var(--em-primary)" }}
                            onClick={() => handleAddCodexModel(m)}
                            disabled={isBusy}
                          >
                            {isBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : "添加"}
                          </Button>
                        )}
                      </div>
                    );
                  })}
                </div>
                {hasLegacyCodexProfile && (
                  <p className="text-[10px] text-amber-600 dark:text-amber-400 mt-1">
                    检测到旧版 codex-oauth 档案，建议删除后使用上方按钮重新添加
                  </p>
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
            使用 ChatGPT Plus/Pro 订阅，无需 API Key。登录后自动创建模型档案。
          </p>

          {!oauthBusy ? (
            <Button
              size="sm"
              className="w-full h-8 text-xs text-white font-medium gap-1.5"
              style={{ backgroundColor: "var(--em-primary)" }}
              onClick={handleOAuthLogin}
              disabled={authorizing}
            >
              <ExternalLink className="h-3 w-3" />
              使用 ChatGPT 账号登录
            </Button>
          ) : (
            <div className="space-y-2 rounded-md border border-border bg-muted/30 p-2.5">
              {oauthMode === "popup" ? (
                <div className="text-center space-y-1">
                  <Loader2 className="h-5 w-5 mx-auto animate-spin text-muted-foreground" />
                  <p className="text-[11px] text-muted-foreground">请在弹出窗口中完成 OpenAI 登录...</p>
                  <p className="text-[10px] text-muted-foreground">授权完成后自动更新</p>
                </div>
              ) : (
                <div className="space-y-1.5">
                  <p className="text-[11px] text-muted-foreground">登录完成后，复制地址栏 URL 粘贴到下方：</p>
                  <p className="text-[10px] text-amber-600 dark:text-amber-400">提示：页面可能显示无法访问，直接复制地址栏 URL 即可</p>
                  <Input value={pasteUrl} onChange={(e) => setPasteUrl(e.target.value)} className="h-7 text-[11px] font-mono" placeholder="http://localhost:1455/auth/callback?code=...&state=..." autoFocus />
                  <Button size="sm" className="w-full h-7 text-[11px] text-white" style={{ backgroundColor: "var(--em-primary)" }} onClick={handlePasteUrlSubmit} disabled={!pasteUrl.trim()}>
                    确认连接
                  </Button>
                </div>
              )}
              <div className="flex justify-center">
                <button type="button" className="text-[10px] text-muted-foreground hover:text-foreground transition-colors" onClick={cancelOAuth}>取消</button>
              </div>
            </div>
          )}

          {/* 备选方式 */}
          <div>
            <button type="button" onClick={() => setShowFallback(!showFallback)} className="text-[10px] text-muted-foreground hover:text-foreground transition-colors">
              {showFallback ? "▾ 收起备选方式" : "▸ 其他连接方式"}
            </button>
            {showFallback && (
              <div className="mt-1.5 space-y-2.5">
                {/* Device Code */}
                <div className="space-y-1">
                  <p className="text-[10px] font-medium text-muted-foreground">设备码登录</p>
                  {!authorizing ? (
                    <Button size="sm" variant="outline" className="w-full h-7 text-[11px]" onClick={handleDeviceCode} disabled={oauthBusy}>使用设备码登录</Button>
                  ) : (
                    <div className="space-y-1.5 rounded-md border border-border bg-muted/30 p-2">
                      <p className="text-[10px] text-muted-foreground text-center">打开链接并输入验证码：</p>
                      <a href={verificationUrl} target="_blank" rel="noopener noreferrer" className="block text-[10px] font-medium underline text-center" style={{ color: "var(--em-primary)" }}>{verificationUrl}</a>
                      <div className="flex justify-center">
                        <span className="font-mono text-base font-bold tracking-widest select-all px-2 py-0.5 rounded border border-border bg-background cursor-pointer" onClick={() => navigator.clipboard.writeText(userCode)}>{userCode}</span>
                      </div>
                      <div className="flex items-center justify-center gap-2">
                        <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                        <span className="text-[10px] text-muted-foreground">等待授权...</span>
                        <button type="button" className="text-[10px] text-muted-foreground hover:text-foreground" onClick={stopPolling}>取消</button>
                      </div>
                    </div>
                  )}
                </div>
                {/* Manual paste */}
                <div>
                  <button type="button" onClick={() => setShowManual(!showManual)} className="text-[10px] text-muted-foreground hover:text-foreground transition-colors">
                    {showManual ? "▾ 收起手动粘贴" : "▸ 手动粘贴 Token"}
                  </button>
                  {showManual && (
                    <div className="mt-1 space-y-1">
                      <div className="text-[10px] text-muted-foreground space-y-0.5">
                        <p>1. 运行 <code className="px-0.5 rounded bg-background font-mono">codex login</code></p>
                        <p>2. 复制 <code className="px-0.5 rounded bg-background font-mono">~/.codex/auth.json</code></p>
                      </div>
                      <textarea value={tokenInput} onChange={(e) => setTokenInput(e.target.value)} className="w-full h-14 rounded-md border border-input bg-background px-2 py-1 text-[10px] font-mono resize-none" placeholder='{"token":"...","refresh_token":"..."}' />
                      <Button size="sm" variant="outline" className="h-6 text-[10px]" onClick={handleManualConnect} disabled={connecting || !tokenInput.trim()}>
                        {connecting ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}粘贴连接
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {error ? <p className="text-[10px] text-destructive">{error}</p> : null}
    </div>
  );
}
