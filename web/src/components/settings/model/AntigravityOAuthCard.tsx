"use client";

import { useState } from "react";
import {
  Loader2, ExternalLink, ChevronRight, ChevronDown,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  subscriptionOAuthStart,
  subscriptionOAuthExchange,
} from "@/lib/auth-api";
import { SubscriptionAccountCard } from "./SubscriptionAccountCard";
import { useSubscriptionProvider } from "./useSubscriptionProvider";
import type { ProfileEntry } from "./types";
import { useOAuthLogin } from "./useSubscriptionLogin";
import { OAuthLoginProgress } from "./OAuthLoginProgress";
import { ANTIGRAVITY_OAUTH_PRESET } from "./constants";

const PROVIDER = "antigravity";
const GOOGLE_AUTH_ORIGIN = "https://accounts.google.com";

function assertGoogleAuthUrl(value: string): string {
  const parsed = new URL(value);
  if (
    parsed.protocol !== "https:"
    || parsed.origin !== GOOGLE_AUTH_ORIGIN
    || parsed.username
    || parsed.password
  ) {
    throw new Error("授权地址无效");
  }
  return value;
}

export function AntigravityOAuthCard({
  profiles,
}: {
  profiles: ProfileEntry[];
}) {
  const [modelsExpanded, setModelsExpanded] = useState(false);
  const controller = useSubscriptionProvider(PROVIDER, profiles, ANTIGRAVITY_OAUTH_PRESET);
  const {
    status, loading, statusError, reloadStatus, completeLogin, error, setError,
    tokenInput, setTokenInput, connecting, showManual, setShowManual, disconnecting, refreshing,
    addingModel, removingModel, providerProfiles, profileForModel,
    handleManualConnect, handleDisconnect, handleRefresh, handleAddModel, handleRemoveModel,
    catalog, catalogLoading, catalogError, loadCatalog,
  } = controller;
  const oauth = useOAuthLogin({
    lock: controller.lock,
    name: "antigravity-oauth", messageType: "antigravity-oauth-callback",
    start: () => subscriptionOAuthStart(PROVIDER), validateUrl: assertGoogleAuthUrl,
    exchange: (code, state) => subscriptionOAuthExchange(PROVIDER, code, state),
    onConnected: completeLogin, onError: setError,
  });
  const oauthBusy = oauth.busy;
  const handleOAuthLogin = oauth.start;
  const busy = oauthBusy || controller.busy;
  const isConnected = status?.status === "connected";
  const isExpired = status?.status === "expired";

  return (
    <SubscriptionAccountCard
      provider={PROVIDER} title="Google Antigravity" description="使用 Google Antigravity / Cloud Code Assist 订阅" account={status?.email}
      status={status?.status} loading={loading} busy={busy} modelCount={providerProfiles.length}
      error={error} statusError={statusError} onRetry={reloadStatus}
    >
      {/* ── Connected / Expired ── */}
      {(isConnected || isExpired) && (
        <>
          <div className="flex items-center gap-2">
            <div className={`h-2 w-2 rounded-full ${isExpired ? "bg-amber-500" : "bg-green-500"}`} />
            <span className="text-xs font-medium">{isExpired ? "Token 已过期" : "已连接"}</span>
          </div>
          <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-xs">
            {status?.email && (<><span className="text-muted-foreground">邮箱</span><span className="truncate">{status.email}</span></>)}
            {status?.account_id && (<><span className="text-muted-foreground">账户</span><span className="font-mono truncate">{status.account_id}</span></>)}
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
                {providerProfiles.length}/{catalog.length || "…"}
              </Badge>
            </button>
            {modelsExpanded && (
              <>
                <p className="text-[11px] text-muted-foreground">添加后可在模型选择器中使用；移除仅删除该模型配置。</p>
                {catalogError && <p role="alert" className="text-xs text-destructive">{catalogError} <button type="button" className="underline" onClick={loadCatalog}>重新获取</button></p>}
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
                      const existing = profileForModel(m);
                      const isAdded = Boolean(existing);
                      const isBusy = addingModel === profileName || removingModel === existing?.name;
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
                              onClick={() => handleRemoveModel(existing!.name)}
                              disabled={busy}
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
      {(!loading || status) && !isConnected && (
        <>
          <p className="text-xs text-muted-foreground leading-relaxed">
            使用 Google Antigravity / Cloud Code Assist 订阅（Claude、Gemini、GPT-OSS），无需 API Key。登录后自动创建模型档案。
          </p>

          {!oauthBusy ? (
            <Button
              size="sm"
              className="w-full h-10 text-xs text-white font-medium gap-1.5"
              style={{ backgroundColor: "var(--em-primary)" }}
              onClick={handleOAuthLogin}
              disabled={busy}
            >
              <ExternalLink className="h-3 w-3" />
              使用 Google 账号登录
            </Button>
          ) : (
            <OAuthLoginProgress login={oauth} />
          )}

          {/* 备选方式：手动粘贴 */}
          {!oauthBusy && <div>
            <button type="button" disabled={connecting} aria-expanded={showManual} onClick={() => setShowManual(!showManual)} className="text-[11px] text-muted-foreground hover:text-foreground transition-colors">
              {showManual ? "▾ 收起手动粘贴" : "▸ 导入已有凭证（高级）"}
            </button>
            {showManual && (
              <div className="mt-1 space-y-1">
                <div className="text-[11px] text-muted-foreground space-y-0.5">
                  <p>粘贴 CPA <code className="px-0.5 rounded bg-background font-mono">antigravity-*.json</code> 或包含 access_token / refresh_token / project_id 的 JSON</p>
                </div>
                <textarea aria-label="已有账号凭证 JSON" autoComplete="off" spellCheck={false} disabled={busy}
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  className="w-full h-14 rounded-md border border-input bg-background px-2 py-1 text-[11px] font-mono resize-none"
                  placeholder='{"access_token":"...","refresh_token":"...","project_id":"..."}'
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
