"use client";

import { useSyncExternalStore } from "react";
import { ArrowLeftRight, FileSpreadsheet, Loader2, MessageSquare, X } from "lucide-react";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { useWorkbookConversationStore, workbookViewKey } from "@/stores/workbook-conversation-store";
import { workspaceKeyFromSession } from "@/lib/workspace-file-ref";
import { hasPendingWorkbookEdits, isWorkbookEditPaused, subscribeWorkbookEdits } from "@/lib/excel-cell-edit";
import { fileBaseName } from "@/lib/revision-display";

export function useWorkbookConversation() {
  const sessionId = useSessionStore((s) => s.activeSessionId);
  const session = useSessionStore((s) => s.sessions.find((item) => item.id === s.activeSessionId));
  const target = useWorkbookConversationStore((s) => sessionId ? s.targets[sessionId] : undefined);
  const valid = target?.file.workspaceKey === workspaceKeyFromSession(session) ? target : undefined;
  const view = useWorkbookConversationStore((s) => sessionId && valid ? s.views[workbookViewKey(sessionId, valid.file)] : undefined);
  return { sessionId, target: valid, view };
}

export function WorkbookContextChip() {
  const { sessionId, target, view } = useWorkbookConversation();
  const editState = useSyncExternalStore(subscribeWorkbookEdits,
    () => !target ? "" : isWorkbookEditPaused(target.file) ? "保存需要处理" : hasPendingWorkbookEdits(target.file) ? "正在保存…" : "",
    () => "");
  if (!target || !sessionId) return null;
  const label = `${fileBaseName(target.file.relative)}${target.sheet ? ` · ${target.sheet}` : ""}`;
  return <div className="flex items-center gap-1.5 px-3 pt-2 pb-1 text-xs text-[var(--em-primary)]" data-workbook-context={target.file.relative}>
    <FileSpreadsheet className="h-3.5 w-3.5 shrink-0" />
    <button type="button" className="truncate text-left min-w-0" title={`当前讨论：${target.file.relative}`} onClick={() => {
      useExcelStore.getState().openFullView(target.file.relative, target.sheet, target.layout);
    }}>{label}</button>
    <button type="button" aria-label="更换主对话文件" title="更换主对话文件"
      className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[var(--em-primary-alpha-15)] bg-[var(--em-primary-alpha-06)] px-2 py-1 hover:bg-[var(--em-primary-alpha-12)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--em-primary)]"
      onClick={() => useWorkbookConversationStore.getState().openSwitchPicker(sessionId)}>
      <ArrowLeftRight className="h-3 w-3" />更换
    </button>
    <span className="text-muted-foreground shrink-0 ml-auto">{editState || (view?.status === "ready" ? "已关联" : view?.status === "error" ? "打开失败" : "加载中…")}</span>
    <button type="button" aria-label="移除当前表格关联" className="p-1 rounded hover:bg-muted shrink-0" onClick={() => useWorkbookConversationStore.getState().detach(sessionId)}><X className="h-3 w-3" /></button>
  </div>;
}

export function WorkbookConversationWelcome({ onSuggestion }: { onSuggestion: (text: string) => void }) {
  const { target, view } = useWorkbookConversation();
  return <div className="flex-1 min-h-0 overflow-y-auto p-5">
    <div className="flex items-center gap-2 text-xs text-muted-foreground" role="status">
      {view?.status === "loading" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileSpreadsheet className="h-4 w-4" />}
      {view?.status === "ready" ? "表格已打开，可以直接提问" : view?.status === "error" ? view.error : "正在加载表格…"}
    </div>
    <h2 className="mt-7 mb-2 text-lg font-medium">想从哪里开始？</h2>
    <p className="text-sm text-muted-foreground mb-5">直接问这张表，或选中区域后引用到对话。</p>
    <div className="flex flex-col gap-2 items-start">
      {["这张表主要记录什么？", "检查这张表是否有重复或缺失的数据", "帮我汇总这份表格的关键数据"].map((text) =>
        <button type="button" key={text} onClick={() => onSuggestion(text)} className="rounded-lg border px-3 py-2 text-sm text-left hover:bg-muted">{text}</button>)}
    </div>
    {target && <p className="mt-6 text-xs text-muted-foreground break-all"><MessageSquare className="inline h-3 w-3 mr-1" />当前讨论：{fileBaseName(target.file.relative)}</p>}
  </div>;
}
