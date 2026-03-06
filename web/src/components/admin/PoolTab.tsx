"use client";
import { useEffect, useState, useCallback } from "react";
import { Database, Zap, Activity, HeartPulse, RefreshCw, Loader2, AlertCircle, CheckCircle, ChevronDown, Plus, Wifi, WifiOff, X, Save, Shield, ArrowRightLeft, Copy, Check } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Switch } from "@/components/ui/switch";
import { type PoolAccountSummary, type PoolAutoPolicy, type PoolRotationEvent, type ProbeResult, type ImportableSubscription, fetchPoolSummary, importPoolAccount, updatePoolAccount, probePoolAccount, setManualActive, fetchAutoPolicies, upsertAutoPolicy, runAutoEvaluate, fetchRotationEvents, fetchImportableSubscriptions, importPoolAccountFromSubscription } from "@/lib/pool-api";

function fmtTok(n: number) { if (n >= 1e6) return `${(n/1e6).toFixed(1)}M`; if (n >= 1e3) return `${(n/1e3).toFixed(0)}K`; return String(n); }
function fmtDate(iso: string) { if (!iso) return "\u2014"; try { return new Date(iso).toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}); } catch { return iso; } }

const S_CFG: Record<string,{l:string;c:string}> = { active:{l:"\u6d3b\u8dc3",c:"text-green-600 dark:text-green-400"}, disabled:{l:"\u7981\u7528",c:"text-muted-foreground"}, depleted:{l:"\u8017\u5c3d",c:"text-red-600 dark:text-red-400"} };
const H_CFG: Record<string,{l:string;c:string;d:string}> = { ok:{l:"\u6b63\u5e38",c:"text-green-600 dark:text-green-400",d:"bg-green-500"}, rate_limited:{l:"\u9650\u6d41",c:"text-amber-600 dark:text-amber-400",d:"bg-amber-500"}, transient:{l:"\u6ce2\u52a8",c:"text-yellow-600 dark:text-yellow-400",d:"bg-yellow-500"}, depleted:{l:"\u8017\u5c3d",c:"text-red-600 dark:text-red-400",d:"bg-red-500"} };
const T_CFG: Record<string,{l:string;c:string}> = { hard:{l:"\u786c\u89e6\u53d1",c:"text-red-500"}, soft:{l:"\u8f6f\u89e6\u53d1",c:"text-amber-500"}, manual:{l:"\u624b\u52a8",c:"text-blue-500"}, fallback:{l:"\u56de\u9000",c:"text-muted-foreground"} };

