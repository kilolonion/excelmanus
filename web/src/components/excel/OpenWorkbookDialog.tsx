"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { defaultRangeExtractor, useVirtualizer } from "@tanstack/react-virtual";
import { FileSpreadsheet, FolderOpen, Layers, Loader2, RefreshCw, Search, Upload } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { type ExcelFileListItem } from "@/lib/api";
import { isSpreadsheetFile } from "@/lib/file-kind";
import { displayFilePath, displayFileName } from "@/lib/file-identity";
import { ensureWorkbookSession, importWorkspaceFile, openWorkbookForConversation } from "@/lib/open-workbook";
import { isScopedWorkspaceKey, normalizeRelativePath, recentFilesForWorkspace, workspaceKeyFromSession } from "@/lib/workspace-file-ref";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { useExcelStore } from "@/stores/excel-store";
import { useSessionStore } from "@/stores/session-store";
import { useWorkbookWorkspace } from "@/hooks/use-workbook-workspace";
import type { Session } from "@/lib/types";

type Source = "uploaded" | "recent" | "workspace";
export function isUploadedWorkbook(path: string) {
  return /(^|\/)uploads\//.test(path.replace(/\\/g, "/"));
}

function isMissingWorkspaceFile(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? "");
  return /(?:\b404\b|not found|文件未找到|文件不存在|不存在)/i.test(message);
}

