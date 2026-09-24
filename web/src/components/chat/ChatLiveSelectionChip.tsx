"use client";

import { MousePointerSquareDashed, X } from "lucide-react";
import { useExcelStore } from "@/stores/excel-store";
import { useWorkbookConversation } from "@/components/excel/WorkbookConversation";
import { normalizeRelativePath } from "@/lib/workspace-file-ref";

/**
 * 显示表格实时上报的当前选区，提供一键「引用」入口。
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
      className="em-composer-tab em-composer-tab--selection text-xs"
      data-em-live-selection={`${sheet}!${range}`}
    >
      <span className="em-composer-tab-icon" aria-hidden="true"><MousePointerSquareDashed className="h-3.5 w-3.5" /></span>
      <span className="em-composer-tab-label truncate min-w-0">当前选区 <strong>{sheet}</strong><span aria-hidden="true"> · </span>{range}</span>
      <span className="flex shrink-0 items-center gap-1">
        <button
          type="button"
          data-em-live-selection-action="reference"
          className="em-composer-tab-action inline-flex items-center"
          onClick={() => useExcelStore.getState().confirmSelection({ filePath: path, sheet, range, contentVersion })}
        >
          引用
        </button>
      </span>
      <button
        type="button"
        data-em-live-selection-action="cancel"
        className="em-composer-tab-dismiss em-composer-tab-cancel shrink-0"
        aria-label="取消选择区域"
        title="取消选择区域"
        onClick={() => useExcelStore.getState().setLiveSelection(null)}
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
