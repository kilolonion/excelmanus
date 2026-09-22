import React from "react";
import { createRoot } from "react-dom/client";
import { WorkspaceViewHost } from "@/components/workspace/WorkspaceViewHost";
import { WorkbookContextChip } from "@/components/excel/WorkbookConversation";
import { WorkbookWorkflowDialogs } from "@/components/excel/WorkbookWorkflowDialogs";
import { OpenWorkbookDialog } from "@/components/excel/OpenWorkbookDialog";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useWorkbookWorkspaceStore } from "@/stores/workbook-workspace-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { prepareWorkbookRequest } from "@/lib/workbook-conversation";
import "@/app/globals.css";

useSessionStore.setState({ activeSessionId: "multi-browser", sessions: [{ id: "multi-browser", workspaceId: "multi-ws", title: "多表验证", messageCount: 0, inFlight: false }] });
useExcelStore.setState({ activeWorkspaceKey: "id:multi-ws" });
useWorkbookWorkspaceStore.setState({ workspaces: {} });
useWorkbookConversationStore.setState({ targets: {}, views: {} });
for (const path of ["销售.xlsx", "预算.xlsx", "库存.xlsx"]) useExcelStore.getState().openFullView(path, "明细");
Object.assign(window, { excelStore: useExcelStore, workbookWorkspaces: useWorkbookWorkspaceStore,
  workbookConversations: useWorkbookConversationStore, prepareWorkbookRequest });
function Harness() {
  return <>
    <WorkspaceViewHost composer={<WorkbookContextChip />}>
      <p>多表对话验证</p>
      <button onClick={() => useExcelStore.getState().openFullView("销售.xlsx")}>返回工作区</button>
    </WorkspaceViewHost>
    <OpenWorkbookDialog /><WorkbookWorkflowDialogs />
  </>;
}
createRoot(document.getElementById("root")!).render(<Harness />);