function BudgetBar({label,used,total,remaining}:{label:string;used:number;total:number;remaining:number}) {
  if (total<=0) return null; const pct=Math.min((used/total)*100,100); const clr=pct>95?"bg-red-500":pct>80?"bg-amber-500":"bg-[var(--em-primary)]";
  return (<div className="space-y-0.5"><div className="flex justify-between text-[11px] text-muted-foreground"><span>{label}</span><span>{fmtTok(used)} / {fmtTok(total)} <span className={remaining<=0?"text-red-500 font-medium":""}>(余 {fmtTok(remaining)})</span></span></div><div className="h-1.5 rounded-full bg-muted overflow-hidden"><div className={`h-full rounded-full transition-all duration-500 ${clr}`} style={{width:`${pct}%`}}/></div></div>);
}
function HDot({s}:{s:string}) { const c=H_CFG[s]||H_CFG.ok; return (<span className="relative inline-flex h-2.5 w-2.5">{s==="ok"&&<span className={`absolute inline-flex h-full w-full rounded-full ${c.d} opacity-75 animate-ping`}/>}<span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${c.d}`}/></span>); }
function CopyBtn({text}:{text:string}) { const [ok,setOk]=useState(false); return (<button className="text-muted-foreground hover:text-foreground transition-colors" onClick={()=>{navigator.clipboard.writeText(text);setOk(true);setTimeout(()=>setOk(false),1500);}}>{ok?<Check className="h-3 w-3 text-green-500"/>:<Copy className="h-3 w-3"/>}</button>); }

const _PROVIDER_LABELS: Record<string, string> = { "openai-codex": "OpenAI Codex", "google-gemini": "Google Gemini" };

function ImportDlg({open,onClose,onDone}:{open:boolean;onClose:()=>void;onDone:()=>void}) {
  const [tab,setTab]=useState<"subscription"|"manual">("subscription");
  const [json,setJson]=useState(""); const [label,setLabel]=useState(""); const [daily,setDaily]=useState(500000); const [weekly,setWeekly]=useState(3000000); const [busy,setBusy]=useState(false); const [err,setErr]=useState("");
  const [subs,setSubs]=useState<ImportableSubscription[]>([]); const [subsLoading,setSubsLoading]=useState(false); const [selectedProvider,setSelectedProvider]=useState("");

  useEffect(()=>{ if(!open) return; setErr(""); setSubsLoading(true); fetchImportableSubscriptions().then(d=>{ const active=d.subscriptions.filter(s=>s.is_active); setSubs(active); if(active.length===0) setTab("manual"); }).catch(()=>setSubs([])).finally(()=>setSubsLoading(false)); },[open]);

  const submitManual=async()=>{ setErr(""); let td:Record<string,unknown>; try{td=JSON.parse(json);}catch{setErr("JSON 格式无效");return;} setBusy(true); try{await importPoolAccount({token_data:td,label:label||undefined,daily_budget_tokens:daily,weekly_budget_tokens:weekly});onDone();onClose();setJson("");setLabel("");}catch(e){setErr(e instanceof Error?e.message:"fail");}finally{setBusy(false);} };
  const submitFromSub=async()=>{ if(!selectedProvider){setErr("请选择一个订阅");return;} setErr(""); setBusy(true); try{await importPoolAccountFromSubscription({provider:selectedProvider,label:label||undefined,daily_budget_tokens:daily,weekly_budget_tokens:weekly});onDone();onClose();setLabel("");setSelectedProvider("");}catch(e){setErr(e instanceof Error?e.message:"fail");}finally{setBusy(false);} };

  if (!open) return null;
  const inputCls="w-full h-8 rounded-lg border border-border bg-background px-3 text-xs focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none";
  return (<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"><motion.div initial={{opacity:0,scale:0.95}} animate={{opacity:1,scale:1}} className="bg-card border border-border rounded-2xl shadow-2xl w-full max-w-lg mx-4 max-h-[85vh] flex flex-col">
    <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0"><h3 className="text-sm font-semibold">导入池账号</h3><button onClick={onClose}><X className="h-4 w-4 text-muted-foreground hover:text-foreground"/></button></div>

    {/* Tabs */}
    <div className="flex border-b border-border px-5 shrink-0">
      <button className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors ${tab==="subscription"?"border-[var(--em-primary)] text-foreground":"border-transparent text-muted-foreground hover:text-foreground"}`} onClick={()=>setTab("subscription")}>从我的订阅</button>
      <button className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors ${tab==="manual"?"border-[var(--em-primary)] text-foreground":"border-transparent text-muted-foreground hover:text-foreground"}`} onClick={()=>setTab("manual")}>手动粘贴 Token</button>
    </div>

    <div className="px-5 py-4 space-y-4 overflow-y-auto flex-1">
      {tab==="subscription"&&(<>
        {subsLoading?(<div className="flex items-center justify-center py-6"><Loader2 className="h-4 w-4 animate-spin text-muted-foreground"/></div>):subs.length===0?(<div className="text-center py-6 space-y-2"><p className="text-xs text-muted-foreground">暂无已连接的订阅</p><p className="text-[11px] text-muted-foreground">请先在「模型配置」中连接 OpenAI Codex 或 Google Gemini 订阅</p></div>):(<div className="space-y-2">
          <label className="block text-xs text-muted-foreground mb-1">选择已连接的订阅</label>
          {subs.map(s=>(<button key={s.provider} type="button" className={`w-full flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-all ${selectedProvider===s.provider?"border-[var(--em-primary)] bg-[var(--em-primary-alpha-06)] shadow-sm":"border-border hover:border-border/80 hover:bg-muted/30"}`} onClick={()=>setSelectedProvider(s.provider)}>
            <div className={`h-8 w-8 rounded-lg flex items-center justify-center shrink-0 ${selectedProvider===s.provider?"bg-[var(--em-primary-alpha-10)]":"bg-muted"}`}><Database className="h-4 w-4" style={selectedProvider===s.provider?{color:"var(--em-primary)"}:undefined}/></div>
            <div className="flex-1 min-w-0"><p className="text-xs font-medium">{_PROVIDER_LABELS[s.provider]||s.provider}</p><p className="text-[11px] text-muted-foreground truncate">{s.plan_type&&<span className="capitalize">{s.plan_type}</span>}{s.account_id&&<span> · {s.account_id.slice(0,16)}</span>}</p></div>
            {selectedProvider===s.provider&&<CheckCircle className="h-4 w-4 shrink-0" style={{color:"var(--em-primary)"}}/>}
          </button>))}
        </div>)}
      </>)}

      {tab==="manual"&&(<div><label className="block text-xs text-muted-foreground mb-1">OAuth Token JSON</label><textarea rows={5} value={json} onChange={e=>setJson(e.target.value)} placeholder="粘贴 auth.json" className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] placeholder:text-muted-foreground/40 resize-none"/></div>)}

      <div className="grid grid-cols-2 gap-3"><div><label className="block text-xs text-muted-foreground mb-1">标签</label><input type="text" value={label} onChange={e=>setLabel(e.target.value)} placeholder="账号A" className={inputCls}/></div><div><label className="block text-xs text-muted-foreground mb-1">时区</label><input defaultValue="Asia/Shanghai" disabled className="w-full h-8 rounded-lg border border-border bg-muted px-3 text-xs text-muted-foreground"/></div></div>
      <div className="grid grid-cols-2 gap-3"><div><label className="block text-xs text-muted-foreground mb-1">日预算</label><input type="number" value={daily||""} onChange={e=>setDaily(Number(e.target.value)||0)} className={inputCls}/></div><div><label className="block text-xs text-muted-foreground mb-1">周预算</label><input type="number" value={weekly||""} onChange={e=>setWeekly(Number(e.target.value)||0)} className={inputCls}/></div></div>
      {err&&<div className="flex items-center gap-2 text-xs text-red-500"><AlertCircle className="h-3.5 w-3.5"/>{err}</div>}
    </div>
    <div className="flex justify-end gap-2 px-5 py-3 border-t border-border bg-muted/30 shrink-0">
      <Button variant="outline" size="sm" className="text-xs h-8" onClick={onClose}>取消</Button>
      {tab==="subscription"?(<Button size="sm" className="text-xs h-8 gap-1.5" onClick={submitFromSub} disabled={busy||!selectedProvider}>{busy?<Loader2 className="h-3 w-3 animate-spin"/>:<Plus className="h-3 w-3"/>}导入</Button>):(<Button size="sm" className="text-xs h-8 gap-1.5" onClick={submitManual} disabled={busy||!json.trim()}>{busy?<Loader2 className="h-3 w-3 animate-spin"/>:<Plus className="h-3 w-3"/>}导入</Button>)}
    </div>
  </motion.div></div>);
}

function AcctCard({a,onRefresh,onToast}:{a:PoolAccountSummary;onRefresh:()=>void;onToast:(m:string,t:"success"|"error")=>void}) {
  const [exp,setExp]=useState(false); const [probing,setProbing]=useState(false); const [pRes,setPRes]=useState<ProbeResult|null>(null); const [activating,setActivating]=useState(false);
  const [editing,setEditing]=useState(false); const [eL,setEL]=useState(a.label); const [eD,setED]=useState(a.daily_budget_tokens); const [eW,setEW]=useState(a.weekly_budget_tokens); const [saving,setSaving]=useState(false);
  const sc=S_CFG[a.status]||S_CFG.active; const hc=H_CFG[a.health_signal]||H_CFG.ok;
  const doProbe=async()=>{setProbing(true);setPRes(null);try{const r=await probePoolAccount(a.id);setPRes(r);onToast(r.status==="ok"?"连通性正常":`探测失败: ${r.message}`,r.status==="ok"?"success":"error");}catch(e){onToast(e instanceof Error?e.message:"探测失败","error");}finally{setProbing(false);}};
  const doActivate=async()=>{setActivating(true);try{await setManualActive({pool_account_id:a.id});onToast(`已激活: ${a.label||a.id.slice(0,8)}`,"success");onRefresh();}catch(e){onToast(e instanceof Error?e.message:"激活失败","error");}finally{setActivating(false);}};
  const doSave=async()=>{setSaving(true);try{await updatePoolAccount(a.id,{label:eL,daily_budget_tokens:eD,weekly_budget_tokens:eW});onToast("已保存","success");setEditing(false);onRefresh();}catch(e){onToast(e instanceof Error?e.message:"保存失败","error");}finally{setSaving(false);}};
  const doToggle=async()=>{const ns=a.status==="active"?"disabled":"active";try{await updatePoolAccount(a.id,{status:ns});onToast(ns==="active"?"已启用":"已禁用","success");onRefresh();}catch(e){onToast(e instanceof Error?e.message:"操作失败","error");}};
  return (
    <motion.div layout className={`rounded-xl border transition-all duration-200 hover:shadow-md bg-card ${a.is_active?"border-[var(--em-primary-alpha-30)] shadow-sm shadow-[var(--em-primary-alpha-10)]":a.status==="depleted"?"border-red-500/20 bg-red-500/[0.02]":"border-border"}`}>
      <div className="flex items-center gap-3 px-4 py-3 cursor-pointer select-none" onClick={()=>setExp(!exp)}>
        <HDot s={a.health_signal}/>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium truncate">{a.label||`账号 ${a.id.slice(0,8)}`}</span>
            <Badge variant="outline" className={`text-[10px] px-1.5 py-0 border-0 ${sc.c} ${a.status==="active"?"bg-green-500/10":a.status==="depleted"?"bg-red-500/10":"bg-muted"}`}>{sc.l}</Badge>
            {a.is_active&&<Badge className="text-[10px] px-1.5 py-0 bg-[var(--em-primary)] text-white border-0 gap-0.5"><Zap className="h-2.5 w-2.5"/>当前激活</Badge>}
          </div>
          <div className="flex items-center gap-2 mt-0.5 text-[11px] text-muted-foreground">
            <span>{a.plan_type||a.provider}</span><span>·</span><span className={hc.c}>{hc.l}</span>
            {a.health_updated_at&&<><span>·</span><span>{fmtDate(a.health_updated_at)}</span></>}
          </div>
        </div>
        <div className="flex items-center gap-1.5" onClick={e=>e.stopPropagation()}>
          <Button variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={doProbe} disabled={probing} title="探测">
            {probing?<Loader2 className="h-3.5 w-3.5 animate-spin"/>:<Wifi className="h-3.5 w-3.5"/>}
          </Button>
          {!a.is_active&&a.status==="active"&&(
            <Button variant="ghost" size="sm" className="h-7 px-2 text-xs hover:text-[var(--em-primary)]" onClick={doActivate} disabled={activating}>
              {activating?<Loader2 className="h-3 w-3 animate-spin"/>:<Zap className="h-3 w-3"/>}<span className="ml-1">激活</span>
            </Button>
          )}
        </div>
        {pRes&&<span className="text-[11px] shrink-0">{pRes.status==="ok"?<span className="text-green-500 flex items-center gap-1"><CheckCircle className="h-3 w-3"/>OK</span>:<span className="text-red-500 flex items-center gap-1"><WifiOff className="h-3 w-3"/>{pRes.message?.slice(0,20)}</span>}</span>}
        <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform duration-200 ${exp?"rotate-180":""}`}/>
      </div>
      {(a.daily_budget_tokens>0||a.weekly_budget_tokens>0)&&(
        <div className="px-4 pb-3 space-y-1.5">
          {a.budget&&a.daily_budget_tokens>0&&<BudgetBar label="今日" used={a.budget.day_window_tokens} total={a.daily_budget_tokens} remaining={a.budget.daily_remaining}/>}
          {a.budget&&a.weekly_budget_tokens>0&&<BudgetBar label="本周" used={a.budget.week_window_tokens} total={a.weekly_budget_tokens} remaining={a.budget.weekly_remaining}/>}
        </div>
      )}
      <AnimatePresence initial={false}>{exp&&(
        <motion.div initial={{height:0,opacity:0}} animate={{height:"auto",opacity:1}} exit={{height:0,opacity:0}} transition={{duration:0.2,ease:[0.25,0.1,0.25,1]}} className="overflow-hidden">
          <div className="px-4 pb-4 pt-1 border-t border-border/60 space-y-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground"><span className="font-mono">{a.id}</span><CopyBtn text={a.id}/></div>
            {a.account_id&&<div className="flex items-center gap-2 text-xs text-muted-foreground">account_id: <span className="font-mono">{a.account_id}</span><CopyBtn text={a.account_id}/></div>}
            <div className="text-xs text-muted-foreground">创建: {fmtDate(a.created_at)} · 更新: {fmtDate(a.updated_at)}</div>
            {editing?(
              <div className="space-y-2 pt-1">
                <div className="grid grid-cols-3 gap-2">
                  <div><label className="block text-[11px] text-muted-foreground mb-0.5">标签</label><input type="text" value={eL} onChange={e=>setEL(e.target.value)} className="w-full h-7 rounded-md border border-border bg-background px-2 text-xs focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)]"/></div>
                  <div><label className="block text-[11px] text-muted-foreground mb-0.5">日预算</label><input type="number" value={eD||""} onChange={e=>setED(Number(e.target.value)||0)} className="w-full h-7 rounded-md border border-border bg-background px-2 text-xs focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"/></div>
                  <div><label className="block text-[11px] text-muted-foreground mb-0.5">周预算</label><input type="number" value={eW||""} onChange={e=>setEW(Number(e.target.value)||0)} className="w-full h-7 rounded-md border border-border bg-background px-2 text-xs focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"/></div>
                </div>
                <div className="flex gap-1.5"><Button size="sm" className="h-7 text-xs gap-1" onClick={doSave} disabled={saving}>{saving?<Loader2 className="h-3 w-3 animate-spin"/>:<Save className="h-3 w-3"/>}保存</Button><Button variant="ghost" size="sm" className="h-7 text-xs" onClick={()=>setEditing(false)}>取消</Button></div>
              </div>
            ):(
              <div className="flex gap-1.5 pt-1">
                <Button variant="outline" size="sm" className="h-7 text-xs" onClick={()=>{setEL(a.label);setED(a.daily_budget_tokens);setEW(a.weekly_budget_tokens);setEditing(true);}}>编辑</Button>
                <Button variant="outline" size="sm" className={`h-7 text-xs ${a.status==="active"?"text-red-500 hover:border-red-500/30":"text-green-500 hover:border-green-500/30"}`} onClick={doToggle}>{a.status==="active"?"禁用":"启用"}</Button>
              </div>
            )}
          </div>
        </motion.div>
      )}</AnimatePresence>
    </motion.div>
  );
}

