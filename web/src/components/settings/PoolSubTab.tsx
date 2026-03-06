"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Database, Zap, Activity, HeartPulse, RefreshCw, Loader2,
  AlertCircle, CheckCircle, ChevronDown, Plus, Wifi, WifiOff,
  X, Save, Copy, Check, Trash2, ExternalLink,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  type PoolAccountSummary, type ProbeResult, type PoolManualActive,
  type ImportableSubscription,
  fetchPoolSummary, importPoolAccount, updatePoolAccount,
  deletePoolAccount, probePoolAccount, setManualActive,
  fetchManualActive, deleteManualActive,
  fetchImportableSubscriptions, importPoolAccountFromSubscription,
} from "@/lib/pool-api";

function fmtTok(n: number) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K`;
  return String(n);
}
function fmtDate(iso: string) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }
  catch { return iso; }
}

const S_CFG: Record<string, { l: string; c: string }> = {
  active: { l: "活跃", c: "text-green-600 dark:text-green-400" },
  disabled: { l: "禁用", c: "text-muted-foreground" },
  depleted: { l: "耗尽", c: "text-red-600 dark:text-red-400" },
};
const H_CFG: Record<string, { l: string; c: string; d: string }> = {
  ok: { l: "正常", c: "text-green-600 dark:text-green-400", d: "bg-green-500" },
  rate_limited: { l: "限流", c: "text-amber-600 dark:text-amber-400", d: "bg-amber-500" },
  transient: { l: "波动", c: "text-yellow-600 dark:text-yellow-400", d: "bg-yellow-500" },
  depleted: { l: "耗尽", c: "text-red-600 dark:text-red-400", d: "bg-red-500" },
};

/* ── Atoms ────────────────────────────────────────────────── */

function BudgetBar({ label, used, total, remaining }: { label: string; used: number; total: number; remaining: number }) {
  if (total <= 0) return null;
  const pct = Math.min((used / total) * 100, 100);
  const clr = pct > 95 ? "bg-red-500" : pct > 80 ? "bg-amber-500" : "bg-[var(--em-primary)]";
  return (
    <div className="space-y-0.5">
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>{label}</span>
        <span>{fmtTok(used)} / {fmtTok(total)} <span className={remaining <= 0 ? "text-red-500 font-medium" : ""}>(余 {fmtTok(remaining)})</span></span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-500 ${clr}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function HDot({ s }: { s: string }) {
  const c = H_CFG[s] || H_CFG.ok;
  return (
    <span className="relative inline-flex h-2.5 w-2.5">
      {s === "ok" && <span className={`absolute inline-flex h-full w-full rounded-full ${c.d} opacity-75 animate-ping`} />}
      <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${c.d}`} />
    </span>
  );
}

