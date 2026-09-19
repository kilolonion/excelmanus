"use client";

import { useMemo } from "react";
import { motion } from "framer-motion";
import {
  Activity,
  ChevronDown,
  Eye,
  Gauge,
  Pin,
  PinOff,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { SlidePanel } from "@/components/ui/slide-panel";
import { useIsDesktop } from "@/hooks/use-mobile";
import { panelSlideVariants } from "@/lib/sidebar-motion";
import { cn } from "@/lib/utils";
import { jevGateLabel } from "@/lib/jev-settings";
import {
  ANSWER_LABELS,
  JEV_PACKS,
  cardTone,
  formatConfidence,
  formatLatency,
  jevEmptyCopy,
  packHint,
  packTitle,
  type JevTrace,
} from "@/lib/jev-trace";
import { useJevStore } from "@/stores/jev-store";
import { useChatStore } from "@/stores/chat-store";

const TONE_CLASS: Record<ReturnType<typeof cardTone>, string> = {
  shadow:
    "border-dashed border-amber-400/70 bg-amber-50/70 dark:bg-amber-950/20",
  applied:
    "border-solid border-[var(--em-primary)] bg-[var(--em-primary-alpha-06)] shadow-sm",
  unavailable: "border-dashed border-border bg-muted/40 text-muted-foreground",
  deny: "border-solid border-[var(--em-error)]/70 bg-red-50/80 dark:bg-red-950/20",
  ask: "border-solid border-amber-500/80 bg-amber-50/80 dark:bg-amber-950/30",
};

const TONE_DOT: Record<ReturnType<typeof cardTone>, string> = {
  shadow: "bg-[var(--em-gold)]",
  applied: "bg-[var(--em-primary)]",
  unavailable: "bg-muted-foreground/40",
  deny: "bg-[var(--em-error)]",
  ask: "bg-[var(--em-gold)]",
};

function AnswerChips({ answers }: { answers: JevTrace["answers"] }) {
  const entries = Object.entries(answers).filter(([key]) => ANSWER_LABELS[key]);
  if (entries.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {entries.map(([key, value]) => {
        const shown =
          key === "confidence" && typeof value === "number"
            ? formatConfidence(value)
            : String(value);
        if (!shown) return null;
        return (
          <span
            key={key}
            className="inline-flex items-center whitespace-nowrap rounded-full border border-[var(--em-hairline)] bg-background/80 px-2 py-0.5 text-[11px] tabular-nums text-muted-foreground"
          >
            <span className="text-foreground/70">{ANSWER_LABELS[key]}</span>
            <span className="mx-1 text-border">·</span>
            {shown}
          </span>
        );
      })}
    </div>
  );
}

function DecisionCard({ trace, index }: { trace: JevTrace; index?: number }) {
  const tone = cardTone(trace);
  const confidence = formatConfidence(trace.answers.confidence);
  return (
    <article
      className={cn(
        "rounded-2xl border px-3 py-2.5 transition-colors",
        TONE_CLASS[tone],
      )}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            {typeof index === "number" ? (
              <span className="text-[10px] tabular-nums text-muted-foreground">
                {String(index + 1).padStart(2, "0")}
              </span>
            ) : null}
            <h3 className="whitespace-nowrap text-[13px] font-semibold text-foreground">
              {packTitle(trace.pack)}
            </h3>
            <span className="whitespace-nowrap rounded-full bg-background/70 px-1.5 py-px text-[10px] font-medium text-muted-foreground">
              {jevGateLabel(trace.gate)}
            </span>
            {trace.applied ? (
              <span className="whitespace-nowrap rounded-full bg-[var(--em-primary-alpha-12)] px-1.5 py-px text-[10px] font-medium text-[var(--em-primary)]">
                已生效
              </span>
            ) : (
              <span className="inline-flex items-center gap-0.5 whitespace-nowrap rounded-full bg-background/70 px-1.5 py-px text-[10px] text-muted-foreground">
                <Eye className="h-3 w-3" />
                观察
              </span>
            )}
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">{packHint(trace.pack)}</p>
        </div>
        <div className="flex flex-shrink-0 flex-col items-end gap-0.5 text-right">
          <span className="text-[12px] font-medium tabular-nums text-foreground">
            {formatLatency(trace.latencyMs)}
          </span>
          {confidence ? (
            <span className="text-[11px] tabular-nums text-muted-foreground">{confidence}</span>
          ) : null}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[12px]">
        <span className="whitespace-nowrap rounded-md bg-background/80 px-1.5 py-0.5 font-medium text-foreground">
          {trace.action}
        </span>
        <span className="whitespace-nowrap rounded-md bg-background/60 px-1.5 py-0.5 text-muted-foreground">
          {trace.transport === "unavailable" ? "不可用" : trace.transport === "typesafe" ? "TypeSafe" : "Gateway"}
        </span>
        {trace.kind !== "noop" ? (
          <span className="text-muted-foreground">{trace.kind}</span>
        ) : null}
      </div>
      <AnswerChips answers={trace.answers} />
      <p className="mt-2 text-[12px] leading-5 text-foreground/80">{trace.impact}</p>
    </article>
  );
}

function StageRail({
  traces,
  streaming,
}: {
  traces: JevTrace[];
  streaming: boolean;
}) {
  const latest = useMemo(() => {
    const map = new Map<string, JevTrace>();
    for (const trace of traces) map.set(trace.pack, trace);
    return map;
  }, [traces]);

  return (
    <ol className="flex min-w-0 gap-1.5 overflow-x-auto pb-1 [scrollbar-width:thin]">
      {JEV_PACKS.map((pack) => {
        const trace = latest.get(pack.id);
        const pending = streaming && !trace;
        const tone = trace ? cardTone(trace) : pending ? "shadow" : "unavailable";
        return (
          <li key={pack.id} className="shrink-0">
            <div
              className={cn(
                "flex flex-col items-center rounded-xl border px-2.5 py-1.5 text-center whitespace-nowrap",
                TONE_CLASS[tone],
                pending && "jev-pulse",
              )}
              title={pack.hint}
            >
              <span className="text-[11px] font-medium leading-4 text-foreground">
                {pack.title}
              </span>
              <span className="mt-0.5 text-[10px] tabular-nums text-muted-foreground">
                {trace ? trace.action : pending ? "评估中" : "未触发"}
              </span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function VerticalTimeline({ traces }: { traces: JevTrace[] }) {
  return (
    <ol className="relative flex flex-col gap-3">
      <span
        aria-hidden
        className="absolute bottom-3 left-[7px] top-3 w-px bg-[var(--em-hairline)]"
      />
      {traces.map((trace, index) => {
        const tone = cardTone(trace);
        return (
          <li key={trace.id} className="relative pl-6">
            <span
              aria-hidden
              className={cn(
                "absolute left-0 top-3 h-[15px] w-[15px] rounded-full border-2 border-background",
                TONE_DOT[tone],
              )}
            />
            <DecisionCard trace={trace} index={index} />
          </li>
        );
      })}
    </ol>
  );
}

function TimelineSummary({
  traces,
  streaming,
}: {
  traces: JevTrace[];
  streaming: boolean;
}) {
  const latest = traces[traces.length - 1];
  const meanMs =
    traces.length > 0
      ? traces.reduce((sum, item) => sum + (item.latencyMs || 0), 0) / traces.length
      : 0;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-muted-foreground">
      <span className="whitespace-nowrap tabular-nums text-foreground">{traces.length} 条决策</span>
      {meanMs > 0 ? (
        <span className="tabular-nums">均延迟 {formatLatency(meanMs)}</span>
      ) : null}
      {latest ? <span>最近 {packTitle(latest.pack)}</span> : null}
      {streaming ? <span className="text-[var(--em-gold)]">评估中</span> : null}
    </div>
  );
}

export function JevTimelinePanel() {
  const traces = useJevStore((s) => s.traces);
  const pending = useJevStore((s) => s.pending);
  const streaming = useChatStore((s) => s.isStreaming);
  const empty = jevEmptyCopy({ streaming: streaming || pending, traces });

  return (
    <div className="flex flex-col gap-3 px-4 py-4">
      <StageRail traces={traces} streaming={streaming || pending} />
      <TimelineSummary traces={traces} streaming={streaming || pending} />
      {empty ? (
        <div className="rounded-2xl border border-dashed border-[var(--em-hairline)] bg-[var(--em-fill)] px-4 py-6 text-center">
          <p className="text-[13px] font-medium text-foreground">{empty}</p>
          <p className="mt-1 text-[12px] leading-5 text-muted-foreground">
            Jev 只做回合评估，不参与对话回复。仅观察时只记录，不改动当前行为。
          </p>
        </div>
      ) : (
        <VerticalTimeline traces={traces} />
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
  const streaming = useChatStore((s) => s.isStreaming);
  const empty = jevEmptyCopy({ streaming: streaming || pending, traces });
  const pulsing = streaming || pending;
  if (!chatEnabled) return null;

  return (
    <section
      className={cn(
        "my-2 min-w-0 overflow-hidden rounded-2xl border border-[var(--em-hairline)] bg-background",
        pulsing && traces.length === 0 && "jev-pulse",
      )}
    >
      <button
        type="button"
        onClick={() => setCollapsed(!collapsed)}
        className="flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left hover:bg-[var(--em-fill)]"
      >
        <Activity
          className={cn(
            "h-4 w-4 flex-shrink-0 text-[var(--em-primary)]",
            pulsing && "jev-pulse",
          )}
        />
        <span className="whitespace-nowrap text-[13px] font-semibold text-foreground">Jev 评估</span>
        <span className="hidden min-w-0 truncate text-[12px] text-muted-foreground sm:inline">
          {traces.length > 0 ? `${traces.length} 条决策` : empty || "观察中"}
        </span>
        {pulsing ? (
          <span className="whitespace-nowrap rounded-full bg-amber-500/15 px-1.5 py-px text-[10px] font-medium text-amber-700 dark:text-amber-400">
            评估中
          </span>
        ) : null}
        <ChevronDown
          className={cn(
            "ml-auto h-4 w-4 text-muted-foreground/60 transition-transform",
            collapsed && "-rotate-90",
          )}
        />
      </button>
      {!collapsed && (
        <div className="border-t border-[var(--em-hairline)] px-3 py-2.5">
          <StageRail traces={traces} streaming={streaming || pending} />
          {!compact && traces.length > 0 && (
            <div className="mt-2 flex flex-col gap-2">
              {traces.slice(-3).map((trace, index) => (
                <DecisionCard key={trace.id} trace={trace} index={index} />
              ))}
            </div>
          )}
          {empty ? (
            <p className="mt-2 text-[12px] text-muted-foreground">{empty}</p>
          ) : null}
          <button
            type="button"
            className="mt-2 text-[12px] text-[var(--em-primary)] hover:underline"
            onClick={() => setDrawerOpen(true)}
          >
            打开完整时间线
          </button>
        </div>
      )}
    </section>
  );
}

export function JevTimelineButton() {
  const chatEnabled = useJevStore((s) => s.chatEnabled);
  const traces = useJevStore((s) => s.traces);
  const pending = useJevStore((s) => s.pending);
  const pinned = useJevStore((s) => s.pinned);
  const drawerOpen = useJevStore((s) => s.drawerOpen);
  const setDrawerOpen = useJevStore((s) => s.setDrawerOpen);
  const streaming = useChatStore((s) => s.isStreaming);
  const active = drawerOpen || pinned || traces.length > 0 || pending || streaming;
  if (!chatEnabled) return null;

  return (
    <Button
      variant="ghost"
      size="icon"
      className={cn(
        "h-7 w-7 p-0",
        active && "bg-accent text-accent-foreground",
        (pending || streaming) && "jev-pulse",
      )}
      title="Jev 时间线"
      aria-label="Jev 时间线"
      aria-pressed={drawerOpen || pinned}
      onClick={() => setDrawerOpen(!drawerOpen)}
    >
      <Gauge className="h-4 w-4" />
    </Button>
  );
}

function JevPinButton({ compact = false }: { compact?: boolean }) {
  const pinned = useJevStore((s) => s.pinned);
  const togglePinned = useJevStore((s) => s.togglePinned);
  return (
    <Button
      variant="ghost"
      size={compact ? "icon" : "sm"}
      className={cn("rounded-lg text-[12px]", compact ? "h-8 w-8" : "h-7 gap-1")}
      title={pinned ? "取消钉住" : "钉住侧栏"}
      aria-label={pinned ? "取消钉住" : "钉住侧栏"}
      aria-pressed={pinned}
      onClick={togglePinned}
    >
      {pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
      {compact ? null : pinned ? "取消钉住" : "钉住侧栏"}
    </Button>
  );
}

export function JevTimelineDrawer() {
  const isDesktop = useIsDesktop();
  const chatEnabled = useJevStore((s) => s.chatEnabled);
  const open = useJevStore((s) => s.drawerOpen);
  const pinned = useJevStore((s) => s.pinned);
  const visible = chatEnabled && (open || pinned);

  const close = () => {
    useJevStore.setState({ drawerOpen: false, pinned: false });
  };

  if (isDesktop) {
    if (!visible) return null;
    return (
      <motion.aside
        key="jev-side-panel"
        data-testid="jev-desktop-sidebar"
        className="relative flex h-full w-[360px] max-w-[32vw] flex-shrink-0 flex-col border-l border-border bg-background"
        variants={panelSlideVariants}
        initial="initial"
        animate="animate"
      >
        <div className="flex shrink-0 items-center gap-2 border-b border-border/60 px-3 py-2.5">
          <div
            className="flex h-8 w-8 items-center justify-center rounded-lg"
            style={{ backgroundColor: "var(--em-primary-alpha-10)" }}
          >
            <Activity className="h-4 w-4 text-[var(--em-primary)]" />
          </div>
          <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">Jev 时间线</h2>
          <JevPinButton compact />
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 rounded-lg"
            title="关闭"
            aria-label="关闭"
            onClick={close}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <JevTimelinePanel />
        </div>
      </motion.aside>
    );
  }

  return (
    <SlidePanel
      open={visible}
      onClose={close}
      title="Jev 时间线"
      icon={<Activity className="h-4 w-4 text-[var(--em-primary)]" />}
      headerExtra={<JevPinButton />}
      width={420}
    >
      <JevTimelinePanel />
    </SlidePanel>
  );
}
