"use client";

import { useEffect, useRef, useState } from "react";
import { FolderOpen, MessageSquare, X } from "lucide-react";
import { useWorkbookWorkspace } from "@/hooks/use-workbook-workspace";
import { useIsMobile } from "@/hooks/use-mobile";
import { useExcelStore } from "@/stores/excel-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { useWorkbookWorkspaceStore, visibleWorkbookPaths } from "@/stores/workbook-workspace-store";
import { fileBaseName } from "@/lib/revision-display";
import { WorkbookPane } from "./WorkbookPane";
import { prepareWorkbookGroupAction } from "@/lib/workbook-group-actions";
import styles from "./WorkbookWorkspace.module.css";

export function WorkbookWorkspace({ active }: { active: boolean }) {
  const { key, workspace } = useWorkbookWorkspace();
  const isMobile = useIsMobile();
  const layout = useExcelStore((state) => state.fullViewLayout);
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const closeFile = async (path: string) => {
    setError("");
    if (!await useExcelStore.getState().closeWorkbook(path)) setError(`${fileBaseName(path)} 尚未保存，已保留标签。请先处理保存问题。`);
  };
  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  const paths = visibleWorkbookPaths(workspace, width < 720 ? 1 : 3);
  const signature = JSON.stringify(paths);
  useEffect(() => {
    useWorkbookWorkspaceStore.getState().setVisible(key, active ? JSON.parse(signature) : []);
    return () => { useWorkbookWorkspaceStore.getState().setVisible(key, []); };
  }, [key, active, signature]);
  const primary = workspace.files[0];
  const compareTarget = workspace.focused !== primary?.path ? workspace.focused : paths.find((path) => path !== primary?.path) ?? workspace.files[1]?.path;
  const mergePaths = [...new Set([primary?.path, ...paths, ...(paths.length === 1 && compareTarget ? [compareTarget] : [])])].filter((path): path is string => Boolean(path));
  const groupAction = async (kind: "compare" | "merge" | "reference") => {
    setBusy(true); setError("");
    try {
      const files = kind === "compare" ? [primary.path, compareTarget!]
        : kind === "merge" ? mergePaths : paths;
      await prepareWorkbookGroupAction(kind, files);
      if (kind === "reference" && isMobile) useExcelStore.getState().closeFullView();
    } catch (err) { setError(err instanceof Error ? err.message : "表格操作失败，请重试"); }
    finally { setBusy(false); }
  };
  if (!primary) return null;
  return <div ref={container} className={styles.workspace} data-workbook-workspace data-pane-count={paths.length}>
    <div className={styles.toolbar}>
      <span className="font-medium">多表工作区</span>
      <button type="button" className={styles.button} onClick={() => useWorkbookConversationStore.getState().openPicker(layout)}><FolderOpen size={14} />打开表格</button>
      <div className={styles.actions} role="group" aria-label="同时显示的表格数量">
        {([1, 2, 3] as const).map((count) => <button key={count} type="button" className={styles.button} aria-pressed={workspace.paneCount === count}
          onClick={() => useWorkbookWorkspaceStore.getState().setPaneCount(key, count)}>{count}表</button>)}
        <button type="button" className={styles.button} aria-pressed={workspace.linkSelection}
          title="在同名工作表中同步定位选中的区域；不会同步修改内容"
          onClick={() => useWorkbookWorkspaceStore.getState().setLinkSelection(key, !workspace.linkSelection)}>联动定位</button>
        <button type="button" className={styles.button} disabled={!compareTarget || busy} onClick={() => void groupAction("compare")}>与主表对比</button>
        <button type="button" className={styles.button} disabled={mergePaths.length < 2 || busy} onClick={() => void groupAction("merge")}>合并方案</button>
        <button type="button" className={styles.button} disabled={busy} onClick={() => void groupAction("reference")}>引用可见表格</button>
        <button type="button" className={styles.button} onClick={() => {
          const focused = workspace.files.find((file) => file.path === workspace.focused) ?? primary;
          useExcelStore.getState().openFullView(focused.path, focused.sheet, layout === "split" ? "embedded" : "split");
        }}><MessageSquare size={14} />{layout === "split" ? "展开表格" : "并排对话"}</button>
        <button type="button" className={styles.button} onClick={() => useExcelStore.getState().closeFullView()}>返回对话</button>
      </div>
    </div>
    <div className={styles.tabs} aria-label="已打开的表格">
      {workspace.files.map((file, index) => <div key={file.path} className={styles.tab} data-focused={workspace.focused === file.path}>
        <button type="button" className={styles.tabName} title={file.path} aria-pressed={workspace.focused === file.path} onClick={() => useExcelStore.getState().focusWorkbook(file.path)}>
          <span className={styles.badge}>{index === 0 ? "主表" : "参考"}</span>{fileBaseName(file.path)}
        </button>
        <button type="button" className={styles.button} aria-label={`关闭标签 ${fileBaseName(file.path)}`} onClick={() => void closeFile(file.path)}><X size={12} /></button>
      </div>)}
    </div>
    {error && <p className="px-3 py-1 text-xs text-destructive" role="alert">{error}</p>}
    <div className={`${styles.grid} ${paths.length === 3 && width < 1440 ? styles.stacked : ""}`} style={{ gridTemplateColumns: paths.length === 3 && width < 1440 ? undefined : `repeat(${paths.length}, minmax(0, 1fr))` }}>
      {paths.map((path) => <WorkbookPane key={path} path={path} active={active} focused={workspace.focused === path}
        onClose={() => void closeFile(path)}
        onExpand={() => { useExcelStore.getState().focusWorkbook(path); useWorkbookWorkspaceStore.getState().setPaneCount(key, workspace.paneCount === 1 ? 3 : 1); }}
        expandTitle={workspace.paneCount === 1 ? "恢复多表展示" : "单独查看此表"} />)}
    </div>
    <div className={styles.status}>
      <span>主表：{fileBaseName(primary.path)}</span><span>{workspace.files.length} 张已打开 · {paths.length} 张同屏</span>
      {width < 720 && workspace.paneCount > 1 && <span>当前宽度使用单表，可通过标签切换</span>}
      {workspace.linkSelection && <span>同名工作表联动定位</span>}
    </div>
  </div>;
}
