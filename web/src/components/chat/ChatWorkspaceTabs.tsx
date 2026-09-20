"use client";

import { useShallow } from "zustand/react/shallow";
import { useExcelStore } from "@/stores/excel-store";
import { useWordStore } from "@/stores/word-store";
import {
  resolveChatWorkspaceTab,
  resolveSheetFullViewTarget,
  type ChatWorkspaceTab,
} from "@/lib/chat-workspace-tabs";
import { prefetchExcelView } from "@/lib/excel-view-prefetch";
import { openWorkspaceFile } from "@/lib/open-workspace-file";

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
      closeFullView: s.closeFullView,
      closeCompare: s.closeCompare,
    })),
  );
  const wordFullViewPath = useWordStore((s) => s.fullViewPath);

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

  const select = (key: ChatWorkspaceTab) => {
    if (key === "chat") {
      if (excel.compareMode) excel.closeCompare();
      if (excel.fullViewPath) excel.closeFullView();
      if (wordFullViewPath) useWordStore.getState().closeFullView();
      return;
    }
    if (!sheetTarget) return;
    if (excel.compareMode) excel.closeCompare();
    openWorkspaceFile(sheetTarget.path, { intent: "full", sheet: sheetTarget.sheet });
  };

  return (
    <div className="flex items-center">
      <div
        role="tablist"
        aria-label="工作区视图"
        data-active-tab={active}
        className="flex items-center min-w-0"
      >
        <span className="em-workspace-tab-glider" aria-hidden="true" />
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
              className={`relative inline-flex items-center justify-center leading-none ${
                selected
                  ? "text-white"
                  : "text-muted-foreground/75 hover:text-foreground"
              } disabled:opacity-40 disabled:cursor-not-allowed`}
            >
              <span className="relative z-10">{label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