function PolicyCard({p,onRefresh,onToast}:{p:PoolAutoPolicy;onRefresh:()=>void;onToast:(m:string,t:"success"|"error")=>void}) {
  const [busy,setBusy]=useState(false);
  const toggle=async(en:boolean)=>{setBusy(true);try{await upsertAutoPolicy({provider:p.provider,model_pattern:p.model_pattern,enabled:en,low_watermark:p.low_watermark,rate_limit_threshold:p.rate_limit_threshold,transient_threshold:p.transient_threshold,error_window_minutes:p.error_window_minutes,cooldown_seconds:p.cooldown_seconds,fallback_to_default:p.fallback_to_default});onToast(en?"策略已启用":"策略已禁用","success");onRefresh();}catch(e){onToast(e instanceof Error?e.message:"操作失败","error");}finally{setBusy(false);}};
  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3 space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield className="h-4 w-4 text-muted-foreground"/>
          <span className="text-sm font-medium">{p.provider} / {p.model_pattern}</span>
          <Badge variant="outline" className={`text-[10px] px-1.5 py-0 ${p.enabled?"text-green-600 bg-green-500/10 border-green-500/20":"text-muted-foreground"}`}>{p.enabled?"启用":"禁用"}</Badge>
        </div>
        <Switch checked={p.enabled} onCheckedChange={toggle} disabled={busy}/>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        <span>低水位: <span className="text-foreground font-medium">{(p.low_watermark*100).toFixed(0)}%</span></span>
        <span>429上限: <span className="text-foreground font-medium">{p.rate_limit_threshold}次</span></span>
        <span>5xx上限: <span className="text-foreground font-medium">{p.transient_threshold}次</span></span>
        <span>冷却: <span className="text-foreground font-medium">{p.cooldown_seconds}s</span></span>
      </div>
    </div>
  );
}

