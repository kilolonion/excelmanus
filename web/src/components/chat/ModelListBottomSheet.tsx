"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence, type PanInfo } from "framer-motion";
import { Check, Loader2, Search, Sparkles, RefreshCw, AlertTriangle, X } from "lucide-react";
import { displayModelLabel } from "@/lib/model-display";
import { getProviderColor, getProviderDisplayName, inferModelBrand } from "@/lib/provider-brand";
import { ProviderAvatar } from "@/components/settings/model/ProviderLogo";
import type { ModelInfo } from "@/lib/types";
import { SubscriptionSettingsEntry } from "@/components/settings/model/SubscriptionSettingsEntry";

/**
 * Portal overlay to document.body so `position: fixed` is viewport-relative.
 * The topbar uses `backdrop-filter` + `overflow-hidden`, which otherwise
 * traps fixed descendants and makes the sheet expand inside the header.
 */
function BottomSheetPortal({ children }: { children: React.ReactNode }) {
  if (typeof document === "undefined") return null;
  return createPortal(children, document.body);
}

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface ProviderGroup {
  provider: string;
  models: ModelInfo[];
}

function groupByProvider(models: ModelInfo[]): ProviderGroup[] {
  const map = new Map<string, ModelInfo[]>();
  for (const m of models) {
    const provider = inferModelBrand(m);
    if (!map.has(provider)) map.set(provider, []);
    map.get(provider)!.push(m);
  }
  return Array.from(map.entries()).map(([provider, models]) => ({
    provider,
    models,
  }));
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const displayLabel = (m: ModelInfo) => displayModelLabel(m);

function providerOf(m: ModelInfo): string {
  return inferModelBrand(m);
}

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

interface ModelListBottomSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  models: ModelInfo[];
  currentModel: string | null;
  onSelect: (modelName: string) => void;
  /** Header mode: "switch" for model switching, "retry" for retry-with-model */
  mode?: "switch" | "retry";
  /** Health capability map — only used in "switch" mode */
  capsMap?: Record<string, { healthy: boolean | null; health_error: string }>;
  /** Whether switching is in progress */
  switching?: boolean;
  /** Error message */
  switchError?: string | null;
}

/* ------------------------------------------------------------------ */
/*  Compact Retry Sheet                                                */
/* ------------------------------------------------------------------ */

