"use client";

import { createElement, useId, useMemo, useState } from "react";
import { Activity, ArrowUpRight, Ban, Check, ChevronDown, CircleDot, CircleHelp, CircleSlash, Clock3, Gauge, Info, Pin, PinOff, Undo2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useIsDesktop } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";
import { jevGateLabel } from "@/lib/jev-settings";
import {
  ANSWER_LABELS, JEV_PACKS, cardTone, formatConfidence, formatContextAnswer,
  formatLatency, jevEmptyCopy, packTitle, summarizeTraces, traceActionLabel, traceNeedsAttention,
  traceStatus, type JevTrace,
} from "@/lib/jev-trace";
import { useJevStore } from "@/stores/jev-store";

const TONE_CLASS: Record<ReturnType<typeof cardTone>, string> = {
  applied: "bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]",
  unavailable: "bg-muted text-muted-foreground",
  deny: "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300",
  ask: "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
};
const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--em-primary)] focus-visible:ring-offset-2 focus-visible:ring-offset-background";
const TONE_ICON = { applied: Check, unavailable: Undo2, deny: Ban, ask: CircleHelp } as const;
function statusIcon(trace: JevTrace, tone: ReturnType<typeof cardTone>) {
  if (trace.gate === "off" || trace.reason === "disabled") return CircleSlash;
  return TONE_ICON[tone];
}

function DecisionCard({ trace, index }: { trace: JevTrace; index: number }) {
  const status = traceStatus(trace);
  const tone = cardTone(trace);
  const StatusIcon = statusIcon(trace, tone);
  const confidence = formatConfidence(trace.answers.confidence);
  const answers = Object.entries(trace.answers).filter(([key]) => ANSWER_LABELS[key] && key !== "confidence");
  return (
    <article className="min-w-0 rounded-xl border border-border/70 bg-background p-3.5">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className="tabular-nums text-muted-foreground/70">{String(index + 1).padStart(2, "0")}</span>
        <h3 className="font-medium text-muted-foreground">{packTitle(trace.pack)}</h3>
        <span className={cn("ml-auto inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-medium", TONE_CLASS[tone])}>
          {createElement(StatusIcon, { className: "size-3" })}
          {status.label}
        </span>
      </div>
      <p className="mt-2 break-words text-sm font-semibold leading-6 text-foreground">{traceActionLabel(trace)}</p>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">{status.description}</p>
      {trace.pack === "context.resolve" && (
        <dl className="mt-3 grid gap-2 rounded-lg bg-muted/40 p-2.5 text-xs">
          {["workspace", "target", "edit_intent"].map((key) => trace.answers[key] !== undefined && (
            <div key={key} className="flex justify-between gap-3">
              <dt className="shrink-0 text-muted-foreground">{ANSWER_LABELS[key]}</dt>
              <dd className="break-words text-right">{formatContextAnswer(key, trace.answers[key])}</dd>
            </div>
          ))}
        </dl>
      )}
      <details className="group mt-3 border-t border-border/50 pt-2.5">
        <summary className={cn("flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 rounded text-[11px] text-muted-foreground [&::-webkit-details-marker]:hidden", FOCUS)}>
          <span className="inline-flex items-center gap-1">判断详情 <ChevronDown className="size-3 transition-transform group-open:rotate-180 motion-reduce:transition-none" /></span>
          <span className="ml-auto inline-flex items-center gap-1 tabular-nums"><Clock3 className="size-3" />{formatLatency(trace.latencyMs)}</span>
          {confidence && <span className="tabular-nums">置信度 {confidence}</span>}
        </summary>
        <dl className="mt-3 space-y-2 text-[11px]">
          {answers.map(([key, value]) => (
            <div key={key} className="flex justify-between gap-3">
              <dt className="shrink-0 text-muted-foreground">{ANSWER_LABELS[key]}</dt>
              <dd className="min-w-0 break-words text-right">{formatContextAnswer(key, value)}</dd>
            </div>
          ))}
          <div className="flex justify-between gap-3"><dt className="text-muted-foreground">评估模式</dt><dd>{jevGateLabel(trace.gate)}</dd></div>
          <div className="flex justify-between gap-3"><dt className="text-muted-foreground">连接方式</dt><dd>{trace.transport === "typesafe" ? "TypeSafe 直连" : trace.transport === "gateway" ? "AI Gateway" : "不可用"}</dd></div>
          {trace.reason && <div className="border-t border-border/50 pt-2"><dt className="text-muted-foreground">诊断原因</dt><dd className="mt-1 break-all font-mono text-muted-foreground">{trace.reason}</dd></div>}
        </dl>
      </details>
    </article>
  );
}

