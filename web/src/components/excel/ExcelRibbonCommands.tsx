"use client";

import { useExcelStore } from "@/stores/excel-store";
import { activeSession, fileRefFromSession } from "@/lib/workspace-file-ref";
import { useWorkbookWorkflowStore } from "@/stores/workbook-workflow-store";
import {
  buildRibbonAskPrompt,
  ribbonAskActions,
  type NativeRibbonTab,
  type RibbonAskContext,
} from "@/lib/excel-ribbon-actions";

export function ExcelRibbonCommands({
  tab,
  filePath,
  fallbackSheet,
  getSelection,
}: {
  tab: NativeRibbonTab;
  filePath: string;
  fallbackSheet?: string;
  getSelection?: () => Pick<RibbonAskContext, "sheet" | "range" | "version">;
}) {
  const actions = ribbonAskActions(tab);
  if (actions.length === 0 || !filePath) return null;

  const ask = (kind: (typeof actions)[number]["kind"]) => {
    const live = getSelection?.() ?? {};
    const { draftRange, activeSheet } = useExcelStore.getState();
    const sameDraft = draftRange && (!draftRange.path || draftRange.path === filePath);
    const version = live.version ?? (sameDraft ? draftRange.contentVersion : undefined);
    const ctx: RibbonAskContext = {
      path: filePath,
      sheet:
        live.sheet ||
        (sameDraft ? draftRange.sheet : undefined) ||
        fallbackSheet ||
        activeSheet ||
        undefined,
      range: live.range || (sameDraft ? draftRange.range : undefined),
      version,
    };
    const session = activeSession();
    if (session && ["filter-analyze", "chart", "pivot", "generate-formula", "dedupe", "sort"].includes(kind)) {
      useWorkbookWorkflowStore.getState().openHandoff({ file: fileRefFromSession(filePath, session), sessionId: session.id,
        operation: kind === "filter-analyze" ? "filter" : kind, sheet: ctx.sheet, range: ctx.range, version: ctx.version ?? undefined });
    } else useExcelStore.getState().setPendingTemplateMessage(buildRibbonAskPrompt(kind, ctx));
  };

  return (
    <>
      <span className="em-ribbon-ask-label text-[10px] uppercase tracking-wide text-muted-foreground/80 pr-1 shrink-0">
        问 AI
      </span>
      {actions.map((action) => (
        <button
          key={action.kind}
          type="button"
          data-em-ribbon={`ask-${action.kind}`}
          title={action.title}
          onClick={() => ask(action.kind)}
          className="em-ribbon-ask-button inline-flex items-center h-7 px-2 rounded-sm text-sm whitespace-nowrap text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
        >
          {action.label}
        </button>
      ))}
    </>
  );
}