function CompactRetrySheet({
  open,
  onOpenChange,
  models,
  currentModel,
  onSelect,
}: Pick<ModelListBottomSheetProps, "open" | "onOpenChange" | "models" | "currentModel" | "onSelect">) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const close = useCallback(() => onOpenChange(false), [onOpenChange]);

  // Lock body scroll when open
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  // Scroll-aware drag: only allow dismiss when scrolled to top
  const handleDragEnd = useCallback(
    (_: MouseEvent | TouchEvent | PointerEvent, info: PanInfo) => {
      const atTop = !scrollRef.current || scrollRef.current.scrollTop <= 0;
      if (atTop && (info.offset.y > 60 || info.velocity.y > 300)) {
        close();
      }
    },
    [close],
  );

  return (
    <BottomSheetPortal>
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            key="retry-sheet-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 z-[80] bg-black/40 backdrop-blur-[2px]"
            onClick={close}
          />

          {/* Compact bottom sheet */}
          <motion.div
            key="retry-sheet-content"
            initial={{ y: "100%" }}
            animate={{ y: 0 }}
            exit={{ y: "100%" }}
            transition={{ type: "spring", damping: 28, stiffness: 400 }}
            drag="y"
            dragConstraints={{ top: 0 }}
            dragElastic={0.12}
            onDragEnd={handleDragEnd}
            className="em-model-sheet fixed inset-x-0 bottom-0 z-[81] flex min-h-[280px] h-[min(70dvh,520px)] max-h-[78dvh] flex-col rounded-t-2xl border border-b-0 border-[var(--em-line)] bg-[var(--em-panel)] shadow-2xl overflow-hidden"
            style={{ touchAction: "none" }}
          >
            {/* Drag handle */}
            <div className="flex justify-center pt-2.5 pb-1 shrink-0">
              <div className="h-1 w-10 rounded-full bg-muted-foreground/20" />
            </div>

            {/* Header */}
            <div className="flex items-center gap-2 px-4 pb-2.5 shrink-0">
              <RefreshCw className="h-4 w-4 shrink-0" style={{ color: "var(--em-primary)" }} />
              <span className="text-sm font-semibold flex-1">选择模型重试</span>
              <button
                type="button"
                onClick={close}
                className="h-7 w-7 flex items-center justify-center rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors -mr-0.5 shrink-0"
                aria-label="关闭"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>

            {/* Divider */}
            <div className="h-px bg-border/40 mx-3 shrink-0" />

            {/* Flat model list */}
            <div
              ref={scrollRef}
              className="flex-1 min-h-0 overflow-y-auto overscroll-contain py-1 model-selector-scroll"
              style={{ WebkitOverflowScrolling: "touch" }}
              onPointerDownCapture={(e) => e.stopPropagation()}
            >
              {models.map((m) => {
                const isCurrent = m.name === currentModel;
                const provider = providerOf(m);
                const providerColor = getProviderColor(provider);
                return (
                  <button
                    key={m.name}
                    onClick={() => { onSelect(m.name); close(); }}
                    className={[
                      "w-full text-left px-4 py-2.5 min-h-[48px] flex items-center gap-3",
                      "transition-all duration-150 ease-out cursor-pointer",
                      "active:bg-accent/70",
                      isCurrent
                        ? "bg-[var(--em-primary-alpha-06)]"
                        : "hover:bg-accent/50",
                    ].join(" ")}
                  >
                    <ProviderAvatar
                      id={provider}
                      label={displayLabel(m)}
                      color={providerColor}
                      className="h-8 w-8"
                      iconClassName="h-4 w-4"
                    />
                    <span className={`text-sm flex-1 min-w-0 truncate ${isCurrent ? "font-semibold" : "font-medium"}`}>
                      {displayLabel(m)}
                    </span>
                    {isCurrent && (
                      <span
                        className="text-[10px] px-1.5 py-px rounded-full font-medium shrink-0"
                        style={{
                          backgroundColor: "var(--em-primary-alpha-10)",
                          color: "var(--em-primary)",
                        }}
                      >
                        当前
                      </span>
                    )}
                  </button>
                );
              })}
              {models.length === 0 && (
                <div className="px-4 py-8 text-center">
                  <Loader2 className="h-4 w-4 text-muted-foreground/25 mx-auto mb-1.5 animate-spin" />
                  <p className="text-xs text-muted-foreground/40">加载模型列表...</p>
                </div>
              )}
            </div>

            {/* Safe area padding */}
            <div className="shrink-0" style={{ height: "env(safe-area-inset-bottom, 0px)" }} />
          </motion.div>
        </>
      )}
    </AnimatePresence>
    </BottomSheetPortal>
  );
}

/* ------------------------------------------------------------------ */
/*  Switch Sheet                                                       */
/* ------------------------------------------------------------------ */

const SHEET_HEIGHT = "min(70dvh, 520px)";