function StageRail({ traces, selected, onSelect }: { traces: JevTrace[]; selected: string; onSelect: (pack: string) => void }) {
  const packs = useMemo(() => {
    const counts = new Map<string, number>();
    traces.forEach((trace) => counts.set(trace.pack, (counts.get(trace.pack) || 0) + 1));
    return [...JEV_PACKS.map((pack) => pack.id), ...counts.keys()].filter((pack, index, all) => counts.has(pack) && all.indexOf(pack) === index).map((pack) => ({ pack, count: counts.get(pack)! }));
  }, [traces]);
  return (
    <div role="group" aria-label="按评估环节筛选" className="flex min-w-0 gap-1.5 overflow-x-auto px-0.5 py-1 [scrollbar-width:thin]">
      {[{ pack: "all", count: traces.length }, ...packs].map(({ pack, count }) => (
        <button key={pack} type="button" aria-pressed={selected === pack} onClick={() => onSelect(pack)}
          className={cn("shrink-0 whitespace-nowrap rounded-full border px-2.5 py-1.5 text-[11px] transition-colors", FOCUS, selected === pack ? "border-[var(--em-primary-alpha-25)] bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]" : "border-border/70 text-muted-foreground hover:bg-muted")}>
          {pack === "all" ? "全部环节" : packTitle(pack)} <span className="ml-1 tabular-nums opacity-70">{count}</span>
        </button>
      ))}
    </div>
  );
}

function VerticalTimeline({ traces }: { traces: { trace: JevTrace; index: number }[] }) {
  return <ol className="flex flex-col gap-2.5">{traces.map(({ trace, index }) => <li key={trace.id}><DecisionCard trace={trace} index={index} /></li>)}</ol>;
}

export function JevTimelinePanel() {
  const traces = useJevStore((s) => s.traces);
  const pending = useJevStore((s) => s.pending);
  const [pack, setPack] = useState("all");
  const [attentionOnly, setAttentionOnly] = useState(false);
  const [limit, setLimit] = useState(40);
  const summary = useMemo(() => summarizeTraces(traces), [traces]);
  const selected = pack === "all" || traces.some((trace) => trace.pack === pack) ? pack : "all";
  const filtered = traces.map((trace, index) => ({ trace, index })).filter(({ trace }) => (selected === "all" || trace.pack === selected) && (!attentionOnly || traceNeedsAttention(trace))).reverse();

  return (
    <div className="space-y-4 p-4">
      <div className="rounded-xl border border-[var(--em-primary-alpha-15)] bg-[var(--em-primary-alpha-06)] p-3.5">
        <div className="flex items-center gap-2 text-xs font-medium">
          <span className={cn("size-1.5 rounded-full", pending ? "bg-[var(--em-primary)] motion-safe:animate-pulse" : "bg-muted-foreground/50")} />
          <span role="status">{pending ? "本轮进行中 · 记录随任务更新" : traces.length ? "本轮记录已更新" : "等待下一次任务"}</span>
        </div>
        <dl className="mt-4 grid grid-cols-3 gap-2">
          {[{ label: "已评估", value: summary.evaluated }, { label: "已应用", value: summary.applied }, { label: "平均耗时", value: formatLatency(summary.meanMs) }].map((item) => (
            <div key={item.label}><dd className="text-lg font-semibold tabular-nums tracking-tight">{item.value}</dd><dt className="mt-0.5 text-[11px] text-muted-foreground">{item.label}</dt></div>
          ))}
        </dl>
      </div>
      <p className="flex items-start gap-2 text-[11px] leading-5 text-muted-foreground"><Info className="mt-1 size-3 shrink-0" />Jev 为任务提供判断建议；开启的环节会直接接入当前回合。</p>
      {traces.length ? (
        <>
          <StageRail traces={traces} selected={selected} onSelect={(value) => { setPack(value); setLimit(40); }} />
          <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
            <span>最新在前 · {filtered.length} 条记录</span>
            <button type="button" aria-pressed={attentionOnly} onClick={() => { setAttentionOnly(!attentionOnly); setLimit(40); }} className={cn("rounded-md px-2 py-1.5", FOCUS, attentionOnly ? "bg-amber-500/10 text-amber-700 dark:text-amber-300" : "hover:bg-muted")}>
              需留意 {summary.attention}
            </button>
          </div>
          {filtered.length ? <VerticalTimeline traces={filtered.slice(0, limit)} /> : (
            <div className="rounded-xl border border-dashed p-6 text-center text-xs text-muted-foreground">
              <p>当前筛选下没有记录</p>
              <button type="button" onClick={() => { setPack("all"); setAttentionOnly(false); }} className={cn("mt-3 rounded text-[var(--em-primary)]", FOCUS)}>查看全部记录</button>
            </div>
          )}
          {filtered.length > limit && <Button variant="outline" size="sm" className="w-full" onClick={() => setLimit((value) => value + 40)}>加载更早记录（剩余 {filtered.length - limit} 条）</Button>}
        </>
      ) : (
        <div className="rounded-xl border border-dashed border-border px-5 py-8 text-center">
          <CircleDot className="mx-auto mb-3 size-7 text-muted-foreground/40" />
          <p className="text-sm font-medium">{pending ? "等待首条评估记录" : "还没有评估记录"}</p>
          <p className="mt-2 text-xs leading-6 text-muted-foreground">{pending ? "任务已开始，相关环节触发后会显示在这里。" : "发送任务后，可在这里查看上下文判断、工具建议和结果检查。"}</p>
        </div>
      )}
    </div>
  );
}

