/**
 * Side-effect registry for tour steps.
 * Each key corresponds to an `onEnter` / `onInteractionDone` / `onSceneEnter` / `onSceneExit` string
 * in tour-steps.ts. Centralises all UI mutations so step definitions stay pure data.
 */
import { useChatStore } from "@/stores/chat-store";
import { useUIStore } from "@/stores/ui-store";
import { useExcelStore } from "@/stores/excel-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import {
  ensureDemoSession,
  prefillDemoInput,
  clearDemoInput,
  injectMockStreaming,
  cleanupMockStreaming,
  cleanupExcelPreviewDemo,
  cleanupDemoSession,
} from "./demo-session";
import { activateSettingsDemo, deactivateSettingsDemo } from "./demo-settings";

type EffectFn = () => void;

const EFFECTS: Record<string, EffectFn> = {
  // ── Demo session ──
  ensureDemoSession: () => {
    ensureDemoSession();
  },
  ensureDemoSession_openSidebar: () => {
    ensureDemoSession();
    useUIStore.getState().setSidebarOpen(true);
    useUIStore.getState().setSidebarTab("chats");
  },
  ensureDemoSession_closeSettings_switchChats: () => {
    ensureDemoSession();
    useUIStore.getState().closeSettings();
    useUIStore.getState().setSidebarTab("chats");
  },
  cleanupDemoSession: () => {
    cleanupDemoSession();
  },

  // ── Sidebar ──
  openSidebar_chats: () => {
    useUIStore.getState().setSidebarOpen(true);
    useUIStore.getState().setSidebarTab("chats");
  },
  switchSidebarToChats: () => {
    useUIStore.getState().setSidebarTab("chats");
  },
  openSidebar_files_injectDemo: () => {
    useUIStore.getState().setSidebarTab("files");
    useUIStore.getState().setSidebarOpen(true);
    useExcelStore.getState().injectDemoFile();
  },
  clearInput_openSidebar_files_injectDemo: () => {
    clearDemoInput();
    useUIStore.getState().setSidebarTab("files");
    useUIStore.getState().setSidebarOpen(true);
    useExcelStore.getState().injectDemoFile();
  },

  // ── Input ──
  prefillDemoInput: () => {
    prefillDemoInput();
  },
  clearInput_delayed: () => {
    setTimeout(() => clearDemoInput(), 200);
  },

  // ── Mock streaming ──
  injectMockStreaming: () => {
    injectMockStreaming();
  },
  ensureMockStreaming: () => {
    const { isStreaming } = useChatStore.getState();
    if (!isStreaming) {
      injectMockStreaming();
    }
  },
  cleanupMockStreaming: () => {
    cleanupMockStreaming();
  },

  // ── Excel preview ──
  openExcelPanel: () => {
    useExcelStore.getState().openPanel("__demo__/示例销售数据.xlsx");
  },
  ensureExcelPanelOpen: () => {
    const store = useExcelStore.getState();
    store.injectDemoFile();
    if (!store.panelOpen) {
      store.openPanel("__demo__/示例销售数据.xlsx");
    }
  },
  cleanupExcelPreview: () => {
    cleanupExcelPreviewDemo();
  },
  cleanupExcelPreview_closeSettings: () => {
    cleanupExcelPreviewDemo();
    useUIStore.getState().closeSettings();
  },
  cleanupAdvancedScene: () => {
    clearDemoInput();
    cleanupExcelPreviewDemo();
    // NOTE: do NOT close settings here — the settings tour reuses the
    // settings page that was opened during the last advanced-tour step.
    cleanupDemoSession();
  },
  cleanupExcelPreview_openSidebar: () => {
    cleanupExcelPreviewDemo();
    useUIStore.getState().setSidebarOpen(true);
    useUIStore.getState().setSidebarTab("chats");
  },

  // ── Settings ──
  closeSettings: () => {
    useUIStore.getState().closeSettings();
  },
  closeSettings_switchChats: () => {
    useUIStore.getState().closeSettings();
    useUIStore.getState().setSidebarTab("chats");
  },
  openSettings_model: () => {
    useUIStore.getState().openSettings("model");
  },
  openSettings_rules: () => {
    useUIStore.getState().openSettings("rules");
  },
  openSettings_skills: () => {
    useUIStore.getState().openSettings("skills");
  },
  openSettings_mcp: () => {
    useUIStore.getState().openSettings("mcp");
  },
  openSettings_memory: () => {
    useUIStore.getState().openSettings("memory");
  },
  openSettings_runtime: () => {
    useUIStore.getState().openSettings("runtime");
  },
  openSettings_version: () => {
    useUIStore.getState().openSettings("version");
  },

  // ── Settings demo data ──
  openSettings_rules_withDemo: () => {
    activateSettingsDemo();
    useUIStore.getState().openSettings("rules");
  },
  openSettings_memory_withDemo: () => {
    activateSettingsDemo();
    useUIStore.getState().openSettings("memory");
  },

  // ── Guide lock (prevents settings dialog from closing during settings tour) ──
  lockSettings: () => {
    useOnboardingStore.getState().setGuideLocked(true);
    activateSettingsDemo();
    // Only open settings if not already open (advanced tour may have left it open)
    if (!useUIStore.getState().settingsOpen) {
      useUIStore.getState().openSettings("model");
    }
  },
  unlockAndCloseSettings: () => {
    useOnboardingStore.getState().setGuideLocked(false);
    deactivateSettingsDemo();
    useUIStore.getState().closeSettings();
  },
};

/** Execute a named side-effect. Silently ignores unknown keys. */
export function runEffect(key: string | undefined): void {
  if (!key) return;
  const fn = EFFECTS[key];
  if (fn) {
    fn();
  } else {
    console.warn(`[tour-effects] Unknown effect key: "${key}"`);
  }
}