function EvtItem({e,accts}:{e:PoolRotationEvent;accts:PoolAccountSummary[]}) {
  const tc=T_CFG[e.trigger]||T_CFG.hard;
  const fl=accts.find(x=>x.id===e.from_account_id)?.label||e.from_account_id?.slice(0,8)||"\u2014";
  const tl=accts.find(x=>x.id===e.to_account_id)?.label||e.to_account_id?.slice(0,8)||"\u9ed8\u8ba4\u94fe\u8def";
  return (
    <div className="flex gap-3 py-2">
      <div className="flex flex-col items-center">
        <span className={`h-2.5 w-2.5 rounded-full shrink-0 mt-1 ${e.trigger==="hard"?"bg-red-500":e.trigger==="soft"?"bg-amber-500":e.trigger==="manual"?"bg-blue-500":"bg-gray-400"}`}/>
        <div className="w-px flex-1 bg-border/60 mt-1"/>
      </div>
      <div className="flex-1 min-w-0 pb-2">
        <div className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">{fmtDate(e.created_at)}</span>
          <Badge variant="outline" className={`text-[10px] px-1.5 py-0 border-0 ${tc.c}`}>{tc.l}</Badge>
        </div>
        <div className="flex items-center gap-1.5 mt-1 text-xs">
          <span className="font-medium truncate">{fl}</span>
          <ArrowRightLeft className="h-3 w-3 text-muted-foreground shrink-0"/>
          <span className="font-medium truncate">{e.fallback_used?"\u9ed8\u8ba4\u94fe\u8def":tl}</span>
        </div>
        <div className="text-[11px] text-muted-foreground mt-0.5 truncate">{e.reason}</div>
      </div>
    </div>
  );
}