export function JevInlineRail({ compact = false }: { compact?: boolean }) {
  const chatEnabled = useJevStore((s) => s.chatEnabled);
  const traces = useJevStore((s) => s.traces);
  const pending = useJevStore((s) => s.pending);
  const collapsed = useJevStore((s) => s.railCollapsed);
  const setCollapsed = useJevStore((s) => s.setRailCollapsed);
  const setDrawerOpen = useJevStore((s) => s.setDrawerOpen);
  const contentId = useId();
  const summary = summarizeTraces(traces);
  const latest = traces[traces.length - 1];
  const railSummary = !latest
    ? "等待评估记录"
    : summary.evaluated > 0
      ? `${summary.evaluated} 次评估 · ${summary.applied} 项应用`
      : jevEmptyCopy({ streaming: pending, traces }) || "评估未完成，任务按原流程处理";
  if (!chatEnabled || (!pending && !traces.length)) return null;

  return (
    <section aria-label="Jev 任务辅助" className="my-2 min-w-0 overflow-hidden rounded-xl border border-border/60 bg-muted/20">
      <div className="flex min-w-0 items-center gap-1 pr-1">
        <button type="button" aria-expanded={!collapsed} aria-controls={contentId} onClick={() => setCollapsed(!collapsed)} className={cn("flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-3 py-2.5 text-left hover:bg-muted/40", FOCUS)}>
          <Activity className="size-3.5 shrink-0 text-[var(--em-primary)]" />
          <span className="shrink-0 text-xs font-semibold">Jev</span>
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{railSummary}</span>
          {pending && <span title="本轮进行中" className="size-1.5 shrink-0 rounded-full bg-[var(--em-primary)] motion-safe:animate-pulse" />}
          <ChevronDown className={cn("size-3.5 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none", collapsed && "-rotate-90")} />
        </button>
        <button type="button" title="查看 Jev 时间线" aria-label="查看 Jev 时间线" onClick={() => setDrawerOpen(true)} className={cn("grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground", FOCUS)}><ArrowUpRight className="size-3.5" /></button>
      </div>
      <div id={contentId} hidden={collapsed} className="border-t border-border/50 p-3">
        {latest ? <p className="mb-2 text-xs leading-5 text-muted-foreground">最近：{packTitle(latest.pack)} · {traceActionLabel(latest)} · {traceStatus(latest).label}</p> : <p className="text-xs leading-5 text-muted-foreground">相关环节触发后，会在这里显示判断与实际影响。</p>}
        {!compact && latest && <DecisionCard trace={latest} index={traces.length - 1} />}
        <button type="button" onClick={() => setDrawerOpen(true)} className={cn("mt-3 inline-flex items-center gap-1 rounded text-xs text-[var(--em-primary)]", FOCUS)}>查看全部 {traces.length} 条记录 <ArrowUpRight className="size-3" /></button>
      </div>
    </section>
  );
}

