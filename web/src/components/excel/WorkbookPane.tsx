"use client";

import { useCallback, useState } from "react";
import dynamic from "next/dynamic";
import { Check } from "lucide-react";
import { buildExcelFileUrl, downloadFile } from "@/lib/api";
import { fileBaseName } from "@/lib/revision-display";
import { fileRefFromSession, normalizeRelativePath } from "@/lib/workspace-file-ref";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useWorkbookConversationStore, type WorkbookViewState } from "@/stores/workbook-conversation-store";
import { useWorkbookWorkspaceStore } from "@/stores/workbook-workspace-store";
import { useWorkbookWorkspace } from "@/hooks/use-workbook-workspace";
import { useExcelCellEdit } from "@/hooks/use-excel-cell-edit";
import { useIsMobile } from "@/hooks/use-mobile";
import { FileHistoryWorkspace } from "@/components/history/FileHistoryWorkspace";
import { ExcelRibbonChrome } from "./ExcelRibbonChrome";
import { HistoryPaneOverlay } from "./HistoryPaneOverlay";
import { ExcelWriteConflictBar } from "./ExcelWriteConflictBar";
import { WorkbookInteractionBar, useWorkbookQuestionRequest } from "./WorkbookInteractionBar";
import styles from "./WorkbookWorkspace.module.css";
import { WorkbookLoadingState } from "./WorkbookLoadingState";

const UniverSheet = dynamic(() => import("./UniverSheet").then((module) => module.UniverSheet), {
  ssr: false, loading: () => <WorkbookLoadingState />,
});

export function WorkbookPane({ id, labelledBy, path, active, focused, onClose, onExpand, expandTitle }: {
  id?: string; labelledBy?: string;
  path: string; active: boolean; focused: boolean; onClose?: () => void; onExpand?: () => void; expandTitle: string;
}) {
  const { session, workspaceKey, key, workspace } = useWorkbookWorkspace();
  const file = fileRefFromSession(path, session);
  const primary = workspace.files[0]?.path === path;
  const sheet = workspace.files.find((entry) => entry.path === path)?.sheet;
  const isMobile = useIsMobile();
  const question = useWorkbookQuestionRequest();
  const generation = useExcelStore((state) => state.viewGeneration);
  const panelTab = useExcelStore((state) => state.panelTab);
  const globalHistoryView = useExcelStore((state) => state.historySubview);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyView, setHistoryView] = useState(globalHistoryView);
  // Existing navigation controls operate on the focused pane; other panes retain their own history view.
  const historyActive = focused ? panelTab === "history" : historyOpen;
  const selecting = useExcelStore((state) => state.selectionMode);
  const selectionMode = active && focused && selecting;
  const draft = useExcelStore((state) => state.draftRange);
  const ownDraft = draft?.path && normalizeRelativePath(draft.path) === path ? draft : null;
  const diffs = useExcelStore((state) => state.diffs);
  const operations = useExcelStore((state) => state.operations);
  const { handleCellEdit, conflict, writeError, reloadAfterConflict } = useExcelCellEdit(path);
  const [withStyles, setWithStyles] = useState(true);
  const focus = () => useExcelStore.getState().focusWorkbook(path);
  const reportView = useCallback((view: WorkbookViewState) => {
    if (!session || useSessionStore.getState().activeSessionId !== session.id) return;
    useWorkbookConversationStore.getState().observe(session.id, fileRefFromSession(path, session), view);
    if (view.status === "ready" && view.sheet) {
      const store = useWorkbookWorkspaceStore.getState();
      store.observeSheet(key, path, view.sheet);
      if (view.range && store.workspaces[key]?.focused === path) {
        store.navigate(key, path, view.sheet, view.range);
      }
    }
  }, [session, path, key]);
  const handleRange = useCallback((range: string, selectedSheet: string, _value?: string, contentVersion?: string) => {
    if (range && selectedSheet) useExcelStore.getState().setDraftRange({ path, range, sheet: selectedSheet, contentVersion });
  }, [path]);
  const filename = fileBaseName(path);
  const navigation = workspace.linkSelection && !focused && workspace.navigation?.source !== path ? workspace.navigation : undefined;

  return <section id={id} role="tabpanel" aria-labelledby={labelledBy} className={styles.pane} data-workbook-pane={path} data-focused={focused}
    aria-label={`${primary ? "主表" : "参考表"}：${filename}`} onPointerDownCapture={focus} onFocusCapture={focus}>
    <div className={styles.paneBody}>
      <UniverSheet fitContainer active={active && !historyActive} focused={focused} fileUrl={buildExcelFileUrl(path, session?.id, session?.workspaceId)}
        fileRef={file} sessionId={session?.id} viewGeneration={generation} initialSheet={sheet}
        selectionMode={selectionMode} readOnly={Boolean(question && selectionMode)} onRangeSelected={handleRange}
        onCellEdit={handleCellEdit} onViewState={reportView} withStyles={withStyles} linkedSelection={navigation}
        historyActive={historyActive} onNativeRibbonTab={() => { setHistoryOpen(false); useExcelStore.getState().setPanelTab("sheet"); }}
        ribbonSlot={<ExcelRibbonChrome historyActive={historyActive} selectionMode={selectionMode} withStyles={withStyles} isMobile={isMobile}
          onHistory={() => { focus(); setHistoryOpen(true); useExcelStore.getState().setPanelTab("history"); }}
          onToggleSelection={() => { focus(); const store = useExcelStore.getState(); if (store.selectionMode) store.exitSelectionMode(); else store.enterSelectionMode(); }}
          onCancelSelection={() => useExcelStore.getState().exitSelectionMode()} onToggleStyles={() => setWithStyles((value) => !value)}
          onRefresh={() => useExcelStore.getState().notifyWorkbookChanged(path, workspaceKey, undefined, "refresh")}
          onDownload={() => { void downloadFile(path, filename, session?.id, session?.workspaceId).catch(() => {}); }}
          onExpand={onExpand} expandTitle={expandTitle} onClose={onClose} />}
      />
      {historyActive && <HistoryPaneOverlay><FileHistoryWorkspace filePath={path} workspaceId={session?.workspaceId} active={active}
        view={historyView} onViewChange={setHistoryView} operationCount={operations.length}
        cellDiffs={diffs.filter((diff) => normalizeRelativePath(diff.filePath) === path).slice(-20)} /></HistoryPaneOverlay>}
    </div>
    {(conflict || writeError) && <ExcelWriteConflictBar filePath={path} onReload={reloadAfterConflict} error={conflict ? null : writeError} />}
    <WorkbookInteractionBar filePath={path} />
    {selectionMode && ownDraft && !question && <div className={styles.selectionBar}>
      <span className={styles.selectionText}>{filename} · {ownDraft.sheet}!{ownDraft.range}</span>
      <button type="button" className={styles.selectionConfirm} onClick={() => {
        useExcelStore.getState().confirmSelection({ filePath: path, sheet: ownDraft.sheet, range: ownDraft.range, contentVersion: ownDraft.contentVersion });
        if (isMobile) useExcelStore.getState().closeFullView();
      }}><Check className="h-3 w-3" />引用到对话</button>
      <button type="button" className={styles.selectionCancel} onClick={() => useExcelStore.getState().exitSelectionMode()}>取消</button>
    </div>}
  </section>;
}
