"use client";

import { useRef, useEffect, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Settings, Server, Package, Plug, SlidersHorizontal, ScrollText, Brain } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ModelTab } from "./ModelTab";
import { RulesTab } from "./RulesTab";
import { SkillsTab } from "./SkillsTab";
import { MCPTab } from "./MCPTab";
import { MemoryTab } from "./MemoryTab";
import { RuntimeTab } from "./RuntimeTab";
import { useShallow } from "zustand/react/shallow";
import { useUIStore } from "@/stores/ui-store";

const TAB_META = [
  { value: "model", label: "模型", icon: <Server className="h-3.5 w-3.5" /> },
  { value: "rules", label: "规则", icon: <ScrollText className="h-3.5 w-3.5" /> },
  { value: "skills", label: "技能", icon: <Package className="h-3.5 w-3.5" /> },
  { value: "mcp", label: "MCP", icon: <Plug className="h-3.5 w-3.5" /> },
  { value: "memory", label: "记忆", icon: <Brain className="h-3.5 w-3.5" /> },
  { value: "runtime", label: "运行时", icon: <SlidersHorizontal className="h-3.5 w-3.5" /> },
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

  return { ref, canScrollLeft, canScrollRight, onPointerDown, onPointerMove, onPointerUp };
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

  return (
    <Dialog open={settingsOpen} onOpenChange={(v) => (v ? openSettings(settingsTab) : closeSettings())}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openSettings("model")}>
          <Settings className="h-4 w-4" />
        </Button>
      </DialogTrigger>
      <DialogContent className="!grid-none !flex !flex-col max-w-[calc(100vw-1rem)] sm:max-w-2xl max-h-[85dvh] sm:max-h-[85vh] p-0 overflow-hidden">
        <DialogHeader className="px-6 pt-6 pb-0 flex-shrink-0">
          <DialogTitle className="flex items-center gap-2">
            <Settings className="h-5 w-5" />
            设置
          </DialogTitle>
        </DialogHeader>

        <Tabs
          value={settingsTab}
          onValueChange={(v) => openSettings(v)}
          className="px-6 pb-6 flex flex-col overflow-hidden min-h-0 flex-1"
        >
          <div className="relative mb-4 flex-shrink-0">
            {/* Left fade */}
            <div
              className="pointer-events-none absolute left-0 top-0 bottom-0 w-6 z-10 rounded-l-lg transition-opacity duration-200"
              style={{
                opacity: tabs.canScrollLeft ? 1 : 0,
                background: "linear-gradient(to right, var(--color-muted), transparent)",
              }}
            />
            {/* Right fade */}
            <div
              className="pointer-events-none absolute right-0 top-0 bottom-0 w-6 z-10 rounded-r-lg transition-opacity duration-200"
              style={{
                opacity: tabs.canScrollRight ? 1 : 0,
                background: "linear-gradient(to left, var(--color-muted), transparent)",
              }}
            />
            <TabsList
              ref={tabs.ref}
              onPointerDown={tabs.onPointerDown}
              onPointerMove={tabs.onPointerMove}
              onPointerUp={tabs.onPointerUp}
              onPointerCancel={tabs.onPointerUp}
              className="w-full justify-start flex-shrink-0 overflow-x-auto scrollbar-none touch-pan-x cursor-grab"
            >
              {TAB_META.map((tab) => (
                <TabsTrigger key={tab.value} value={tab.value} className="gap-1.5 text-xs select-none">
                  {tab.icon}
                  {tab.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>

          <AnimatePresence mode="wait">
            <motion.div
              key={settingsTab}
              initial={{ opacity: 0, x: 8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -8 }}
              transition={{ duration: 0.15 }}
              className="overflow-y-auto min-h-0 flex-1"
            >
              <TabsContent value="model" className="mt-0" forceMount={settingsTab === "model" ? true : undefined}>
                {settingsTab === "model" && <ModelTab />}
              </TabsContent>
              <TabsContent value="rules" className="mt-0" forceMount={settingsTab === "rules" ? true : undefined}>
                {settingsTab === "rules" && <RulesTab />}
              </TabsContent>
              <TabsContent value="skills" className="mt-0" forceMount={settingsTab === "skills" ? true : undefined}>
                {settingsTab === "skills" && <SkillsTab />}
              </TabsContent>
              <TabsContent value="mcp" className="mt-0" forceMount={settingsTab === "mcp" ? true : undefined}>
                {settingsTab === "mcp" && <MCPTab />}
              </TabsContent>
              <TabsContent value="memory" className="mt-0" forceMount={settingsTab === "memory" ? true : undefined}>
                {settingsTab === "memory" && <MemoryTab />}
              </TabsContent>
              <TabsContent value="runtime" className="mt-0" forceMount={settingsTab === "runtime" ? true : undefined}>
                {settingsTab === "runtime" && <RuntimeTab />}
              </TabsContent>
            </motion.div>
          </AnimatePresence>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