export function JevTimelineButton() {
  const chatEnabled = useJevStore((s) => s.chatEnabled);
  const pending = useJevStore((s) => s.pending);
  const pinned = useJevStore((s) => s.pinned);
  const drawerOpen = useJevStore((s) => s.drawerOpen);
  const visible = drawerOpen || pinned;
  if (!chatEnabled) return null;
  return (
    <Button variant="ghost" size="icon" className={cn("relative h-8 w-8 p-0", visible && "bg-accent text-accent-foreground")} title="Jev 时间线" aria-label="Jev 时间线" aria-expanded={visible}
      onClick={() => useJevStore.setState(visible ? { drawerOpen: false, pinned: false } : { drawerOpen: true })}>
      <Gauge className="size-4" />
      {pending && <span aria-hidden className="absolute right-1 top-1 size-1.5 rounded-full bg-[var(--em-primary)] motion-safe:animate-pulse" />}
    </Button>
  );
}

function JevPinButton() {
  const pinned = useJevStore((s) => s.pinned);
  return <Button variant="ghost" size="icon" className="h-8 w-8 rounded-lg" title={pinned ? "取消固定侧栏" : "固定侧栏"} aria-label={pinned ? "取消固定侧栏" : "固定侧栏"} aria-pressed={pinned} onClick={() => useJevStore.getState().togglePinned()}>{pinned ? <PinOff className="size-3.5" /> : <Pin className="size-3.5" />}</Button>;
}

export function JevTimelineDrawer() {
  const isDesktop = useIsDesktop();
  const chatEnabled = useJevStore((s) => s.chatEnabled);
  const open = useJevStore((s) => s.drawerOpen);
  const pinned = useJevStore((s) => s.pinned);
  const sessionId = useJevStore((s) => s.sessionId);
  const visible = chatEnabled && (open || pinned);
  const titleId = useId();
  const close = () => useJevStore.setState({ drawerOpen: false, pinned: false });
  if (isDesktop) {
    if (!visible) return null;
    return (
      <aside aria-labelledby={titleId} data-testid="jev-desktop-sidebar" className="relative flex h-full w-[360px] max-w-[32vw] flex-shrink-0 flex-col border-l border-border bg-muted/15" onKeyDown={(event) => { if (event.key === "Escape" && !event.defaultPrevented) { event.stopPropagation(); close(); } }}>
        <div className="flex shrink-0 items-center gap-2 border-b border-border/60 bg-background px-4 py-3">
          <span className="grid size-8 place-items-center rounded-xl bg-[var(--em-primary-alpha-10)]"><Activity className="size-4 text-[var(--em-primary)]" /></span>
          <div className="min-w-0 flex-1"><h2 id={titleId} className="text-sm font-semibold">Jev 时间线</h2><p className="mt-0.5 text-[10px] text-muted-foreground">判断依据与实际影响</p></div>
          <JevPinButton />
          <Button variant="ghost" size="icon" className="size-8 rounded-lg" aria-label="关闭 Jev 时间线" onClick={close}><X className="size-4" /></Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain"><JevTimelinePanel key={sessionId} /></div>
      </aside>
    );
  }
  return (
    <Dialog open={visible} onOpenChange={(value) => { if (!value) close(); }}>
      <DialogContent className="inset-0 flex h-dvh max-h-dvh w-full max-w-none translate-x-0 translate-y-0 flex-col gap-0 rounded-none border-0 p-0 sm:inset-y-0 sm:left-auto sm:right-0 sm:max-w-[420px]" onCloseAutoFocus={(event) => { event.preventDefault(); document.querySelector<HTMLButtonElement>('button[aria-label="Jev 时间线"]')?.focus(); }}>
        <div className="border-b border-border px-5 py-4 pr-14"><DialogTitle className="text-sm">Jev 时间线</DialogTitle><DialogDescription className="mt-1 text-xs">查看本轮判断依据与实际影响</DialogDescription></div>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain pb-[env(safe-area-inset-bottom)]"><JevTimelinePanel key={sessionId} /></div>
      </DialogContent>
    </Dialog>
  );
}