function SwitchSheet({
  open,
  onOpenChange,
  models,
  currentModel,
  onSelect,
  capsMap = {},
  switching = false,
  switchError = null,
}: Omit<ModelListBottomSheetProps, "mode">) {
  const [search, setSearch] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const close = useCallback(() => {
    onOpenChange(false);
    setSearch("");
  }, [onOpenChange]);

  // Lock body scroll when open
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  // Scroll-aware drag: only allow dismiss when scrolled to top
  const handleDragEnd = useCallback(
    (_: MouseEvent | TouchEvent | PointerEvent, info: PanInfo) => {
      const atTop = !scrollRef.current || scrollRef.current.scrollTop <= 0;
      if (!atTop) return;

      if (info.offset.y > 80 || info.velocity.y > 300) {
        close();
      }
    },
    [close],
  );

  // Filter
  const filtered = React.useMemo(() => {
    if (!search.trim()) return models;
    const q = search.toLowerCase();
    return models.filter(
      (m) =>
        m.name.toLowerCase().includes(q) ||
        m.model.toLowerCase().includes(q) ||
        m.description?.toLowerCase().includes(q),
    );
  }, [models, search]);

  const groups = groupByProvider(filtered);
  const showSearch = models.length >= 4;
  const showProviderHeaders = groups.length > 1;

  return (
    <BottomSheetPortal>
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            key="switch-sheet-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 z-[80] bg-black/40 backdrop-blur-[2px]"
            onClick={close}
          />

          <motion.div
            key="switch-sheet-content"
            initial={{ y: "100%" }}
            animate={{ y: 0 }}
            exit={{ y: "100%" }}
            transition={{ type: "spring", damping: 28, stiffness: 400 }}
            drag="y"
            dragConstraints={{ top: 0 }}
            dragElastic={0.12}
            onDragEnd={handleDragEnd}
            className="em-model-sheet fixed inset-x-0 bottom-0 z-[81] flex flex-col bg-background rounded-t-2xl shadow-2xl overflow-hidden"
            style={{
              height: SHEET_HEIGHT,
              maxHeight: SHEET_HEIGHT,
              touchAction: "none",
            }}
          >
            {/* Drag handle */}
            <div className="flex justify-center pt-2.5 pb-1 shrink-0">
              <div className="h-1 w-10 rounded-full bg-muted-foreground/20" />
            </div>

            {/* Header */}
            <div className="flex items-center gap-2.5 px-4 pb-2 shrink-0">
              <Sparkles className="h-4 w-4 shrink-0" style={{ color: "var(--em-primary)" }} />
              <span className="text-sm font-semibold flex-1">模型选择</span>
              <span className="text-[11px] text-muted-foreground/50 tabular-nums">
                {models.length} 个可用
              </span>
              <button
                type="button"
                onClick={close}
                className="h-7 w-7 flex items-center justify-center rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors -mr-0.5 shrink-0"
                aria-label="关闭"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>

            {/* Search — shown when the list is long enough to need it */}
            <AnimatePresence>
              {showSearch && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="overflow-hidden shrink-0"
                >
                  <div className="px-3 pb-2" onPointerDownCapture={(e) => e.stopPropagation()}>
                    <div className="relative">
                      <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground/40" />
                      <input
                        ref={searchRef}
                        type="text"
                        placeholder="搜索模型..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="w-full h-9 pl-9 pr-3 text-sm bg-muted/30 rounded-xl border-0 outline-none placeholder:text-muted-foreground/40 focus:bg-muted/50 transition-colors"
                        onPointerDown={(e) => e.stopPropagation()}
                      />
                    </div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Divider */}
            <div className="h-px bg-border/40 mx-3 shrink-0" />

            {/* Model list */}
            <div
              ref={scrollRef}
              className="flex-1 min-h-0 overflow-y-auto overscroll-contain py-1 model-selector-scroll"
              style={{ WebkitOverflowScrolling: "touch" }}
              onPointerDownCapture={(e) => e.stopPropagation()}
            >
              {/* ── Provider groups ── */}
              {groups.map((group, gi) => {
                const color = getProviderColor(group.provider);
                return (
                  <div key={group.provider}>
                    {gi > 0 && <div className="h-px mx-4 my-1 bg-border/30" />}
                    {/* Provider header — hidden when single provider */}
                    {showProviderHeaders && (
                      <div className="flex items-center gap-2 px-4 pt-2 pb-1">
                        <ProviderAvatar
                          id={group.provider}
                          label={getProviderDisplayName(group.provider)}
                          color={color}
                          className="h-4 w-4 rounded-md"
                          iconClassName="h-2.5 w-2.5"
                        />
                        <span
                          className="text-[10px] font-semibold uppercase tracking-widest"
                          style={{ color }}
                        >
                          {getProviderDisplayName(group.provider)}
                        </span>
                      </div>
                    )}
                    {/* Model items — 44px touch targets */}
                    {group.models.map((m) => {
                      const isSelected = m.name === currentModel;
                      const isUnhealthy = capsMap[m.name]?.healthy === false;
                      return (
                        <button
                          key={m.name}
                          onClick={() => onSelect(m.name)}
                          disabled={switching}
                          className={[
                            "w-full text-left px-4 py-2 min-h-[48px] flex items-center gap-3",
                            "transition-all duration-150 ease-out cursor-pointer",
                            "active:bg-accent/70",
                            isSelected
                              ? "bg-[var(--em-primary-alpha-06)]"
                              : "hover:bg-accent/50",
                            switching ? "opacity-50 pointer-events-none" : "",
                          ].join(" ")}
                        >
                          <ProviderAvatar
                            id={group.provider}
                            label={displayLabel(m)}
                            color={isUnhealthy ? undefined : color}
                            className="h-8 w-8"
                            iconClassName="h-4 w-4"
                          />

                          {/* Model info */}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1.5">
                              <span
                                className={`text-sm leading-tight truncate ${
                                  isSelected ? "font-semibold" : "font-medium"
                                }`}
                              >
                                {displayLabel(m)}
                              </span>
                              {isUnhealthy && (
                                <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-px rounded-full bg-destructive/10 text-destructive font-medium">
                                  <AlertTriangle className="h-2 w-2" />
                                  不可用
                                </span>
                              )}
                            </div>
                            {m.description && (
                              <span className="text-[11px] text-muted-foreground/40 truncate block mt-0.5">
                                {m.description}
                              </span>
                            )}
                          </div>

                          {/* Selection check */}
                          {isSelected && (
                            <span
                              className="h-5 w-5 rounded-full flex items-center justify-center shrink-0"
                              style={{ backgroundColor: "var(--em-primary-alpha-15)" }}
                            >
                              <Check className="h-3 w-3" style={{ color: "var(--em-primary)" }} />
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                );
              })}

              {/* Empty states */}
              {filtered.length === 0 && models.length > 0 && (
                <div className="px-4 py-8 text-center">
                  <Search className="h-5 w-5 text-muted-foreground/25 mx-auto mb-2" />
                  <p className="text-xs text-muted-foreground/40">未找到匹配的模型</p>
                </div>
              )}
              {models.length === 0 && (
                <div className="px-4 py-8 text-center">
                  <Loader2 className="h-4 w-4 text-muted-foreground/25 mx-auto mb-1.5 animate-spin" />
                  <p className="text-xs text-muted-foreground/40">加载模型列表...</p>
                </div>
              )}
            </div>

            <div className="shrink-0 border-t border-border/60 p-2">
              <SubscriptionSettingsEntry onNavigate={() => onOpenChange(false)} />
            </div>
            {/* Error banner */}
            {switchError && (
              <div className="px-4 py-2 border-t border-destructive/20 bg-destructive/5 flex items-center gap-2 shrink-0">
                <AlertTriangle className="h-3 w-3 text-destructive shrink-0" />
                <span className="text-[11px] text-destructive truncate">{switchError}</span>
              </div>
            )}

            {/* Safe area padding */}
            <div className="shrink-0" style={{ height: "env(safe-area-inset-bottom, 0px)" }} />
          </motion.div>
        </>
      )}
    </AnimatePresence>
    </BottomSheetPortal>
  );
}

/* ------------------------------------------------------------------ */
/*  Public Component — routes to compact or two-stage                  */
/* ------------------------------------------------------------------ */

export function ModelListBottomSheet(props: ModelListBottomSheetProps) {
  if (props.mode === "retry") {
    return <CompactRetrySheet {...props} />;
  }
  return <SwitchSheet {...props} />;
}