interface PoolTabProps { onToast: (msg: string, type: "success"|"error") => void; }

export default function PoolTab({ onToast }: PoolTabProps) {
  const [tab,setTab]=useState<"accounts"|"rotation">("accounts");
  const [loading,setLoading]=useState(true);
  const [accounts,setAccounts]=useState<PoolAccountSummary[]>([]);
  const [policies,setPolicies]=useState<PoolAutoPolicy[]>([]);
  const [events,setEvents]=useState<PoolRotationEvent[]>([]);
  const [importOpen,setImportOpen]=useState(false);
  const [evaluating,setEvaluating]=useState(false);
  const [error,setError]=useState("");

  const load=useCallback(async(opts?:{silent?:boolean})=>{
    if(!opts?.silent) setLoading(true); setError("");
    try {
      const [s,p,ev]=await Promise.all([
        fetchPoolSummary().catch(()=>({accounts:[] as PoolAccountSummary[],total:0})),
        fetchAutoPolicies().catch(()=>({policies:[] as PoolAutoPolicy[],total:0})),
        fetchRotationEvents({limit:30}).catch(()=>({events:[] as PoolRotationEvent[],total:0})),
      ]);
      setAccounts(s.accounts); setPolicies(p.policies); setEvents(ev.events);
    } catch(e){setError(e instanceof Error?e.message:"\u52a0\u8f7d\u5931\u8d25");}
    finally{if(!opts?.silent) setLoading(false);}
  },[]);

  useEffect(()=>{load();},[load]);

  const doEval=async()=>{
    setEvaluating(true);
    try{const r=await runAutoEvaluate();const acts=r.results.filter(x=>x.action!=="none");onToast(acts.length>0?`\u8bc4\u4f30\u5b8c\u6210: ${acts.length} \u4e2a\u64cd\u4f5c`:"\u8bc4\u4f30\u5b8c\u6210: \u65e0\u9700\u64cd\u4f5c","success");load({silent:true});}
    catch(e){onToast(e instanceof Error?e.message:"\u8bc4\u4f30\u5931\u8d25","error");}
    finally{setEvaluating(false);}
  };

  const ac=accounts.filter(a=>a.status==="active").length;
  const activated=accounts.find(a=>a.is_active);
  const hc=accounts.filter(a=>a.health_signal==="ok").length;
  const dayUsed=accounts.reduce((s,a)=>s+(a.budget?.day_window_tokens||0),0);

  return (<div className="space-y-4">
    <ImportDlg open={importOpen} onClose={()=>setImportOpen(false)} onDone={()=>{onToast("\u5bfc\u5165\u6210\u529f","success");load({silent:true});}}/>

    {/* Stats row */}
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-4">
      <StatCard icon={Database} color="em-primary" value={String(accounts.length)} label="\u603b\u8d26\u53f7" sub={<><span className="text-green-600 dark:text-green-400 font-medium">{ac}</span> \u6d3b\u8dc3</>}/>
      <StatCard icon={Zap} color="green" value={activated?(activated.label||activated.id.slice(0,8)):"\u2014"} label="\u5f53\u524d\u6fc0\u6d3b" sub={activated?`${activated.provider} / *`:"\u672a\u8bbe\u7f6e"} truncate/>
      <StatCard icon={Activity} color="blue" value={fmtTok(dayUsed)} label="\u4eca\u65e5\u7528\u91cf" sub="\u5168\u90e8\u8d26\u53f7\u5408\u8ba1"/>
      <StatCard icon={HeartPulse} color="emerald" value={<>{hc}<span className="text-sm font-normal text-muted-foreground">/{accounts.length}</span></>} label="\u5065\u5eb7" sub={accounts.length-hc>0?<span className="text-amber-500 font-medium">{accounts.length-hc} \u5f02\u5e38</span>:"\u5168\u90e8\u6b63\u5e38"}/>
    </div>

    {/* Inner tabs */}
    <Tabs value={tab} onValueChange={v=>setTab(v as "accounts"|"rotation")}>
      <div className="flex items-center gap-2">
        <TabsList className="flex-1 sm:flex-none">
          <TabsTrigger value="accounts" className="text-xs gap-1.5"><Database className="h-3.5 w-3.5"/>\u8d26\u53f7</TabsTrigger>
          <TabsTrigger value="rotation" className="text-xs gap-1.5"><ArrowRightLeft className="h-3.5 w-3.5"/>\u81ea\u52a8\u8f6e\u6362</TabsTrigger>
        </TabsList>
        <div className="flex gap-1.5 ml-auto">
          {tab==="accounts"&&<Button size="sm" className="h-8 text-xs gap-1.5" onClick={()=>setImportOpen(true)}><Plus className="h-3 w-3"/>\u5bfc\u5165\u8d26\u53f7</Button>}
          {tab==="rotation"&&<Button variant="outline" size="sm" className="h-8 text-xs gap-1.5" onClick={doEval} disabled={evaluating}>{evaluating?<Loader2 className="h-3 w-3 animate-spin"/>:<Activity className="h-3 w-3"/>}\u624b\u52a8\u8bc4\u4f30</Button>}
          <Button variant="outline" size="sm" className="h-8 text-xs gap-1.5" onClick={()=>load()} disabled={loading}>{loading?<Loader2 className="h-3 w-3 animate-spin"/>:<RefreshCw className="h-3 w-3"/>}\u5237\u65b0</Button>
        </div>
      </div>

      <TabsContent value="accounts" className="mt-4 space-y-3">
        {error&&<div className="rounded-xl bg-red-500/5 border border-red-500/15 px-4 py-3 flex items-center gap-2 text-xs text-red-500"><AlertCircle className="h-4 w-4 shrink-0"/>{error}</div>}
        {loading&&accounts.length===0?(
          <div className="flex flex-col items-center py-16 gap-3"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground"/><p className="text-sm text-muted-foreground">\u52a0\u8f7d\u53f7\u6c60\u6570\u636e...</p></div>
        ):accounts.length===0?(
          <div className="flex flex-col items-center py-16 gap-3"><div className="h-14 w-14 rounded-2xl flex items-center justify-center" style={{backgroundColor:"var(--em-primary-alpha-10)"}}><Database className="h-7 w-7" style={{color:"var(--em-primary)"}}/></div><p className="text-sm font-medium">\u6682\u65e0\u6c60\u8d26\u53f7</p><p className="text-xs text-muted-foreground">\u70b9\u51fb\u300c\u5bfc\u5165\u8d26\u53f7\u300d\u6dfb\u52a0\u7b2c\u4e00\u4e2a OAuth \u6c60\u8d26\u53f7</p></div>
        ):(
          <div className="space-y-3">{accounts.map(a=><AcctCard key={a.id} a={a} onRefresh={()=>load({silent:true})} onToast={onToast}/>)}</div>
        )}
      </TabsContent>

      <TabsContent value="rotation" className="mt-4 space-y-6">
        {/* Policies */}
        <div className="space-y-3">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">\u81ea\u52a8\u8f6e\u6362\u7b56\u7565</h3>
          {policies.length===0?(
            <div className="rounded-xl border border-dashed border-border bg-muted/30 px-4 py-6 text-center"><p className="text-xs text-muted-foreground">\u6682\u65e0\u7b56\u7565\uff0c\u8bf7\u901a\u8fc7 API \u521b\u5efa</p></div>
          ):(
            <div className="space-y-3">{policies.map(p=><PolicyCard key={p.id} p={p} onRefresh={()=>load({silent:true})} onToast={onToast}/>)}</div>
          )}
        </div>
        {/* Events timeline */}
        <div className="space-y-2">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">\u8f6e\u6362\u5ba1\u8ba1</h3>
          {events.length===0?(
            <div className="rounded-xl border border-dashed border-border bg-muted/30 px-4 py-6 text-center"><p className="text-xs text-muted-foreground">\u6682\u65e0\u8f6e\u6362\u4e8b\u4ef6</p></div>
          ):(
            <div className="rounded-xl border border-border bg-card px-4 py-2">{events.map(e=><EvtItem key={e.id} e={e} accts={accounts}/>)}</div>
          )}
        </div>
      </TabsContent>
    </Tabs>
  </div>);
}

