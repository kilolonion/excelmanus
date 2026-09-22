import React, { useState, useCallback } from "react";
import { createRoot } from "react-dom/client";
import { UniverSheet } from "@/components/excel/UniverSheet";
import { ExcelRibbonChrome } from "@/components/excel/ExcelRibbonChrome";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { fetchWorkbookView } from "@/lib/api";
import { WorkbookInteractionBar, useWorkbookQuestionRequest } from "@/components/excel/WorkbookInteractionBar";
import { useWorkbookFocusStore } from "@/stores/workbook-focus-store";
import { useChatStore } from "@/stores/chat-store";
import { openWorkbookQuestion, showWorkbookPresentation } from "@/lib/workbook-interaction";
import "@/app/globals.css";

useSessionStore.setState({ activeSessionId: "browser", sessions: [{ id: "browser", workspaceId: "browser-ws", title: "Browser test", messageCount: 0, inFlight: false }] });
useExcelStore.setState({ activeWorkspaceKey: "id:browser-ws" });
Object.assign(window, { excelStore: useExcelStore, prefetchView: fetchWorkbookView, focusStore: useWorkbookFocusStore,
  chatStore: useChatStore, openWorkbookQuestion, showWorkbookPresentation });

function Harness() {
  const [tick, setTick] = useState(0);
  const [active, setActive] = useState(true);
  const [path, setPath] = useState("book.xlsx");
  const [history, setHistory] = useState(false);
  const question = useWorkbookQuestionRequest();
  const selectionMode = useExcelStore((s) => s.selectionMode);
  const onRangeSelected = useCallback((range: string, sheet: string, _value?: string, contentVersion?: string) => {
    useExcelStore.getState().setDraftRange({ path, sheet, range, contentVersion });
  }, [path]);
  return <>
    <div style={{ height: 36, display: "flex", gap: 16 }}>
      <button onClick={() => setTick((v) => v + 1)}>Parent render {tick}</button>
      <button onClick={() => setActive((v) => !v)}>Toggle visibility</button>
      <button onClick={() => setPath((v) => v === "book.xlsx" ? "other.xlsx" : "book.xlsx")}>Switch file</button>
    </div>
    <div style={{ height: "calc(100vh - 36px)", visibility: active ? "visible" : "hidden", display: "flex", flexDirection: "column" }}>
      <UniverSheet fileUrl={`/api/v1/files/excel?path=${path}`} fileRef={{ workspaceKey: "id:browser-ws", workspaceId: "browser-ws", relative: path }} sessionId="browser" active={active}
        historyActive={history} onNativeRibbonTab={() => setHistory(false)}
        selectionMode={selectionMode} onRangeSelected={onRangeSelected} readOnly={Boolean(question && selectionMode)}
        ribbonSlot={<ExcelRibbonChrome historyActive={history} selectionMode={false} withStyles isMobile={false}
          onHistory={() => setHistory(true)} onToggleSelection={() => {}} onCancelSelection={() => {}} onToggleStyles={() => {}}
          onRefresh={() => useExcelStore.getState().notifyWorkbookChanged(path, "id:browser-ws", undefined, "refresh")}
          onDownload={() => {}} onExpand={() => {}} onClose={() => setActive(false)} />}
      />
      <WorkbookInteractionBar filePath={path} />
    </div>
  </>;
}
createRoot(document.getElementById("root")!).render(<Harness />);
