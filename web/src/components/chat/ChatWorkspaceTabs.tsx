"use client";

import { useShallow } from "zustand/react/shallow";
import { useExcelStore } from "@/stores/excel-store";
import { useUIStore } from "@/stores/ui-store";
import { useWordStore } from "@/stores/word-store";
import {
  resolveChatWorkspaceTab,
  resolveSheetFullViewTarget,
  type ChatWorkspaceTab,
} from "@/lib/chat-workspace-tabs";
import { prefetchExcelView } from "@/lib/excel-view-prefetch";

const TABS: { key: ChatWorkspaceTab; label: string }[] = [
  { key: "chat", label: "对话" },
  { key: "sheet", label: "表格" },
];

function closeWordSurfaces() {
  const word = useWordStore.getState();
  word.closePanel();
  word.closeFullView();
}

export function ChatWorkspaceTabs() {
  const excel = useExcelStore(
    useShallow((s) => ({
      fullViewPath: s.fullViewPath,
      fullViewSheet: s.fullViewSheet,
      compareMode: s.compareMode,
      activeFilePath: s.activeFilePath,
      activeSheet: s.activeSheet,
      recentFiles: s.recentFiles,
      activeWorkspaceKey: s.activeWorkspaceKey,
      openFullView: s.openFullView,
      closeFullView: s.closeFullView,
      closeCompare: s.closeCompare,
    })),
  );
  const wordFullViewPath = useWordStore((s) => s.fullViewPath);
  const sidebarOpen = useUIStore((s) => s.sidebarOpen);

  const active = resolveChatWorkspaceTab({
    fullViewPath: excel.fullViewPath,
    compareMode: excel.compareMode,
  });
  const sheetTarget = resolveSheetFullViewTarget({
    activeFilePath: excel.activeFilePath,
    activeSheet: excel.activeSheet,
    recentFiles: excel.recentFiles,
    workspaceKey: excel.activeWorkspaceKey,
    fullViewPath: excel.fullViewPath,
    fullViewSheet: excel.fullViewSheet,
  });

  const select = (key: ChatWorkspaceTab) => {
    if (key === "chat") {
      if (excel.compareMode) excel.closeCompare();
      if (excel.fullViewPath) excel.closeFullView();
      if (wordFullViewPath) useWordStore.getState().closeFullView();
      return;
    }
    if (!sheetTarget) return;
    closeWordSurfaces();
    if (excel.compareMode) excel.closeCompare();
    excel.openFullView(sheetTarget.path, sheetTarget.sheet);
  };

  return (
    <div className="flex items-center h-7 -mt-1 pb-0">
      {!sidebarOpen && <div className="w-8 mr-1 shrink-0" aria-hidden />}
      <div
        role="tablist"
        aria-label="工作区视图"
        className="flex items-center gap-0.5 -ml-1.5 min-w-0"
      >
        {TABS.map(({ key, label }) => {
          const selected = active === key;
          const disabled = key === "sheet" && !sheetTarget;
          return (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={selected}
              disabled={disabled}
              title={disabled ? "先打开一个工作簿" : undefined}
              onMouseEnter={() => {
                if (key === "sheet" && sheetTarget) prefetchExcelView(sheetTarget.path);
              }}
              onFocus={() => {
                if (key === "sheet" && sheetTarget) prefetchExcelView(sheetTarget.path);
              }}
              onClick={() => select(key)}
              className={`relative inline-flex items-center h-5 px-1.5 text-[11px] font-normal leading-none transition-colors ${
                selected
                  ? "text-foreground"
                  : "text-muted-foreground/70 hover:text-muted-foreground"
              } disabled:opacity-40 disabled:pointer-events-none`}
            >
              {label}
              {selected && (
                <span
                  className="absolute inset-x-1.5 -bottom-0.5 h-px rounded-full"
                  style={{ backgroundColor: "var(--em-primary)" }}
                  aria-hidden
                />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
