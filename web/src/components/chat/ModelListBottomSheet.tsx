"use client";

import React, { useCallback, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence, useDragControls, useReducedMotion, type PanInfo } from "framer-motion";
import { Loader2, RefreshCw, X } from "lucide-react";
import { displayModelLabel } from "@/lib/model-display";
import { getProviderColor, inferModelBrand } from "@/lib/provider-brand";
import { ProviderAvatar } from "@/components/settings/model/ProviderLogo";
import type { ModelInfo } from "@/lib/types";
import { Dialog } from "radix-ui";
import { ModelPickerContent, type ModelPickerContentProps } from "./ModelPickerContent";
import styles from "./ModelPickerContent.module.css";

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

const displayLabel = (m: ModelInfo) => displayModelLabel(m);

function providerOf(m: ModelInfo): string {
  return inferModelBrand(m);
}

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

interface ModelListBottomSheetProps extends Pick<ModelPickerContentProps, "loading" | "loadError" | "onReload"> {
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
  onCloseAutoFocus?: (event: Event) => void;
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

function SwitchSheet({ open, onOpenChange, onCloseAutoFocus, ...props }: Omit<ModelListBottomSheetProps, "mode">) {
  const dragControls = useDragControls();
  const reduceMotion = useReducedMotion();
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-black/35 backdrop-blur-[2px]" />
        <Dialog.Content asChild aria-describedby={undefined} onCloseAutoFocus={onCloseAutoFocus}>
          <motion.div className={styles.sheet}
            initial={{ y: reduceMotion ? 0 : "100%" }} animate={{ y: 0 }}
            transition={{ type: "spring", damping: 30, stiffness: 380 }}
            drag="y" dragControls={dragControls} dragListener={false}
            dragConstraints={{ top: 0, bottom: 0 }} dragElastic={{ top: 0, bottom: 0.3 }}
            onDragEnd={(_, info) => { if (info.offset.y > 80 || info.velocity.y > 300) onOpenChange(false); }}>
            <Dialog.Title className="sr-only">选择模型</Dialog.Title>
            <div className={styles.handle} aria-hidden="true" onPointerDown={(event) => dragControls.start(event)} />
            <ModelPickerContent {...props} mobile onClose={() => onOpenChange(false)} />
          </motion.div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
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
