"use client";

import { useEffect, useRef, useState } from "react";
import { FileSpreadsheet, FolderOpen, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { workspaceKeyFromSession } from "@/lib/workspace-file-ref";
import { displayFilePath } from "@/lib/file-identity";
import { openWorkbookForConversation } from "@/lib/open-workbook";

export function WorkbookStart() {
  const session = useSessionStore((s) => s.sessions.find((item) => item.id === s.activeSessionId));
  const recent = useExcelStore((s) => s.recentFiles);
  const version = useExcelStore((s) => s.workspaceFilesVersion);
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    if (session) void useExcelStore.getState().refreshWorkspaceFiles(session.id, { cached: true });
  }, [session, version]);
  useEffect(() => () => { request.current?.abort(); }, [session?.id]);
  const files = recent.filter((f) => f.workspaceKey === workspaceKeyFromSession(session)).slice(0, 3);
  return <div className="flex flex-col gap-2 mt-2">
    <div><Button onClick={() => useWorkbookConversationStore.getState().openPicker()} className="gap-2" data-coach-id="coach-open-workbook"><FolderOpen className="h-4 w-4" />打开表格</Button></div>
    {files.length > 0 && <div className="flex items-center gap-1.5 flex-wrap text-xs">
      <span className="text-muted-foreground">已有表格</span>
      {files.map((file) => <button key={file.path} type="button" className="flex items-center gap-1 max-w-48 rounded-md px-2 py-1 hover:bg-muted text-[var(--em-primary)]"
        title={displayFilePath(file.path)} onClick={async () => {
          if (!session || opening === file.path) return;
          request.current?.abort(); const controller = new AbortController(); request.current = controller;
          setOpening(file.path); setError(null);
          try { await openWorkbookForConversation(file.path, session, { signal: controller.signal }); }
          catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "打开失败"); }
          finally { if (request.current === controller) setOpening(null); }
        }}>{opening === file.path ? <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" /> : <FileSpreadsheet className="h-3.5 w-3.5 shrink-0" />}<span className="truncate">{file.filename}</span></button>)}
    </div>}
    {error && <p className="text-xs text-destructive" role="alert">{error}</p>}
  </div>;
}
