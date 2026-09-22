import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { UniverSheet } from "@/components/excel/UniverSheet";
import { RevisionTimelinePanel } from "@/components/chat/CheckpointTimeline";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import "@/app/globals.css";

useSessionStore.setState({ activeSessionId: "browser", sessions: [{ id: "browser", workspaceId: "browser-ws", title: "History test", messageCount: 0, inFlight: false }] });
useExcelStore.setState({ activeWorkspaceKey: "id:browser-ws" });

function Harness() {
  const [result, setResult] = useState("");
  const inspect = () => {
    // This diagnostic is exposed in the page so browser assertions can use DOM.
    const host = window as unknown as { workbookAPI: { getActiveWorkbook(): {
      getSheets(): { getSheetName(): string }[];
      getActiveSheet(): { getSheetName(): string; getRange(a1: string): { getValue(): unknown } };
    } }; workbookCounters: { created: number; disposed: number } };
    const book = host.workbookAPI.getActiveWorkbook();
    const sheet = book.getActiveSheet();
    setResult(JSON.stringify({ sheets: book.getSheets().map((s) => s.getSheetName()), active: sheet.getSheetName(), title: sheet.getRange("A1").getValue(), total: sheet.getRange("F12").getValue(), ...host.workbookCounters }));
  };
  return <>
    <div style={{ padding: 8, display: "flex", gap: 12, height: 48 }}>
      <button onClick={inspect}>检查实际表格</button><output>{result}</output>
    </div>
    <div style={{ height: "calc(100vh - 48px)", display: "grid", gridTemplateColumns: "1fr 360px" }}>
      <UniverSheet fileUrl="/api/v1/files/excel?path=history-demo.xlsx" fileRef={{ workspaceKey: "id:browser-ws", workspaceId: "browser-ws", relative: "history-demo.xlsx" }} sessionId="browser" active />
      <RevisionTimelinePanel filePath="history-demo.xlsx" />
    </div>
  </>;
}
createRoot(document.getElementById("root")!).render(<Harness />);
