"use client";

import { useEffect } from "react";
import { useExcelStore } from "@/stores/excel-store";
import { formatFileMention, trackRecentExcelFile } from "./chat-input-insert";

interface ChatSelectionChipProps {
  insertMentionTokens: (fullTokens: string[], afterInsert?: () => void) => void;
}

/**
 * 消费 excel-store.pendingSelection，把确认的表格选区注入为 @file:…[Sheet!Range] 提及。
 * 选区本身没有独立芯片 UI：注入后走输入框高亮 token。
 */
export function ChatSelectionChip({ insertMentionTokens }: ChatSelectionChipProps) {
  const pendingSelection = useExcelStore((s) => s.pendingSelection);
  const clearPendingSelection = useExcelStore((s) => s.clearPendingSelection);

  useEffect(() => {
    if (!pendingSelection) return;
    const { filePath, sheet, range } = pendingSelection;
    const filename = filePath.split("/").pop() || filePath;
    const version = useExcelStore.getState().getContentVersion(filePath);
    insertMentionTokens([formatFileMention({ path: filePath, sheet, range, version })]);
    trackRecentExcelFile(filePath, filename);
    clearPendingSelection();
  }, [pendingSelection, clearPendingSelection, insertMentionTokens]);

  return null;
}
