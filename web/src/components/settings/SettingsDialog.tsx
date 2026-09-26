"use client";

import { useCallback, useEffect, lazy, Suspense } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Settings, Server, Package, SlidersHorizontal, X, ArrowUpCircle, ShieldCheck } from "lucide-react";
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
const PluginsTab = lazy(() => import("./PluginsTab").then(m => ({ default: m.PluginsTab })));
const RuntimeTab = lazy(() => import("./RuntimeTab").then(m => ({ default: m.RuntimeTab })));
const VersionTab = lazy(() => import("./VersionTab").then(m => ({ default: m.VersionTab })));
const AccessTab = lazy(() => import("./AccessTab").then(m => ({ default: m.AccessTab })));

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
import { useOnboardingStore } from "@/stores/onboarding-store";
import { checkModelPlaceholder } from "@/lib/api";
import { SettingsSearch } from "./SettingsSearch";
import { useSettingsDraftStore } from "@/stores/settings-draft-store";

const TAB_META = [
  { value: "model", label: "模型与连接", coachId: "coach-settings-tab-model", icon: <Server className="size-4" /> },
  { value: "plugins", label: "扩展与记忆", coachId: "coach-settings-tab-plugins", icon: <Package className="size-4" /> },
  { value: "runtime", label: "偏好与运行", coachId: "coach-settings-tab-runtime", icon: <SlidersHorizontal className="size-4" /> },
  { value: "access", label: "访问与安全", coachId: "coach-settings-tab-access", icon: <ShieldCheck className="size-4" /> },
  { value: "version", label: "关于与更新", coachId: "coach-settings-tab-version", icon: <ArrowUpCircle className="size-4" /> },
];

const PLUGIN_TAB_VALUES = ["rules", "skills", "mcp", "memory"] as const;
type PluginTabValue = (typeof PLUGIN_TAB_VALUES)[number];

