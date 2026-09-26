"use client";

import { useId, useState, type ReactNode } from "react";
import { AlertCircle, ArrowRight, CheckCircle2, ChevronDown, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProviderLogo } from "./ProviderLogo";
import { requestModelSubTab } from "./model-subtab";

export function SubscriptionAccountCard({ provider, title, description, status, loading, busy, account, modelCount, error, statusError, onRetry, children }: {
  provider: string;
  title: string;
  description: string;
  status?: "connected" | "expired" | "disconnected";
  loading: boolean;
  busy: boolean;
  account?: string;
  modelCount: number;
  error: string;
  statusError: boolean;
  onRetry: () => void;
  children: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const connected = status === "connected";
  const expired = status === "expired";
  const label = loading ? "检查中" : busy ? "处理中" : statusError ? "状态未知" : expired ? "需重新登录" : connected ? "已连接" : "未连接";
  return (
    <section className={`em-subscription-card overflow-hidden rounded-xl border bg-background transition-colors ${expanded ? "border-primary/35 shadow-sm" : "border-border/80"}`}>
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={id}
        onClick={() => setExpanded(!expanded)}
        className="em-subscription-card-header w-full p-3 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:p-4"
      >
        <span className="flex size-10 shrink-0 items-center justify-center rounded-xl border border-border/60 bg-muted/40"><ProviderLogo id={provider} className="size-5" /></span>
        <span className="em-subscription-card-copy min-w-0 flex-1">
          <span className="em-subscription-card-title block text-[13px] font-semibold">{title}</span>
          <span className="em-subscription-card-desc text-xs text-muted-foreground break-words">{connected ? (account || "账号已连接") : description}</span>
          <span className="em-subscription-card-meta block text-[11px] text-muted-foreground">{connected ? `已添加 ${modelCount} 个模型` : ""}</span>
        </span>
        <span className="em-subscription-card-status flex shrink-0 items-center gap-2 whitespace-nowrap">
          <span className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] ${expired || statusError ? "bg-amber-500/10 text-amber-700 dark:text-amber-400" : connected ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : "bg-muted text-muted-foreground"}`}>
            {(loading || busy) ? <Loader2 className="size-3 animate-spin" /> : connected ? <CheckCircle2 className="size-3" /> : expired || statusError ? <AlertCircle className="size-3" /> : null}
            {label}
          </span>
        </span>
        <ChevronDown className={`em-subscription-card-chevron size-4 text-muted-foreground transition-transform ${expanded ? "rotate-180" : ""}`} />
      </button>
      <div id={id} hidden={!expanded} className="space-y-3 border-t border-border/60 p-3 sm:p-4">
        {loading && !status && <p className="text-xs text-muted-foreground" role="status">正在检查账号状态…</p>}
        {statusError && (
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className="text-muted-foreground">暂时无法读取账号状态，请重试。</span>
            <Button size="sm" variant="outline" onClick={onRetry}>重新检查</Button>
          </div>
        )}
        {status && <>
          {connected && <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-emerald-500/5 p-3 text-xs">
            <span className="text-emerald-700 dark:text-emerald-400">账号已连接，可在下方管理可用模型。</span>
            <Button size="sm" variant="ghost" className="h-8 gap-1 text-xs" onClick={() => requestModelSubTab("roles")} disabled={busy}>配置模型 <ArrowRight className="size-3" /></Button>
          </div>}
          {expired && <p role="status" className="rounded-lg bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-700 dark:text-amber-400">授权已过期。请重新登录，或尝试刷新授权；已有模型配置会保留。</p>}
          {children}
        </>}
        {error && <p role="alert" className="rounded-lg bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive break-words">{error}</p>}
      </div>
    </section>
  );
}