function CopyBtn({ text }: { text: string }) {
  const [ok, setOk] = useState(false);
  return (
    <button className="text-muted-foreground hover:text-foreground transition-colors" onClick={() => { navigator.clipboard.writeText(text); setOk(true); setTimeout(() => setOk(false), 1500); }}>
      {ok ? <Check className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
    </button>
  );
}

/* ── Provider labels ───────────────────────────────────────── */

const PROVIDER_LABELS: Record<string, string> = {
  "openai-codex": "OpenAI Codex",
  "google-gemini": "Google Gemini",
};

/* ── Import dialog ────────────────────────────────────────── */

function ImportDlg({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [tab, setTab] = useState<"subscription" | "manual">("subscription");
  const [json, setJson] = useState("");
  const [label, setLabel] = useState("");
  const [daily, setDaily] = useState(500000);
  const [weekly, setWeekly] = useState(3000000);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const [subs, setSubs] = useState<ImportableSubscription[]>([]);
  const [subsLoading, setSubsLoading] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState("");

  useEffect(() => {
    if (!open) return;
    setErr("");
    setSubsLoading(true);
    fetchImportableSubscriptions()
      .then((data) => {
        setSubs(data.subscriptions.filter((s) => s.is_active));
        if (data.subscriptions.filter((s) => s.is_active).length === 0) setTab("manual");
      })
      .catch(() => setSubs([]))
      .finally(() => setSubsLoading(false));
  }, [open]);

  const submitManual = async () => {
    setErr("");
    let td: Record<string, unknown>;
    try { td = JSON.parse(json); } catch { setErr("JSON 格式无效"); return; }
    setBusy(true);
    try {
      await importPoolAccount({ token_data: td, label: label || undefined, daily_budget_tokens: daily, weekly_budget_tokens: weekly });
      onDone(); onClose(); setJson(""); setLabel("");
    } catch (e) { setErr(e instanceof Error ? e.message : "fail"); }
    finally { setBusy(false); }
  };

  const submitFromSub = async () => {
    if (!selectedProvider) { setErr("请选择一个订阅"); return; }
    setErr(""); setBusy(true);
    try {
      await importPoolAccountFromSubscription({ provider: selectedProvider, label: label || undefined, daily_budget_tokens: daily, weekly_budget_tokens: weekly });
      onDone(); onClose(); setLabel(""); setSelectedProvider("");
    } catch (e) { setErr(e instanceof Error ? e.message : "fail"); }
    finally { setBusy(false); }
  };

  if (!open) return null;
  const inputCls = "w-full h-9 sm:h-8 rounded-lg border border-border bg-background px-3 text-xs focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none";
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 backdrop-blur-sm">
      <motion.div initial={{ opacity: 0, y: 20, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} className="bg-card border border-border rounded-t-2xl sm:rounded-2xl shadow-2xl w-full sm:max-w-lg sm:mx-4 max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between px-4 sm:px-5 py-4 border-b border-border shrink-0">
          <h3 className="text-sm font-semibold">导入池账号</h3>
          <button onClick={onClose} className="h-8 w-8 flex items-center justify-center rounded-lg hover:bg-muted/60 transition-colors"><X className="h-4 w-4 text-muted-foreground hover:text-foreground" /></button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-border px-4 sm:px-5 shrink-0">
          <button
            className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors ${tab === "subscription" ? "border-[var(--em-primary)] text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}
            onClick={() => setTab("subscription")}
          >
            从我的订阅
          </button>
          <button
            className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors ${tab === "manual" ? "border-[var(--em-primary)] text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}
            onClick={() => setTab("manual")}
          >
            手动粘贴 Token
          </button>
        </div>

        <div className="px-4 sm:px-5 py-4 space-y-4 overflow-y-auto flex-1">
          {tab === "subscription" && (
            <>
              {subsLoading ? (
                <div className="flex items-center justify-center py-6"><Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /></div>
              ) : subs.length === 0 ? (
                <div className="text-center py-6 space-y-2">
                  <p className="text-xs text-muted-foreground">暂无已连接的订阅</p>
                  <p className="text-[11px] text-muted-foreground">请先在「模型配置」中连接 OpenAI Codex 或 Google Gemini 订阅</p>
                </div>
              ) : (
                <div className="space-y-2">
                  <label className="block text-xs text-muted-foreground mb-1">选择已连接的订阅</label>
                  {subs.map((s) => (
                    <button
                      key={s.provider}
                      type="button"
                      className={`w-full flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-all ${selectedProvider === s.provider ? "border-[var(--em-primary)] bg-[var(--em-primary-alpha-06)] shadow-sm" : "border-border hover:border-border/80 hover:bg-muted/30"}`}
                      onClick={() => setSelectedProvider(s.provider)}
                    >
                      <div className={`h-8 w-8 rounded-lg flex items-center justify-center shrink-0 ${selectedProvider === s.provider ? "bg-[var(--em-primary-alpha-10)]" : "bg-muted"}`}>
                        <Database className="h-4 w-4" style={selectedProvider === s.provider ? { color: "var(--em-primary)" } : undefined} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-xs font-medium">{PROVIDER_LABELS[s.provider] || s.provider}</p>
                        <p className="text-[11px] text-muted-foreground truncate">
                          {s.plan_type && <span className="capitalize">{s.plan_type}</span>}
                          {s.account_id && <span> · {s.account_id.slice(0, 16)}</span>}
                        </p>
                      </div>
                      {selectedProvider === s.provider && <CheckCircle className="h-4 w-4 shrink-0" style={{ color: "var(--em-primary)" }} />}
                    </button>
                  ))}
                </div>
              )}
            </>
          )}

          {tab === "manual" && (
            <div>
              <label className="block text-xs text-muted-foreground mb-1">OAuth Token JSON</label>
              <textarea rows={4} value={json} onChange={e => setJson(e.target.value)} placeholder="粘贴 auth.json" className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] placeholder:text-muted-foreground/40 resize-none" />
            </div>
          )}

          {/* Shared fields */}
          <div className="grid grid-cols-1 min-[360px]:grid-cols-2 gap-3">
            <div><label className="block text-xs text-muted-foreground mb-1">标签</label><input type="text" value={label} onChange={e => setLabel(e.target.value)} placeholder="账号A" className={inputCls} /></div>
            <div><label className="block text-xs text-muted-foreground mb-1">时区</label><input defaultValue="Asia/Shanghai" disabled className="w-full h-9 sm:h-8 rounded-lg border border-border bg-muted px-3 text-xs text-muted-foreground" /></div>
          </div>
          <div className="grid grid-cols-1 min-[360px]:grid-cols-2 gap-3">
            <div><label className="block text-xs text-muted-foreground mb-1">日预算</label><input type="number" value={daily || ""} onChange={e => setDaily(Number(e.target.value) || 0)} className={inputCls} /></div>
            <div><label className="block text-xs text-muted-foreground mb-1">周预算</label><input type="number" value={weekly || ""} onChange={e => setWeekly(Number(e.target.value) || 0)} className={inputCls} /></div>
          </div>
          {err && <div className="flex items-center gap-2 text-xs text-red-500"><AlertCircle className="h-3.5 w-3.5" />{err}</div>}
        </div>
        <div className="flex justify-end gap-2 px-4 sm:px-5 py-3 border-t border-border bg-muted/30 shrink-0 safe-area-pb">
          <Button variant="outline" size="sm" className="text-xs h-9 sm:h-8" onClick={onClose}>取消</Button>
          {tab === "subscription" ? (
            <Button size="sm" className="text-xs h-9 sm:h-8 gap-1.5" onClick={submitFromSub} disabled={busy || !selectedProvider}>
              {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}导入
            </Button>
          ) : (
            <Button size="sm" className="text-xs h-9 sm:h-8 gap-1.5" onClick={submitManual} disabled={busy || !json.trim()}>
              {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}导入
            </Button>
          )}
        </div>
      </motion.div>
    </div>
  );
}

/* ── Account card ──────────────────────────────────────────── */

function AcctCard({ a, onRefresh, onToast }: { a: PoolAccountSummary; onRefresh: () => void; onToast: (m: string, t: "success" | "error") => void }) {
  const [exp, setExp] = useState(false);
  const [probing, setProbing] = useState(false);
  const [pRes, setPRes] = useState<ProbeResult | null>(null);
  const [activating, setActivating] = useState(false);
  const [editing, setEditing] = useState(false);
  const [eL, setEL] = useState(a.label);
  const [eD, setED] = useState(a.daily_budget_tokens);
  const [eW, setEW] = useState(a.weekly_budget_tokens);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);

  const sc = S_CFG[a.status] || S_CFG.active;
  const hc = H_CFG[a.health_signal] || H_CFG.ok;

  const doProbe = async () => { setProbing(true); setPRes(null); try { const r = await probePoolAccount(a.id); setPRes(r); onToast(r.status === "ok" ? "连通性正常" : `探测失败: ${r.message}`, r.status === "ok" ? "success" : "error"); } catch (e) { onToast(e instanceof Error ? e.message : "探测失败", "error"); } finally { setProbing(false); } };
  const doActivate = async () => { setActivating(true); try { await setManualActive({ pool_account_id: a.id }); onToast(`已激活: ${a.label || a.id.slice(0, 8)}`, "success"); onRefresh(); } catch (e) { onToast(e instanceof Error ? e.message : "激活失败", "error"); } finally { setActivating(false); } };
  const doSave = async () => { setSaving(true); try { await updatePoolAccount(a.id, { label: eL, daily_budget_tokens: eD, weekly_budget_tokens: eW }); onToast("已保存", "success"); setEditing(false); onRefresh(); } catch (e) { onToast(e instanceof Error ? e.message : "保存失败", "error"); } finally { setSaving(false); } };
  const doToggle = async () => { const ns = a.status === "active" ? "disabled" : "active"; try { await updatePoolAccount(a.id, { status: ns }); onToast(ns === "active" ? "已启用" : "已禁用", "success"); onRefresh(); } catch (e) { onToast(e instanceof Error ? e.message : "操作失败", "error"); } };
  const doDelete = async () => { setDeleting(true); try { await deletePoolAccount(a.id); onToast(`已删除: ${a.label || a.id.slice(0, 8)}`, "success"); onRefresh(); } catch (e) { onToast(e instanceof Error ? e.message : "删除失败", "error"); } finally { setDeleting(false); setConfirmDel(false); } };

  return (
    <motion.div layout className={`rounded-xl border transition-all duration-200 hover:shadow-md bg-card ${a.is_active ? "border-[var(--em-primary-alpha-30)] shadow-sm shadow-[var(--em-primary-alpha-10)]" : a.status === "depleted" ? "border-red-500/20 bg-red-500/[0.02]" : "border-border"}`}>
      <div className="flex items-center gap-2 sm:gap-3 px-3 sm:px-4 py-3 cursor-pointer select-none" onClick={() => setExp(!exp)}>
        <HDot s={a.health_signal} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 sm:gap-2 flex-wrap">
            <span className="text-sm font-medium truncate max-w-[140px] sm:max-w-none">{a.label || `账号 ${a.id.slice(0, 8)}`}</span>
            <Badge variant="outline" className={`text-[10px] px-1.5 py-0 border-0 ${sc.c} ${a.status === "active" ? "bg-green-500/10" : a.status === "depleted" ? "bg-red-500/10" : "bg-muted"}`}>{sc.l}</Badge>
            {a.is_active && <Badge className="text-[10px] px-1.5 py-0 bg-[var(--em-primary)] text-white border-0 gap-0.5"><Zap className="h-2.5 w-2.5" /><span className="hidden min-[360px]:inline">当前</span>激活</Badge>}
          </div>
          <div className="flex items-center gap-1.5 sm:gap-2 mt-0.5 text-[11px] text-muted-foreground">
            <span className="truncate max-w-[80px] sm:max-w-none">{a.plan_type || a.provider}</span><span>·</span><span className={hc.c}>{hc.l}</span>
            {a.health_updated_at && <><span className="hidden sm:inline">·</span><span className="hidden sm:inline">{fmtDate(a.health_updated_at)}</span></>}
          </div>
        </div>
        <div className="flex items-center gap-1 sm:gap-1.5 shrink-0" onClick={e => e.stopPropagation()}>
          <Button variant="ghost" size="sm" className="h-8 w-8 sm:h-7 sm:w-7 p-0" onClick={doProbe} disabled={probing} title="探测">
            {probing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wifi className="h-3.5 w-3.5" />}
          </Button>
          {!a.is_active && a.status === "active" && (
            <Button variant="ghost" size="sm" className="h-8 w-8 sm:h-7 sm:w-auto sm:px-2 p-0 text-xs hover:text-[var(--em-primary)]" onClick={doActivate} disabled={activating}>
              {activating ? <Loader2 className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3" />}<span className="ml-1 hidden sm:inline">激活</span>
            </Button>
          )}
        </div>
        {pRes && <span className="text-[11px] shrink-0 hidden sm:inline">{pRes.status === "ok" ? <span className="text-green-500 flex items-center gap-1"><CheckCircle className="h-3 w-3" />OK</span> : <span className="text-red-500 flex items-center gap-1"><WifiOff className="h-3 w-3" />{pRes.message?.slice(0, 20)}</span>}</span>}
        <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform duration-200 shrink-0 ${exp ? "rotate-180" : ""}`} />
      </div>
      {(a.daily_budget_tokens > 0 || a.weekly_budget_tokens > 0) && (
        <div className="px-3 sm:px-4 pb-3 space-y-1.5">
          {a.budget && a.daily_budget_tokens > 0 && <BudgetBar label="今日" used={a.budget.day_window_tokens} total={a.daily_budget_tokens} remaining={a.budget.daily_remaining} />}
          {a.budget && a.weekly_budget_tokens > 0 && <BudgetBar label="本周" used={a.budget.week_window_tokens} total={a.weekly_budget_tokens} remaining={a.budget.weekly_remaining} />}
        </div>
      )}
      <AnimatePresence initial={false}>{exp && (
        <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.2, ease: [0.25, 0.1, 0.25, 1] }} className="overflow-hidden">
          <div className="px-3 sm:px-4 pb-4 pt-1 border-t border-border/60 space-y-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground min-w-0"><span className="font-mono truncate">{a.id}</span><CopyBtn text={a.id} /></div>
            {a.account_id && <div className="flex items-center gap-2 text-xs text-muted-foreground min-w-0"><span className="shrink-0">account_id:</span><span className="font-mono truncate">{a.account_id}</span><CopyBtn text={a.account_id} /></div>}
            <div className="text-xs text-muted-foreground flex flex-wrap gap-x-1">
              <span>创建: {fmtDate(a.created_at)}</span><span className="hidden sm:inline">·</span>
              <span>更新: {fmtDate(a.updated_at)}</span>
            </div>
            {editing ? (
              <div className="space-y-2 pt-1">
                <div className="grid grid-cols-1 min-[400px]:grid-cols-3 gap-2">
                  <div><label className="block text-[11px] text-muted-foreground mb-0.5">标签</label><input type="text" value={eL} onChange={e => setEL(e.target.value)} className="w-full h-8 sm:h-7 rounded-md border border-border bg-background px-2 text-xs focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)]" /></div>
                  <div><label className="block text-[11px] text-muted-foreground mb-0.5">日预算</label><input type="number" value={eD || ""} onChange={e => setED(Number(e.target.value) || 0)} className="w-full h-8 sm:h-7 rounded-md border border-border bg-background px-2 text-xs focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none" /></div>
                  <div><label className="block text-[11px] text-muted-foreground mb-0.5">周预算</label><input type="number" value={eW || ""} onChange={e => setEW(Number(e.target.value) || 0)} className="w-full h-8 sm:h-7 rounded-md border border-border bg-background px-2 text-xs focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none" /></div>
                </div>
                <div className="flex gap-1.5">
                  <Button size="sm" className="h-8 sm:h-7 text-xs gap-1" onClick={doSave} disabled={saving}>{saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}保存</Button>
                  <Button variant="ghost" size="sm" className="h-8 sm:h-7 text-xs" onClick={() => setEditing(false)}>取消</Button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap gap-1.5 pt-1">
                <Button variant="outline" size="sm" className="h-8 sm:h-7 text-xs" onClick={() => { setEL(a.label); setED(a.daily_budget_tokens); setEW(a.weekly_budget_tokens); setEditing(true); }}>编辑</Button>
                <Button variant="outline" size="sm" className={`h-8 sm:h-7 text-xs ${a.status === "active" ? "text-red-500 hover:border-red-500/30" : "text-green-500 hover:border-green-500/30"}`} onClick={doToggle}>{a.status === "active" ? "禁用" : "启用"}</Button>
                {confirmDel ? (
                  <span className="flex items-center gap-1 flex-wrap">
                    <span className="text-[11px] text-red-500">确认删除？</span>
                    <Button variant="outline" size="sm" className="h-8 sm:h-7 text-xs text-red-500 border-red-500/30 hover:bg-red-500/10" onClick={doDelete} disabled={deleting}>{deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : "确认"}</Button>
                    <Button variant="ghost" size="sm" className="h-8 sm:h-7 text-xs" onClick={() => setConfirmDel(false)}>取消</Button>
                  </span>
                ) : (
                  <Button variant="outline" size="sm" className="h-8 sm:h-7 text-xs text-red-500 hover:border-red-500/30 gap-1" onClick={() => setConfirmDel(true)}><Trash2 className="h-3 w-3" />删除</Button>
                )}
              </div>
            )}
          </div>
        </motion.div>
      )}</AnimatePresence>
    </motion.div>
  );
}

/* ── Manual mapping panel ─────────────────────────────────── */

function ManualMappingPanel({ mappings, accts, onRefresh, onToast }: { mappings: PoolManualActive[]; accts: PoolAccountSummary[]; onRefresh: () => void; onToast: (m: string, t: "success" | "error") => void }) {
  const [deleting, setDeleting] = useState<string | null>(null);
  const doDelete = async (m: PoolManualActive) => {
    const key = `${m.provider}/${m.model_pattern}`;
    setDeleting(key);
    try { await deleteManualActive(m.provider, m.model_pattern); onToast(`已移除映射: ${key}`, "success"); onRefresh(); }
    catch (e) { onToast(e instanceof Error ? e.message : "移除失败", "error"); }
    finally { setDeleting(null); }
  };
  return (
    <div className="space-y-2 pt-2">
      <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5"><Zap className="h-3.5 w-3.5" />手动激活映射</h3>
      {mappings.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-muted/30 px-4 py-4 text-center"><p className="text-xs text-muted-foreground">暂无手动激活映射 — 点击账号卡片的「激活」按钮设置</p></div>
      ) : (<>
        {/* Desktop: table */}
        <div className="hidden sm:block rounded-xl border border-border bg-card overflow-hidden">
          <table className="w-full text-xs">
            <thead><tr className="border-b border-border bg-muted/30 text-muted-foreground">
              <th className="text-left px-3 py-2 font-medium">Provider</th>
              <th className="text-left px-3 py-2 font-medium">Model Pattern</th>
              <th className="text-left px-3 py-2 font-medium">激活账号</th>
              <th className="text-left px-3 py-2 font-medium">操作者</th>
              <th className="text-left px-3 py-2 font-medium">激活时间</th>
              <th className="text-right px-3 py-2 font-medium">操作</th>
            </tr></thead>
            <tbody>{mappings.map(m => {
              const key = `${m.provider}/${m.model_pattern}`;
              const acctLabel = accts.find(a => a.id === m.pool_account_id)?.label || m.pool_account_id.slice(0, 8);
              return (<tr key={key} className="border-b border-border/50 last:border-0">
                <td className="px-3 py-2 font-mono">{m.provider}</td>
                <td className="px-3 py-2 font-mono">{m.model_pattern}</td>
                <td className="px-3 py-2 font-medium">{acctLabel}</td>
                <td className="px-3 py-2 text-muted-foreground">{m.activated_by || "—"}</td>
                <td className="px-3 py-2 text-muted-foreground">{fmtDate(m.activated_at)}</td>
                <td className="px-3 py-2 text-right">
                  <Button variant="ghost" size="sm" className="h-6 px-1.5 text-red-500 hover:text-red-600 hover:bg-red-500/10" onClick={() => doDelete(m)} disabled={deleting === key}>
                    {deleting === key ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                  </Button>
                </td>
              </tr>);
            })}</tbody>
          </table>
        </div>

        {/* Mobile: card layout */}
        <div className="sm:hidden space-y-2">
          {mappings.map(m => {
            const key = `${m.provider}/${m.model_pattern}`;
            const acctLabel = accts.find(a => a.id === m.pool_account_id)?.label || m.pool_account_id.slice(0, 8);
            return (
              <div key={key} className="rounded-xl border border-border bg-card px-3 py-2.5 space-y-1.5">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <span className="text-xs font-mono font-medium truncate block">{m.provider} / {m.model_pattern}</span>
                  </div>
                  <Button variant="ghost" size="sm" className="h-7 w-7 p-0 shrink-0 text-red-500 hover:text-red-600 hover:bg-red-500/10" onClick={() => doDelete(m)} disabled={deleting === key}>
                    {deleting === key ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                  </Button>
                </div>
                <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
                  <span>账号: <span className="font-medium text-foreground">{acctLabel}</span></span>
                  {m.activated_by && <span>操作: {m.activated_by}</span>}
                  <span>{fmtDate(m.activated_at)}</span>
                </div>
              </div>
            );
          })}
        </div>
      </>)}
    </div>
  );
}

/* ── Stat card ─────────────────────────────────────────────── */

const _STAT_COLORS: Record<string, { circle: string; icon_bg: string; icon_cls: string; icon_style?: React.CSSProperties; border: string; icon_wrap_style?: React.CSSProperties }> = {
  "em-primary": { circle: "bg-[var(--em-primary-alpha-06)]", icon_bg: "", icon_cls: "", icon_style: { color: "var(--em-primary)" }, border: "hover:border-[var(--em-primary-alpha-20)]", icon_wrap_style: { backgroundColor: "var(--em-primary-alpha-10)" } },
  green: { circle: "bg-green-500/[0.06]", icon_bg: "bg-green-500/10", icon_cls: "text-green-500", border: "hover:border-green-500/20" },
  blue: { circle: "bg-blue-500/[0.06]", icon_bg: "bg-blue-500/10", icon_cls: "text-blue-500", border: "hover:border-blue-500/20" },
  emerald: { circle: "bg-emerald-500/[0.06]", icon_bg: "bg-emerald-500/10", icon_cls: "text-emerald-500", border: "hover:border-emerald-500/20" },
};

function StatCard({ icon: Icon, color, value, label, sub, truncate }: { icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>; color: string; value: React.ReactNode; label: string; sub: React.ReactNode; truncate?: boolean }) {
  const t = _STAT_COLORS[color] || _STAT_COLORS["em-primary"];
  return (
    <div className={`relative overflow-hidden rounded-xl border border-border bg-card px-2.5 py-2 sm:px-4 sm:py-3 ${t.border} group/stat transition-all hover:shadow-md`}>
      <div className={`hidden sm:block absolute -top-6 -right-6 h-20 w-20 rounded-full ${t.circle} group-hover/stat:scale-125 transition-transform duration-500`} />
      <div className="relative">
        <div className={`hidden sm:flex h-7 w-7 items-center justify-center rounded-md mb-2 ${t.icon_bg}`} style={t.icon_wrap_style}>
          <Icon className={`h-4 w-4 ${t.icon_cls}`} style={t.icon_style} />
        </div>
        <p className={`text-lg sm:text-2xl font-bold ${truncate ? "truncate" : ""}`}>{value}</p>
        <p className="text-xs text-muted-foreground mt-0.5">{label}</p>
        <p className={`text-[11px] text-muted-foreground mt-1 ${truncate ? "truncate" : ""}`}>{sub}</p>
      </div>
    </div>
  );
}

/* ── Main component ───────────────────────────────────────── */

export interface PoolSubTabProps {
  onToast: (msg: string, type: "success" | "error") => void;
}

export function PoolSubTab({ onToast }: PoolSubTabProps) {
  const [loading, setLoading] = useState(true);
  const [accounts, setAccounts] = useState<PoolAccountSummary[]>([]);
  const [manualMappings, setManualMappings] = useState<PoolManualActive[]>([]);
  const [importOpen, setImportOpen] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setLoading(true);
    setError("");
    try {
      const [s, ma] = await Promise.all([
        fetchPoolSummary().catch(() => ({ accounts: [] as PoolAccountSummary[], total: 0 })),
        fetchManualActive().catch(() => ({ mappings: [] as PoolManualActive[], total: 0 })),
      ]);
      setAccounts(s.accounts);
      setManualMappings(ma.mappings);
    } catch (e) { setError(e instanceof Error ? e.message : "加载失败"); }
    finally { if (!opts?.silent) setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const ac = accounts.filter(a => a.status === "active").length;
  const activated = accounts.find(a => a.is_active);
  const hc = accounts.filter(a => a.health_signal === "ok").length;
  const dayUsed = accounts.reduce((s, a) => s + (a.budget?.day_window_tokens || 0), 0);

  return (
    <div className="space-y-4">
      <ImportDlg open={importOpen} onClose={() => setImportOpen(false)} onDone={() => { onToast("导入成功", "success"); load({ silent: true }); }} />

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-4">
        <StatCard icon={Database} color="em-primary" value={String(accounts.length)} label="总账号" sub={<><span className="text-green-600 dark:text-green-400 font-medium">{ac}</span> 活跃</>} />
        <StatCard icon={Zap} color="green" value={activated ? (activated.label || activated.id.slice(0, 8)) : "—"} label="当前激活" sub={activated ? `${activated.provider} / *` : "未设置"} truncate />
        <StatCard icon={Activity} color="blue" value={fmtTok(dayUsed)} label="今日用量" sub="全部账号合计" />
        <StatCard icon={HeartPulse} color="emerald" value={<>{hc}<span className="text-sm font-normal text-muted-foreground">/{accounts.length}</span></>} label="健康" sub={accounts.length - hc > 0 ? <span className="text-amber-500 font-medium">{accounts.length - hc} 异常</span> : "全部正常"} />
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-1.5">
        <Button size="sm" className="h-8 text-xs gap-1.5" onClick={() => setImportOpen(true)}><Plus className="h-3 w-3" />导入账号</Button>
        <Button variant="outline" size="sm" className="h-8 text-xs gap-1.5" onClick={() => load()} disabled={loading}>
          {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}刷新
        </Button>
      </div>

      {/* Account list */}
      {error && <div className="rounded-xl bg-red-500/5 border border-red-500/15 px-4 py-3 flex items-center gap-2 text-xs text-red-500"><AlertCircle className="h-4 w-4 shrink-0" />{error}</div>}
      {loading && accounts.length === 0 ? (
        <div className="flex flex-col items-center py-16 gap-3"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /><p className="text-sm text-muted-foreground">加载号池数据...</p></div>
      ) : accounts.length === 0 ? (
        <div className="flex flex-col items-center py-16 gap-3">
          <div className="h-14 w-14 rounded-2xl flex items-center justify-center" style={{ backgroundColor: "var(--em-primary-alpha-10)" }}>
            <Database className="h-7 w-7" style={{ color: "var(--em-primary)" }} />
          </div>
          <p className="text-sm font-medium">暂无池账号</p>
          <p className="text-xs text-muted-foreground">点击「导入账号」添加第一个 OAuth 池账号</p>
        </div>
      ) : (
        <div className="space-y-3">{accounts.map(a => <AcctCard key={a.id} a={a} onRefresh={() => load({ silent: true })} onToast={onToast} />)}</div>
      )}

      {/* Manual activation mappings */}
      <ManualMappingPanel mappings={manualMappings} accts={accounts} onRefresh={() => load({ silent: true })} onToast={onToast} />

      {/* Admin link */}
      <div className="rounded-xl border border-dashed border-border bg-muted/20 px-4 py-4 flex flex-col sm:flex-row items-start sm:items-center gap-3">
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium">需要更多高级配置？</p>
          <p className="text-[11px] text-muted-foreground mt-0.5">轮换策略、Scope 治理、熔断器、监控指标等功能在管理中心提供</p>
        </div>
        <a href="/admin?tab=pool" className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-background text-xs font-medium hover:bg-muted/60 transition-colors shrink-0">
          前往管理中心 <ExternalLink className="h-3 w-3" />
        </a>
      </div>
    </div>
  );
}