export function OpenWorkbookDialog() {
  const open = useWorkbookConversationStore((s) => s.pickerOpen);
  const layout = useWorkbookConversationStore((s) => s.pickerLayout);
  const switching = useWorkbookConversationStore((s) => s.pickerMode === "switch");
  const showSheet = useWorkbookConversationStore((s) => s.pickerShowSheet);
  const close = useWorkbookConversationStore((s) => s.closePicker);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const activeSession = useSessionStore((s) =>
    s.sessions?.find((item) => item.id === s.activeSessionId) ?? null,
  );
  const currentFile = useWorkbookConversationStore((s) => activeSessionId ? s.targets[activeSessionId]?.file : undefined);
  const { workspace } = useWorkbookWorkspace();
  const recent = useExcelStore((s) => s.recentFiles);
  const [scope, setScope] = useState<Session | null>(null);
  const [files, setFiles] = useState<ExcelFileListItem[]>([]);
  const [source, setSource] = useState<Source>("uploaded");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [opening, setOpening] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const requestRef = useRef<AbortController | null>(null);
  const pendingPath = useRef<string | null>(null);
  const uploadedRef = useRef<{ session: Session; path: string } | null>(null);

  // A hydrated session already contains the workspace scope needed for an upload.
  // Do not make the local file picker wait for chat history or the workspace scan.
  const locallyAvailableSession = activeSession
    && isScopedWorkspaceKey(workspaceKeyFromSession(activeSession))
    ? activeSession
    : null;

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setScope(locallyAvailableSession);
    const cached = useExcelStore.getState();
    const cachedFiles = locallyAvailableSession
      && cached.workspaceFilesSessionId === locallyAvailableSession.id
      && cached.workspaceFilesWorkspaceId === (locallyAvailableSession.workspaceId ?? null)
      ? cached.workspaceFiles
      : [];
    setFiles(cachedFiles.filter((f) => !f.is_dir && isSpreadsheetFile(f.filename || f.path))
      .map((f) => ({ ...f, modified_at: 0 })));
    setTruncated(locallyAvailableSession ? cached.workspaceFilesTruncated : false);
    setOpening(null);
    uploadedRef.current = null;
    void (async () => {
      try {
        const session = await ensureWorkbookSession();
        if (cancelled || useSessionStore.getState().activeSessionId !== session.id) return;
        setScope(session);
        // 与侧栏共用同一份扫描：30s 缓存窗口内不重复请求，inflight 合并。
        // 这是后台补全；已有 recentFiles/cached files 已经可以用于打开和上传。
        // The first open may reuse the sidebar snapshot. An explicit reload
        // bypasses the TTL so the picker can recover immediately after upload,
        // rename, or an external file change.
        await useExcelStore.getState().refreshWorkspaceFiles(session.id, { cached: reload === 0 });
        if (cancelled || useSessionStore.getState().activeSessionId !== session.id) return;
        const store = useExcelStore.getState();
        if (store.workspaceFilesError) throw new Error(store.workspaceFilesError);
        const files = store.workspaceFilesSessionId === session.id ? store.workspaceFiles : [];
        setFiles(files.filter((f) => !f.is_dir && isSpreadsheetFile(f.filename || f.path))
          .map((f) => ({ ...f, modified_at: 0 })));
        setTruncated(store.workspaceFilesTruncated);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "文件列表加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; requestRef.current?.abort(); pendingPath.current = null; };
  }, [open, activeSessionId, locallyAvailableSession, reload]);

  const candidates = useMemo(() => {
    const workspaceKey = workspaceKeyFromSession(scope);
    const scopedRecent = recentFilesForWorkspace(recent, workspaceKey);
    const map = new Map(files.map((f) => [f.path, f]));
    for (const f of scopedRecent) if (!map.has(f.path)) map.set(f.path, { ...f, modified_at: 0 });
    const rows = source === "recent" ? scopedRecent : Array.from(map.values());
    return rows.filter((f) => (source !== "uploaded" || isUploadedWorkbook(f.path))
      && `${f.filename} ${displayFilePath(f.path)}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  }, [files, recent, scope, source, query]);

  const virtualizer = useVirtualizer({
    count: open ? candidates.length : 0,
    getScrollElement: () => listRef.current,
    getItemKey: (index) => candidates[index]?.path ?? index,
    estimateSize: () => 64,
    overscan: 6,
    rangeExtractor: defaultRangeExtractor,
  });
  const virtualRows = virtualizer.getVirtualItems();
  // The dialog content is portaled, so the scroll element can be unavailable
  // for one render. Keep the first few rows visible until the observer attaches.
  const rows = virtualRows.length > 0
    ? virtualRows
    : candidates.slice(0, 8).map((file, index) => ({ key: file.path, index, start: index * 64 }));
  const listHeight = Math.max(virtualizer.getTotalSize(), candidates.length * 64);

  const openPath = async (path: string, session = scope) => {
    if (!session || pendingPath.current === path) return;
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    pendingPath.current = path;
    setOpening(`正在打开 ${displayFileName(path)}…`);
    setError(null);
    try {
      await openWorkbookForConversation(path, session, { signal: controller.signal, layout, showSheet, makePrimary: switching });
      if (!controller.signal.aborted) close();
    } catch (err) {
      if (!controller.signal.aborted) {
        if (isMissingWorkspaceFile(err)) {
          // A recent entry can outlive an external delete. Remove only this
          // workspace bucket; another workspace may legitimately have the same path.
          useExcelStore.getState().evictRecentFile(path, workspaceKeyFromSession(session));
          setReload((value) => value + 1);
        }
        setError(err instanceof Error ? err.message : "表格打开失败");
      }
    } finally {
      if (requestRef.current === controller) { setOpening(null); pendingPath.current = null; }
    }
  };

  const uploadAndOpen = async (file: File) => {
    if (!scope || opening) return;
    if (!isSpreadsheetFile(file.name)) { setError("请选择 Excel 或 CSV 表格"); return; }
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setError(null);
    setOpening(`正在上传 ${file.name}…`);
    try {
      const result = await importWorkspaceFile(file, scope);
      if (controller.signal.aborted) return;
      uploadedRef.current = { session: scope, path: result.path };
      setFiles((previous) => [{ ...result, modified_at: Date.now() / 1000 }, ...previous.filter((f) => f.path !== result.path)]);
      setSource("uploaded");
      await openPath(result.path, scope);
    } catch (err) {
      if (!controller.signal.aborted) { setError(err instanceof Error ? err.message : "上传失败"); setOpening(null); }
    }
  };

  return <Dialog open={open} onOpenChange={(value) => { if (!value) close(); }}>
    <DialogContent className="sm:max-w-xl" onDragOver={(e) => {
      if (e.dataTransfer.types.includes("Files")) e.preventDefault();
    }} onDrop={(e) => {
      e.preventDefault();
      const file = e.dataTransfer.files[0];
      if (file) void uploadAndOpen(file);
    }}>
      <DialogHeader>
        <DialogTitle>{switching ? "更换主对话文件" : "打开表格"}</DialogTitle>
        <DialogDescription>{switching ? "选择后，后续提问将默认关联这份表格。" : workspace.files.length > 0 ? "选择后会加入当前多表工作区，可继续并排查看。" : "选择已有文件，直接查看、提问或编辑。"}</DialogDescription>
      </DialogHeader>
      {!switching && workspace.files.length > 0 && (
        <div className="flex items-center gap-2 rounded-lg border border-[var(--em-primary-alpha-20)] bg-[var(--em-primary-alpha-06)] px-3 py-2 text-xs text-muted-foreground">
          <Layers className="h-3.5 w-3.5 shrink-0 text-[var(--em-primary)]" />
          <span>当前已打开 <strong className="font-semibold text-foreground">{workspace.files.length} 张表格</strong>，继续选择会加入工作区</span>
        </div>
      )}
      <div className="flex gap-1 flex-wrap" aria-label="表格来源">
        {([['uploaded', '已上传的表格'], ['recent', '最近打开'], ['workspace', '工作区文件']] as const).map(([key, label]) =>
          <Button key={key} size="sm" variant={source === key ? "secondary" : "ghost"} aria-pressed={source === key} onClick={() => setSource(key)}>{label}</Button>)}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="ml-auto gap-1"
          aria-label="刷新文件列表"
          disabled={loading || Boolean(opening)}
          onClick={() => setReload((value) => value + 1)}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          刷新
        </Button>
      </div>
      <label className="flex items-center gap-2 rounded-lg border px-3 py-2">
        <Search className="h-4 w-4 text-muted-foreground shrink-0" />
        <input aria-label="搜索表格文件名" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索文件名" className="bg-transparent outline-none w-full min-w-0 text-sm" />
      </label>
      <div ref={listRef} className="min-h-32 max-h-72 overflow-y-auto" aria-busy={loading}>
        {loading && source === "workspace" && candidates.length === 0
          ? <p role="status" className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取文件列表…</p>
          : candidates.length ? <div className="relative" style={{ height: listHeight }}>
             {rows.map((row) => {
               const file = candidates[row.index];
               if (!file) return null;
               const alreadyOpen = workspace.files.some((entry) => normalizeRelativePath(entry.path) === normalizeRelativePath(file.path));
               return <div key={row.key} className="absolute left-0 top-0 w-full" style={{ transform: `translateY(${row.start}px)` }}>
                 <button type="button" disabled={!scope || Boolean(opening?.startsWith("正在上传"))}
                   onClick={() => void openPath(file.path)} className="flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left hover:bg-muted disabled:opacity-50">
                   <FileSpreadsheet className="h-5 w-5 shrink-0 text-[var(--em-primary)]" />
                   <span className="flex-1 min-w-0"><span className="block truncate text-sm">{file.filename}</span><span className="block truncate text-xs text-muted-foreground" title={displayFilePath(file.path)}>{displayFilePath(file.path)}</span></span>
                   {alreadyOpen
                     ? <span className="shrink-0 rounded-full bg-[var(--em-primary-alpha-08)] px-2 py-0.5 text-xs text-[var(--em-primary)]">已打开</span>
                     : switching && file.path === currentFile?.relative && currentFile.workspaceKey === workspaceKeyFromSession(scope)
                       ? <span className="shrink-0 rounded-full bg-[var(--em-primary-alpha-08)] px-2 py-0.5 text-xs text-[var(--em-primary)]">当前</span>
                       : <FolderOpen className="h-4 w-4 shrink-0 text-muted-foreground" />}
                 </button>
               </div>;
            })}
          </div> : <p className="py-10 text-center text-sm text-muted-foreground">{query ? "没有匹配的表格" : source === "uploaded" ? "还没有上传表格，可以从本地上传并打开" : "这里还没有表格"}</p>}
        {loading && !(source === "workspace" && candidates.length === 0) && <p role="status" className="flex items-center justify-center gap-2 py-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />正在同步文件列表…</p>}
      </div>
      {truncated && <p className="text-xs text-muted-foreground">当前仅显示部分工作区文件，搜索范围为已加载的列表。</p>}
      {opening && <p role="status" className="flex items-center gap-2 text-sm"><Loader2 className="h-4 w-4 animate-spin shrink-0" /><span className="break-all">{opening}</span></p>}
      {error && <div role="alert" className="text-sm text-destructive"><p>{error}</p><Button variant="outline" size="sm" className="mt-2" onClick={() => {
        const uploaded = uploadedRef.current;
        if (uploaded) void openPath(uploaded.path, uploaded.session);
        else setReload((v) => v + 1);
      }}>{uploadedRef.current ? "重新打开已上传文件" : "刷新列表"}</Button></div>}
      <div className="flex items-center justify-between gap-3 flex-wrap border-t pt-3">
        <Button variant="outline" disabled={!scope || !!opening} onClick={() => inputRef.current?.click()}><Upload className="h-4 w-4" />{switching ? "从本地上传并更换" : "从本地上传并打开"}</Button>
        <span className="text-xs text-muted-foreground">也可将表格拖到这里</span>
      </div>
      <input ref={inputRef} type="file" accept=".xlsx,.xls,.xlsm,.xlsb,.csv,.tsv" className="hidden" aria-label="上传并打开表格" onChange={(e) => {
        const file = e.target.files?.[0]; e.target.value = "";
        if (file) void uploadAndOpen(file);
      }} />
    </DialogContent>
  </Dialog>;
}
