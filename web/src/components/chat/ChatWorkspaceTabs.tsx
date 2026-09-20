"use client";

import { useShallow } from "zustand/react/shallow";
import { FolderOpen } from "lucide-react";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { useExcelStore } from "@/stores/excel-store";
import {
  activateChatWorkspaceTab,
  resolveChatWorkspaceTab,
  resolveSheetFullViewTarget,
  type ChatWorkspaceTab,
} from "@/lib/chat-workspace-tabs";
import { prefetchExcelView } from "@/lib/excel-view-prefetch";

const TABS: { key: ChatWorkspaceTab; label: string }[] = [
  { key: "chat", label: "对话" },
  { key: "sheet", label: "表格" },
];

export function ChatWorkspaceTabs() {
  const excel = useExcelStore(
    useShallow((s) => ({
      fullViewPath: s.fullViewPath,
      fullViewSheet: s.fullViewSheet,
      compareMode: s.compareMode,
      activeFilePath: s.activeFilePath,
      activeSheet: s.activeSheet,
      recentFiles: s.recentFiles,
      workspaceFiles: s.workspaceFiles,
      activeWorkspaceKey: s.activeWorkspaceKey,
    })),
  );

  const active = resolveChatWorkspaceTab({
    fullViewPath: excel.fullViewPath,
    compareMode: excel.compareMode,
  });
  const sheetTarget = resolveSheetFullViewTarget({
    activeFilePath: excel.activeFilePath,
    activeSheet: excel.activeSheet,
    recentFiles: excel.recentFiles,
    workspaceFiles: excel.workspaceFiles,
    workspaceKey: excel.activeWorkspaceKey,
    fullViewPath: excel.fullViewPath,
    fullViewSheet: excel.fullViewSheet,
  });

  return (
    <div className="flex items-center gap-2">
        <div
          role="tablist"
          aria-label="工作区视图"
          data-active-tab={active}
          className="flex items-center min-w-0"
        >
          <span className="em-workspace-tab-glider" aria-hidden="true" />
          {TABS.map(({ key, label }) => {
            const selected = active === key;
            const tab = (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={selected}
                onMouseEnter={() => {
                  if (key === "sheet" && sheetTarget) prefetchExcelView(sheetTarget.path);
                }}
                onFocus={() => {
                  if (key === "sheet" && sheetTarget) prefetchExcelView(sheetTarget.path);
                }}
                onClick={() => {
                  activateChatWorkspaceTab(key);
                }}
                className={`relative inline-flex items-center justify-center leading-none ${
                  selected
                    ? "text-white"
                    : "text-muted-foreground/75 hover:text-foreground"
                }`}
              >
                <span className="relative z-10">{label}</span>
              </button>
            );
            return tab;
          })}
        </div>
      <button type="button" aria-label="打开表格" title="打开已有表格"
        className="em-workspace-open"
        onClick={() => useWorkbookConversationStore.getState().openPicker()}><FolderOpen className="h-4 w-4" /></button>
    </div>
  );
}
