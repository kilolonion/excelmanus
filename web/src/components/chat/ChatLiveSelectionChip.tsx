"use client";

import { MousePointerSquareDashed } from "lucide-react";
import { useExcelStore } from "@/stores/excel-store";
import { useWorkbookConversation } from "@/components/excel/WorkbookConversation";
import { normalizeRelativePath } from "@/lib/workspace-file-ref";
import { buildSelectionAgentPrompt } from "@/lib/excel-ribbon-actions";

/**
 * 显示表格实时上报的当前选区，提供一键「引用 / 分析」入口。
 * 仅在普通模式（非选区引用模式）且选区属于当前对话关联的表格时显示。
 */
export function ChatLiveSelectionChip() {
  const liveSelection = useExcelStore((s) => s.liveSelection);
  const selectionMode = useExcelStore((s) => s.selectionMode);
  const fullViewPath = useExcelStore((s) => s.fullViewPath);
  const panelOpen = useExcelStore((s) => s.panelOpen);
  const activeFilePath = useExcelStore((s) => s.activeFilePath);
  const { target } = useWorkbookConversation();

  if (!liveSelection || selectionMode) return null;
  const selected = normalizeRelativePath(liveSelection.path);
  const candidates = [fullViewPath, panelOpen ? activeFilePath : null, target?.file.relative];
  if (!candidates.some((p) => p && normalizeRelativePath(p) === selected)) return null;

  const { path, sheet, range, contentVersion } = liveSelection;
  return (
    <div
      className="flex items-center gap-1.5 px-3 py-1 text-xs text-muted-foreground"
      data-em-live-selection={`${sheet}!${range}`}
    >
      <MousePointerSquareDashed className="h-3.5 w-3.5 shrink-0" />
      <span className="truncate min-w-0">当前选区 {sheet} · {range}</span>
      <span className="ml-auto flex shrink-0 items-center gap-1">
        <button
          type="button"
          data-em-live-selection-action="reference"
          className="inline-flex items-center rounded-full border border-[var(--em-primary-alpha-15)] bg-[var(--em-primary-alpha-06)] px-2 py-0.5 text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-12)]"
          onClick={() => useExcelStore.getState().confirmSelection({ filePath: path, sheet, range, contentVersion })}
        >
          引用
        </button>
        <button
          type="button"
          data-em-live-selection-action="analyze"
          className="inline-flex items-center rounded-full border border-[var(--em-primary-alpha-15)] bg-[var(--em-primary-alpha-06)] px-2 py-0.5 text-[var(--em-primary)] hover:bg-[var(--em-primary-alpha-12)]"
          onClick={() => useExcelStore.getState().setPendingTemplateMessage(
            buildSelectionAgentPrompt("analyze-selection", { path, sheet, range, version: contentVersion }),
          )}
        >
          分析
        </button>
      </span>
    </div>
  );
}
