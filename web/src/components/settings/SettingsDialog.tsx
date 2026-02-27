"use client";

import { useRef, useEffect, useState, useCallback, lazy, Suspense } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Settings, Server, Package, Plug, SlidersHorizontal, ScrollText, Brain, X, ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent } from "@/components/ui/tabs";
const ModelTab = lazy(() => import("./ModelTab").then(m => ({ default: m.ModelTab })));
const RulesTab = lazy(() => import("./RulesTab").then(m => ({ default: m.RulesTab })));
const SkillsTab = lazy(() => import("./SkillsTab").then(m => ({ default: m.SkillsTab })));
const MCPTab = lazy(() => import("./MCPTab").then(m => ({ default: m.MCPTab })));
const MemoryTab = lazy(() => import("./MemoryTab").then(m => ({ default: m.MemoryTab })));
const RuntimeTab = lazy(() => import("./RuntimeTab").then(m => ({ default: m.RuntimeTab })));

function TabSpinner() {
  return (
    <div className="flex items-center justify-center py-12 text-muted-foreground">
      <svg className="h-5 w-5 animate-spin mr-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83" />
      </svg>
    </div>
  );
}
import { useShallow } from "zustand/react/shallow";
import { useUIStore } from "@/stores/ui-store";
import { checkModelPlaceholder } from "@/lib/api";

const TAB_META = [
  { value: "model", label: "模型", icon: <Server className="h-3.5 w-3.5" /> },
  { value: "rules", label: "规则", icon: <ScrollText className="h-3.5 w-3.5" /> },
  { value: "skills", label: "技能", icon: <Package className="h-3.5 w-3.5" /> },
  { value: "mcp", label: "MCP", icon: <Plug className="h-3.5 w-3.5" /> },
  { value: "memory", label: "记忆", icon: <Brain className="h-3.5 w-3.5" /> },
  { value: "runtime", label: "系统", icon: <SlidersHorizontal className="h-3.5 w-3.5" /> },
];

function useScrollableTabs() {
  const ref = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);
  const dragState = useRef({ active: false, startX: 0, scrollLeft: 0, moved: false });

  const updateOverflow = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const threshold = 2;
    setCanScrollLeft(el.scrollLeft > threshold);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - threshold);
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    updateOverflow();
    const ro = new ResizeObserver(updateOverflow);
    ro.observe(el);
    el.addEventListener("scroll", updateOverflow, { passive: true });

    const onWheel = (e: WheelEvent) => {
      if (el.scrollWidth <= el.clientWidth) return;
      e.preventDefault();
      el.scrollBy({ left: e.deltaY || e.deltaX, behavior: "smooth" });
    };
    el.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      ro.disconnect();
      el.removeEventListener("scroll", updateOverflow);
      el.removeEventListener("wheel", onWheel);
    };
  }, [updateOverflow]);

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    const el = ref.current;
    if (!el || el.scrollWidth <= el.clientWidth) return;
    dragState.current = { active: true, startX: e.clientX, scrollLeft: el.scrollLeft, moved: false };
    el.setPointerCapture(e.pointerId);
    el.style.cursor = "grabbing";
    el.style.userSelect = "none";
  }, []);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const ds = dragState.current;
    if (!ds.active) return;
    const el = ref.current;
    if (!el) return;
    const dx = e.clientX - ds.startX;
    if (Math.abs(dx) > 3) ds.moved = true;
    el.scrollLeft = ds.scrollLeft - dx;
  }, []);

  const onPointerUp = useCallback((e: React.PointerEvent) => {
    const ds = dragState.current;
    if (!ds.active) return;
    ds.active = false;
    const el = ref.current;
    if (!el) return;
    el.releasePointerCapture(e.pointerId);
    el.style.cursor = "";
    el.style.userSelect = "";
    if (ds.moved) {
      e.preventDefault();
    }
  }, []);

  const scroll = useCallback((dir: "left" | "right") => {
    ref.current?.scrollBy({ left: dir === "left" ? -120 : 120, behavior: "smooth" });
  }, []);

  return { ref, canScrollLeft, canScrollRight, onPointerDown, onPointerMove, onPointerUp, scroll };
}