const _STAT_COLORS: Record<string,{circle:string;icon_bg:string;icon_cls:string;icon_style?:React.CSSProperties;border:string;icon_wrap_style?:React.CSSProperties}> = {
  "em-primary": { circle:"bg-[var(--em-primary-alpha-06)]", icon_bg:"", icon_cls:"", icon_style:{color:"var(--em-primary)"}, border:"hover:border-[var(--em-primary-alpha-20)]", icon_wrap_style:{backgroundColor:"var(--em-primary-alpha-10)"} },
  green:   { circle:"bg-green-500/[0.06]",   icon_bg:"bg-green-500/10",   icon_cls:"text-green-500",   border:"hover:border-green-500/20" },
  blue:    { circle:"bg-blue-500/[0.06]",    icon_bg:"bg-blue-500/10",    icon_cls:"text-blue-500",    border:"hover:border-blue-500/20" },
  emerald: { circle:"bg-emerald-500/[0.06]", icon_bg:"bg-emerald-500/10", icon_cls:"text-emerald-500", border:"hover:border-emerald-500/20" },
};

function StatCard({icon:Icon,color,value,label,sub,truncate}:{icon:React.ComponentType<{className?:string;style?:React.CSSProperties}>;color:string;value:React.ReactNode;label:string;sub:React.ReactNode;truncate?:boolean}) {
  const t=_STAT_COLORS[color]||_STAT_COLORS["em-primary"];
  return (
    <div className={`relative overflow-hidden rounded-xl border border-border bg-card px-2.5 py-2 sm:px-4 sm:py-3 ${t.border} group/stat transition-all hover:shadow-md`}>
      <div className={`hidden sm:block absolute -top-6 -right-6 h-20 w-20 rounded-full ${t.circle} group-hover/stat:scale-125 transition-transform duration-500`}/>
      <div className="relative">
        <div className={`hidden sm:flex h-7 w-7 items-center justify-center rounded-md mb-2 ${t.icon_bg}`} style={t.icon_wrap_style}>
          <Icon className={`h-4 w-4 ${t.icon_cls}`} style={t.icon_style}/>
        </div>
        <p className={`text-lg sm:text-2xl font-bold ${truncate?"truncate":""}`}>{value}</p>
        <p className="text-xs text-muted-foreground mt-0.5">{label}</p>
        <p className={`text-[11px] text-muted-foreground mt-1 ${truncate?"truncate":""}`}>{sub}</p>
      </div>
    </div>
  );
}
