"use client";

import { useCallback, lazy, Suspense } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Settings, Server, Package, Plug, SlidersHorizontal, ScrollText, Brain, X } from "lucide-react";
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
  { value: "model", label: "模型", icon: <Server className="size-4" /> },
  { value: "rules", label: "规则", icon: <ScrollText className="size-4" /> },
  { value: "skills", label: "技能", icon: <Package className="size-4" /> },
  { value: "mcp", label: "MCP", icon: <Plug className="size-4" /> },
  { value: "memory", label: "记忆", icon: <Brain className="size-4" /> },
  { value: "runtime", label: "系统", icon: <SlidersHorizontal className="size-4" /> },
];

export function SettingsDialog() {
  const { settingsOpen, settingsTab, openSettings, closeSettings } = useUIStore(
    useShallow((s) => ({
      settingsOpen: s.settingsOpen,
      settingsTab: s.settingsTab,
      openSettings: s.openSettings,
      closeSettings: s.closeSettings,
    }))
  );

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
          {/* ── Tab navigation ── */}
          <nav className="relative flex-shrink-0" role="tablist">
            <div className="flex px-1 sm:px-4 overflow-x-auto scrollbar-none">
              {TAB_META.map((tab) => {
                const isActive = settingsTab === tab.value;
                return (
                  <button
                    key={tab.value}
                    type="button"
                    role="tab"
                    aria-selected={isActive}
                    onClick={() => openSettings(tab.value)}
                    className={`
                      relative flex-1 min-w-[44px] flex items-center justify-center
                      gap-0.5 sm:gap-2 py-2 sm:py-3
                      outline-none select-none whitespace-nowrap
                      transition-colors duration-200
                      flex-col sm:flex-row
                      ${isActive
                        ? "text-foreground"
                        : "text-muted-foreground hover:text-foreground/70"}
                    `}
                  >
                    <span
                      className="transition-colors duration-200"
                      style={{ color: isActive ? "var(--em-primary)" : undefined }}
                    >
                      {tab.icon}
                    </span>
                    <span className="text-[10px] sm:text-[13px] font-medium leading-tight">
                      {tab.label}
                    </span>
                    {isActive && (
                      <motion.div
                        layoutId="settings-tab-underline"
                        className="absolute bottom-0 inset-x-1.5 sm:inset-x-2 h-[2px] rounded-full"
                        style={{ backgroundColor: "var(--em-primary)" }}
                        transition={{ type: "spring", stiffness: 400, damping: 30 }}
                      />
                    )}
                  </button>
                );
              })}
            </div>
            <div className="border-b border-border" />
          </nav>
          <div className="h-2 sm:h-3 flex-shrink-0" />

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