export function SettingsDialog() {
  const { settingsOpen, settingsTab, openSettings, closeSettings } = useUIStore(
    useShallow((s) => ({
      settingsOpen: s.settingsOpen,
      settingsTab: s.settingsTab,
      openSettings: s.openSettings,
      closeSettings: s.closeSettings,
    }))
  );

  const tabs = useScrollableTabs();

  const handleOpenChange = useCallback((v: boolean) => {
    if (v) {
      openSettings(settingsTab);
    } else {
      closeSettings();
      checkModelPlaceholder()
        .then((result) => {
          const ui = useUIStore.getState();
          if (result?.has_placeholder) {
            ui.setConfigReady(false);
            ui.setConfigPlaceholderItems(result.items);
          } else {
            ui.setConfigReady(true);
            ui.setConfigPlaceholderItems([]);
            ui.setConfigError(null);
          }
        })
        .catch(() => {});
    }
  }, [openSettings, closeSettings, settingsTab]);

  return (
    <Dialog open={settingsOpen} onOpenChange={handleOpenChange}>
      <DialogContent showCloseButton={false} className="!grid-none !flex !flex-col max-w-none sm:max-w-2xl h-[100dvh] sm:h-[70vh] sm:max-h-[85vh] p-0 overflow-hidden rounded-none sm:rounded-lg top-0 left-0 right-0 bottom-0 sm:top-[50%] sm:left-[50%] sm:right-auto sm:bottom-auto translate-x-0 translate-y-0 sm:translate-x-[-50%] sm:translate-y-[-50%] w-full">
        <DialogHeader className="px-4 pt-4 pb-0 sm:px-6 sm:pt-6 flex-shrink-0 flex-row items-center">
          <DialogTitle className="flex items-center gap-2 flex-1">
            <Settings className="h-5 w-5" />
            设置
          </DialogTitle>
          <DialogClose asChild>
            <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0 opacity-70 hover:opacity-100">
              <X className="h-4 w-4" />
              <span className="sr-only">Close</span>
            </Button>
          </DialogClose>
        </DialogHeader>

        <Tabs
          value={settingsTab}
          onValueChange={(v) => openSettings(v)}
          className="pb-4 sm:pb-6 flex flex-col overflow-hidden min-h-0 flex-1"
        >
          {/* Custom tab bar */}
          <div className="relative flex-shrink-0 px-4 sm:px-6">
            {/* Left scroll arrow + fade */}
            <div
              className={`absolute left-4 sm:left-6 top-0 bottom-0 z-10 flex items-center transition-opacity duration-200 ${
                tabs.canScrollLeft ? "opacity-100" : "opacity-0 pointer-events-none"
              }`}
            >
              <button
                type="button"
                tabIndex={-1}
                onClick={() => tabs.scroll("left")}
                className="flex items-center justify-center h-8 w-6 rounded-md bg-background/90 backdrop-blur-sm text-muted-foreground hover:text-foreground shadow-sm border border-border/50 transition-colors"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <div className="w-4 h-full bg-gradient-to-r from-background to-transparent" />
            </div>

            {/* Right scroll arrow + fade */}
            <div
              className={`absolute right-4 sm:right-6 top-0 bottom-0 z-10 flex items-center transition-opacity duration-200 ${
                tabs.canScrollRight ? "opacity-100" : "opacity-0 pointer-events-none"
              }`}
            >
              <div className="w-4 h-full bg-gradient-to-l from-background to-transparent" />
              <button
                type="button"
                tabIndex={-1}
                onClick={() => tabs.scroll("right")}
                className="flex items-center justify-center h-8 w-6 rounded-md bg-background/90 backdrop-blur-sm text-muted-foreground hover:text-foreground shadow-sm border border-border/50 transition-colors"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>

            <div
              ref={tabs.ref}
              onPointerDown={tabs.onPointerDown}
              onPointerMove={tabs.onPointerMove}
              onPointerUp={tabs.onPointerUp}
              onPointerCancel={tabs.onPointerUp}
              className="flex gap-1 overflow-x-auto scrollbar-none touch-pan-x cursor-grab py-1"
            >
              {TAB_META.map((tab) => {
                const isActive = settingsTab === tab.value;
                return (
                  <button
                    key={tab.value}
                    type="button"
                    role="tab"
                    aria-selected={isActive}
                    className={`relative flex items-center gap-1.5 px-3 sm:px-4 py-1.5 text-[11px] sm:text-xs font-medium select-none flex-none whitespace-nowrap transition-all duration-200 outline-none rounded-full border
                      ${isActive
                        ? "text-foreground bg-accent border-border shadow-sm"
                        : "text-muted-foreground hover:text-foreground/80 hover:bg-accent/50 border-transparent"}
                    `}
                    onClick={() => openSettings(tab.value)}
                  >
                    <span
                      className="transition-colors duration-200"
                      style={{ color: isActive ? "var(--em-primary)" : undefined }}
                    >
                      {tab.icon}
                    </span>
                    {tab.label}
                  </button>
                );
              })}
            </div>
            <div className="border-b border-border" />
          </div>
          <div className="h-3 flex-shrink-0" />

          <AnimatePresence mode="wait">
            <motion.div
              key={settingsTab}
              initial={{ opacity: 0, x: 8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -8 }}
              transition={{ duration: 0.15 }}
              className="overflow-y-auto min-h-0 flex-1 px-4 sm:px-6 flex flex-col"
            >
              <Suspense fallback={<TabSpinner />}>
                <TabsContent value="model" className="mt-0 grow shrink-0 flex flex-col" forceMount={settingsTab === "model" ? true : undefined}>
                  {settingsTab === "model" && <ModelTab />}
                </TabsContent>
                <TabsContent value="rules" className="mt-0 grow shrink-0 flex flex-col" forceMount={settingsTab === "rules" ? true : undefined}>
                  {settingsTab === "rules" && <RulesTab />}
                </TabsContent>
                <TabsContent value="skills" className="mt-0 grow shrink-0 flex flex-col" forceMount={settingsTab === "skills" ? true : undefined}>
                  {settingsTab === "skills" && <SkillsTab />}
                </TabsContent>
                <TabsContent value="mcp" className="mt-0 grow shrink-0 flex flex-col" forceMount={settingsTab === "mcp" ? true : undefined}>
                  {settingsTab === "mcp" && <MCPTab />}
                </TabsContent>
                <TabsContent value="memory" className="mt-0 grow shrink-0 flex flex-col" forceMount={settingsTab === "memory" ? true : undefined}>
                  {settingsTab === "memory" && <MemoryTab />}
                </TabsContent>
                <TabsContent value="runtime" className="mt-0 grow shrink-0 flex flex-col" forceMount={settingsTab === "runtime" ? true : undefined}>
                  {settingsTab === "runtime" && <RuntimeTab />}
                </TabsContent>
              </Suspense>
            </motion.div>
          </AnimatePresence>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
