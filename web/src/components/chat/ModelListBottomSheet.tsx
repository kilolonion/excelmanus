"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence, type PanInfo } from "framer-motion";
import { Check, Loader2, Search, Sparkles, RefreshCw, AlertTriangle, X, Crown } from "lucide-react";
import { formatModelIdForDisplay } from "@/lib/model-display";
import { extractProvider, getProviderColor, getProviderDisplayName } from "@/lib/provider-brand";
import type { ModelInfo } from "@/lib/types";

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
    const provider = extractProvider(m.base_url);
    if (!map.has(provider)) map.set(provider, []);
    map.get(provider)!.push(m);
  }
  return Array.from(map.entries()).map(([provider, models]) => ({
    provider,
    models,
  }));
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
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function ModelListBottomSheet({
  open,
  onOpenChange,
  models,
  currentModel,
  onSelect,
  mode = "switch",
  capsMap = {},
  switching = false,
  switchError = null,
}: ModelListBottomSheetProps) {
  const [search, setSearch] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);

  // Reset search when sheet closes — handled via onOpenChange wrapper
  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next) setSearch("");
      onOpenChange(next);
    },
    [onOpenChange],
  );

  // Focus search when opened and enough models
  useEffect(() => {
    if (open && models.length >= 4) {
      // Small delay so the animation doesn't fight focus scroll
      const t = setTimeout(() => searchRef.current?.focus(), 350);
      return () => clearTimeout(t);
    }
  }, [open, models.length]);

  // Lock body scroll when open
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  const close = useCallback(() => handleOpenChange(false), [handleOpenChange]);

  // Swipe-down to dismiss
  const handleDragEnd = useCallback(
    (_: MouseEvent | TouchEvent | PointerEvent, info: PanInfo) => {
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

  const displayLabel = (m: ModelInfo) =>
    m.name === "default"
      ? formatModelIdForDisplay(m.model)
      : formatModelIdForDisplay(m.display_name || m.name);
  const resolvedModel = (m: ModelInfo) =>
    formatModelIdForDisplay(m.resolved_model || m.model);

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            key="model-sheet-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-80 bg-black/40 backdrop-blur-[2px]"
            onClick={close}
          />

          {/* Bottom sheet */}
          <motion.div
            ref={sheetRef}
            key="model-sheet-content"
            initial={{ y: "100%" }}
            animate={{ y: 0 }}
            exit={{ y: "100%" }}
            transition={{ type: "spring", damping: 32, stiffness: 380 }}
            drag="y"
            dragConstraints={{ top: 0 }}
            dragElastic={0.15}
            onDragEnd={handleDragEnd}
            className="fixed inset-x-0 bottom-0 z-81 flex flex-col bg-background rounded-t-2xl shadow-2xl"
            style={{ maxHeight: "85dvh", touchAction: "none" }}
          >
            {/* Drag handle */}
            <div className="flex justify-center pt-2.5 pb-1 shrink-0">
              <div className="h-1 w-10 rounded-full bg-muted-foreground/20" />
            </div>

            {/* Header */}
            <div className="flex items-center gap-2.5 px-5 pb-3 shrink-0">
              {mode === "switch" ? (
                <Sparkles className="h-4 w-4 shrink-0" style={{ color: "var(--em-primary)" }} />
              ) : (
                <RefreshCw className="h-4 w-4 shrink-0" style={{ color: "var(--em-primary)" }} />
              )}
              <span className="text-base font-semibold flex-1">
                {mode === "switch" ? "模型选择" : "选择模型重试"}
              </span>
              <span className="text-xs text-muted-foreground/50 tabular-nums">
                {models.length} 个可用
              </span>
              <button
                type="button"
                onClick={close}
                className="h-8 w-8 flex items-center justify-center rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors -mr-1 shrink-0"
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Search */}
            {showSearch && (
              <div className="px-4 pb-3 shrink-0" onPointerDownCapture={(e) => e.stopPropagation()}>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground/40" />
                  <input
                    ref={searchRef}
                    type="text"
                    placeholder="搜索模型..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="w-full h-10 pl-10 pr-4 text-sm bg-muted/30 rounded-xl border-0 outline-none placeholder:text-muted-foreground/40 focus:bg-muted/50 transition-colors"
                    onPointerDown={(e) => e.stopPropagation()}
                  />
                </div>
              </div>
            )}

            {/* Divider */}
            <div className="h-px bg-border/40 mx-4 shrink-0" />

            {/* Model list — scrollable area */}
            <div
              ref={scrollRef}
              className="flex-1 min-h-0 overflow-y-auto overscroll-contain py-2 model-selector-scroll"
              style={{ WebkitOverflowScrolling: "touch" }}
              onPointerDownCapture={(e) => e.stopPropagation()}
            >
              {/* ── Active model hero card ── */}
              {(() => {
                const activeModel = models.find((m) => m.name === currentModel);
                if (!activeModel || search.trim()) return null;
                const activeProvider = extractProvider(activeModel.base_url);
                const providerColor = getProviderColor(activeProvider);
                const isUnhealthy = capsMap[activeModel.name]?.healthy === false;
                return (
                  <div className="mx-3 mt-1 mb-2.5">
                    <div
                      className="relative overflow-hidden rounded-xl p-px"
                      style={{
                        background: `linear-gradient(135deg, ${providerColor}40, ${providerColor}18)`,
                      }}
                    >
                      <div
                        className="relative rounded-[11px] px-4 py-3 flex items-center gap-3 cursor-default"
                        style={{
                          background: `linear-gradient(135deg, ${providerColor}08, transparent)`,
                        }}
                      >
                        {/* Subtle glow */}
                        <div
                          className="absolute -top-8 -right-8 h-20 w-20 rounded-full blur-2xl opacity-20 pointer-events-none"
                          style={{ backgroundColor: providerColor }}
                        />
                        {/* Crown + dot */}
                        <div className="relative shrink-0">
                          <span
                            className="h-3 w-3 rounded-full block"
                            style={{
                              backgroundColor: isUnhealthy ? "var(--em-error)" : providerColor,
                              boxShadow: isUnhealthy
                                ? "0 0 8px var(--em-error)"
                                : `0 0 8px ${providerColor}50`,
                            }}
                          />
                          <Crown
                            className="absolute -top-1.5 -right-1.5 h-2.5 w-2.5"
                            style={{ color: providerColor, opacity: 0.7 }}
                          />
                        </div>
                        {/* Info */}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span className="text-[15px] font-semibold leading-tight truncate">
                              {displayLabel(activeModel)}
                            </span>
                            <span
                              className="text-[9px] px-1.5 py-0.5 rounded-full font-semibold uppercase tracking-wider shrink-0"
                              style={{
                                backgroundColor: `${providerColor}18`,
                                color: providerColor,
                              }}
                            >
                              主模型
                            </span>
                          </div>
                          <div className="flex items-center gap-1 mt-0.5">
                            {activeModel.name !== "default" && activeModel.name !== resolvedModel(activeModel) && !isUnhealthy && (
                              <span className="text-[11px] text-muted-foreground/50 font-mono truncate">
                                {resolvedModel(activeModel)}
                              </span>
                            )}
                            {activeModel.description && (
                              <span className="text-[11px] text-muted-foreground/40 truncate">
                                {activeModel.name !== "default" && activeModel.name !== resolvedModel(activeModel) && !isUnhealthy
                                  ? `· ${activeModel.description}`
                                  : activeModel.description}
                              </span>
                            )}
                          </div>
                        </div>
                        {/* Check */}
                        <span
                          className="h-6 w-6 rounded-full flex items-center justify-center shrink-0"
                          style={{ backgroundColor: `${providerColor}20` }}
                        >
                          <Check className="h-3.5 w-3.5" style={{ color: providerColor }} />
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })()}

              {/* ── Provider groups ── */}
              {groups.map((group, gi) => {
                const remainingModels = !search.trim()
                  ? group.models.filter((m) => m.name !== currentModel)
                  : group.models;
                if (remainingModels.length === 0) return null;
                const color = getProviderColor(group.provider);
                return (
                  <div key={group.provider}>
                    {gi > 0 && <div className="h-px mx-5 my-1.5 bg-border/30" />}
                    {/* Provider header */}
                    <div className="flex items-center gap-2 px-5 pt-3 pb-1.5">
                      <span
                        className="h-2 w-2 rounded-full shrink-0"
                        style={{ backgroundColor: color }}
                      />
                      <span
                        className="text-[11px] font-semibold uppercase tracking-widest"
                        style={{ color }}
                      >
                        {getProviderDisplayName(group.provider)}
                      </span>
                    </div>
                    {/* Model items — large touch targets */}
                    {group.models.filter((m) => !search.trim() ? m.name !== currentModel : true).map((m) => {
                      const isSelected = m.name === currentModel;
                      const isUnhealthy = capsMap[m.name]?.healthy === false;
                      const hasHealthData = m.name in capsMap;
                      return (
                        <button
                          key={m.name}
                          onClick={() => onSelect(m.name)}
                          disabled={switching}
                          className={[
                            "w-full text-left px-5 py-3 min-h-[52px] flex items-center gap-3",
                            "transition-all duration-150 ease-out cursor-pointer",
                            "border-l-[3px] border-l-transparent",
                            "active:bg-accent/70",
                            isSelected
                              ? "bg-(--em-primary-alpha-06) border-l-(--em-primary)!"
                              : "hover:bg-accent/50 hover:border-l-(--em-primary-alpha-25)",
                            switching ? "opacity-50 pointer-events-none" : "",
                          ].join(" ")}
                        >
                          {/* Health / provider dot */}
                          {mode === "switch" ? (
                            <span
                              className="h-2.5 w-2.5 rounded-full shrink-0 mt-0.5 transition-colors duration-200"
                              style={{
                                backgroundColor: isUnhealthy
                                  ? "var(--em-error)"
                                  : hasHealthData && capsMap[m.name]?.healthy === true
                                    ? "var(--em-primary)"
                                    : "var(--muted-foreground)",
                                opacity: hasHealthData ? 1 : 0.25,
                              }}
                            />
                          ) : (
                            <span
                              className="h-2.5 w-2.5 rounded-full shrink-0"
                              style={{ backgroundColor: color, opacity: 0.6 }}
                            />
                          )}

                          {/* Model info */}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1.5">
                              <span
                                className={`text-sm leading-tight ${
                                  isSelected ? "font-semibold" : "font-medium"
                                }`}
                              >
                                {displayLabel(m)}
                              </span>
                              {isUnhealthy && mode === "switch" && (
                                <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-px rounded-full bg-destructive/10 text-destructive font-medium">
                                  <AlertTriangle className="h-2 w-2" />
                                  不可用
                                </span>
                              )}
                              {isSelected && (
                                <span
                                  className="text-[10px] px-1.5 py-px rounded-full font-medium"
                                  style={{
                                    backgroundColor: "var(--em-primary-alpha-10)",
                                    color: "var(--em-primary)",
                                  }}
                                >
                                  当前
                                </span>
                              )}
                            </div>
                            <div className="flex items-center gap-1 mt-0.5">
                              {m.name !== "default" && m.name !== resolvedModel(m) && !isUnhealthy && (
                                <span className="text-[11px] text-muted-foreground/50 font-mono truncate">
                                  {resolvedModel(m)}
                                </span>
                              )}
                              {m.description && (
                                <span className="text-[11px] text-muted-foreground/40 truncate">
                                  {m.name !== "default" && m.name !== resolvedModel(m) && !isUnhealthy
                                    ? `· ${m.description}`
                                    : m.description}
                                </span>
                              )}
                            </div>
                          </div>

                          {/* Selection check */}
                          {isSelected && (
                            <span
                              className="h-6 w-6 rounded-full flex items-center justify-center shrink-0"
                              style={{ backgroundColor: "var(--em-primary-alpha-15)" }}
                            >
                              <Check className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />
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
                <div className="px-5 py-10 text-center">
                  <Search className="h-5 w-5 text-muted-foreground/25 mx-auto mb-2" />
                  <p className="text-sm text-muted-foreground/40">未找到匹配的模型</p>
                </div>
              )}
              {models.length === 0 && (
                <div className="px-5 py-10 text-center">
                  <Loader2 className="h-5 w-5 text-muted-foreground/25 mx-auto mb-2 animate-spin" />
                  <p className="text-sm text-muted-foreground/40">加载模型列表...</p>
                </div>
              )}
            </div>

            {/* Error banner */}
            {switchError && (
              <div className="px-5 py-3 border-t border-destructive/20 bg-destructive/5 flex items-center gap-2 shrink-0">
                <AlertTriangle className="h-3.5 w-3.5 text-destructive shrink-0" />
                <span className="text-xs text-destructive truncate">{switchError}</span>
              </div>
            )}

            {/* Safe area padding for devices with home indicator */}
            <div className="shrink-0" style={{ height: "env(safe-area-inset-bottom, 0px)" }} />
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