function isPluginTab(value: string): value is PluginTabValue {
  return PLUGIN_TAB_VALUES.includes(value as PluginTabValue);
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

  const draftCount = useSettingsDraftStore((state) => Object.keys(state.drafts).length);
  useEffect(() => {
    if (!draftCount) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [draftCount]);

  const isGuideLocked = useOnboardingStore((s) => s.isGuideLocked);
  const primaryTab = isPluginTab(settingsTab) || settingsTab === "plugins" ? "plugins" : settingsTab;
  const pluginTab: PluginTabValue = isPluginTab(settingsTab) ? settingsTab : "skills";

  const handleOpenChange = useCallback((v: boolean) => {
    if (v) {
      openSettings(settingsTab);
    } else {
      if (isGuideLocked) {
        useOnboardingStore.getState().skipAll();
        closeSettings();
        return;
      }
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
  }, [openSettings, closeSettings, settingsTab, isGuideLocked]);

  return (
    <Dialog open={settingsOpen} onOpenChange={handleOpenChange} modal={!isGuideLocked}>
      <DialogContent
        aria-describedby={undefined}
        showCloseButton={false}
        onInteractOutside={(event) => { if (isGuideLocked) event.preventDefault(); }}
        onOpenAutoFocus={(event) => { if (isGuideLocked) event.preventDefault(); }}
        onCloseAutoFocus={(event) => { if (isGuideLocked) event.preventDefault(); }}
        className="em-settings-dialog !grid-none !flex !flex-col max-w-none max-h-none h-auto sm:max-w-5xl sm:h-auto min-h-0 p-0 gap-0 overflow-hidden rounded-none sm:rounded-2xl top-0 left-0 right-0 bottom-0 sm:top-[50%] sm:left-[50%] sm:right-auto sm:bottom-auto translate-x-0 translate-y-0 sm:translate-x-[-50%] sm:translate-y-[-50%] w-full">
        <DialogTitle className="sr-only">设置</DialogTitle>
        <DialogClose asChild>
          <Button variant="ghost" size="icon" className="em-settings-close h-8 w-8 rounded-full opacity-70 hover:opacity-100">
            <X className="h-4 w-4" />
            <span className="sr-only">关闭设置</span>
          </Button>
        </DialogClose>
        <Tabs
          value={primaryTab}
          onValueChange={(v) => openSettings(v)}
          className="flex flex-col overflow-hidden min-h-0 flex-1"
        >
          <div className="em-settings-body min-h-0 flex-1">
            <aside className="em-settings-sidebar hidden sm:flex" aria-label="设置分类" data-coach-id="coach-settings-tabs">
              <DialogHeader className="em-settings-sidebar-header flex-shrink-0 flex-row items-start">
                <h2 className="flex items-center gap-2 flex-1 min-w-0">
                  <span className="em-settings-title-icon"><Settings className="h-4 w-4" /></span>
                  <span className="min-w-0">
                    <span className="block text-sm font-semibold tracking-tight">设置</span>
                    <span className="mt-0.5 block text-[10px] font-normal leading-snug text-muted-foreground">连接模型，调整工作方式</span>
                  </span>
                </h2>
              </DialogHeader>
              <p className="em-settings-sidebar-label">工作区</p>
              {TAB_META.map((tab) => {
                const isActive = primaryTab === tab.value;
                return (
                  <button
                    key={tab.value}
                    type="button"
                    role="tab"
                    aria-selected={isActive}
                    data-coach-id={tab.coachId}
                    onClick={() => openSettings(tab.value === "plugins" ? "skills" : tab.value)}
                    className={`em-settings-sidebar-item ${isActive ? "is-active" : ""}`}
                  >
                    <span className="em-settings-sidebar-item-icon">{tab.icon}</span>
                    <span className="flex-1 text-left">{tab.label}</span>
                    {isActive && <span className="em-settings-sidebar-dot" />}
                  </button>
                );
              })}
              <div className="em-settings-sidebar-foot">
                <span className="em-settings-sidebar-foot-dot" />
                <span>{draftCount ? `${draftCount} 项运行设置未保存，草稿已保留` : "各项设置的保存方式见页面说明"}</span>
              </div>
            </aside>
            <div className="em-settings-mobile-head sm:hidden">
              <nav className="em-settings-mobile-tabs" role="tablist" data-coach-id="coach-settings-tabs">
                {TAB_META.map((tab) => {
                  const isActive = primaryTab === tab.value;
                  return <button key={tab.value} type="button" role="tab" aria-selected={isActive} data-coach-id={tab.coachId} onClick={() => openSettings(tab.value === "plugins" ? "skills" : tab.value)} className={`em-settings-mobile-tab ${isActive ? "is-active" : ""}`}>{tab.icon}{tab.label}</button>;
                })}
              </nav>
              {draftCount > 0 && <p className="px-3 pb-2 text-xs text-amber-700 dark:text-amber-400">{draftCount} 项运行设置未保存，切换页面会保留草稿</p>}
            </div>
            <AnimatePresence mode="wait">
              <motion.div
                key={primaryTab}
                initial={{ opacity: 0, x: 8 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -8 }}
                transition={{ duration: 0.15 }}
                className="em-settings-scroll overflow-y-auto min-h-0 h-full px-4 sm:px-7 flex flex-col pb-[max(1rem,env(safe-area-inset-bottom))] sm:pb-7"
              >
                <SettingsSearch />
                <Suspense fallback={<TabSpinner />}>
                  <TabsContent value="model" className="mt-0 grow shrink-0 flex flex-col" forceMount={settingsTab === "model" ? true : undefined} data-coach-id="coach-settings-content-model">
                    {settingsTab === "model" && <ModelTab />}
                  </TabsContent>
                  <TabsContent value="plugins" className="mt-0 grow shrink-0 flex flex-col" forceMount={primaryTab === "plugins" ? true : undefined} data-coach-id={`coach-settings-content-${pluginTab}`}>
                    {primaryTab === "plugins" && (
                      <PluginsTab
                        activeTab={pluginTab}
                        onTabChange={(tab) => openSettings(tab)}
                      />
                    )}
                  </TabsContent>
                  <TabsContent value="runtime" className="mt-0 grow shrink-0 flex flex-col" forceMount={settingsTab === "runtime" ? true : undefined} data-coach-id="coach-settings-content-runtime">
                    {settingsTab === "runtime" && <RuntimeTab />}
                  </TabsContent>
                  <TabsContent value="access" className="mt-0 grow shrink-0 flex flex-col">
                    {settingsTab === "access" && <AccessTab />}
                  </TabsContent>
                  <TabsContent value="version" className="mt-0 grow shrink-0 flex flex-col" forceMount={settingsTab === "version" ? true : undefined} data-coach-id="coach-settings-content-version">
                    {settingsTab === "version" && <VersionTab />}
                  </TabsContent>
                </Suspense>
              </motion.div>
            </AnimatePresence>
          </div>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
