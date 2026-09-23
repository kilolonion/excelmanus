"use client";

import { useEffect, useId, useRef, useState, type DragEvent, type KeyboardEvent } from "react";
import {
  ArrowLeft,
  AtSign,
  ChevronDown,
  Columns2,
  Columns3,
  Combine,
  FileSpreadsheet,
  FolderOpen,
  GitCompare,
  LayoutGrid,
  Link2,
  Loader2,
  MessageSquare,
  MoreHorizontal,
  Plus,
  Star,
  X,
} from "lucide-react";
import { useWorkbookWorkspace } from "@/hooks/use-workbook-workspace";
import { useIsMobile } from "@/hooks/use-mobile";
import { useExcelStore } from "@/stores/excel-store";
import { useWorkbookConversationStore } from "@/stores/workbook-conversation-store";
import { useWorkbookWorkspaceStore, visibleWorkbookPaths } from "@/stores/workbook-workspace-store";
import { fileBaseName } from "@/lib/revision-display";
import { WorkbookPane } from "./WorkbookPane";
import { prepareWorkbookGroupAction } from "@/lib/workbook-group-actions";
import { prefetchExcelView } from "@/lib/excel-view-prefetch";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuCheckboxItem,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import styles from "./WorkbookWorkspace.module.css";

const LAYOUTS = [
  { count: 1 as const, label: "单表", icon: LayoutGrid },
  { count: 2 as const, label: "双表", icon: Columns2 },
  { count: 3 as const, label: "三表", icon: Columns3 },
];

export function WorkbookWorkspace({ active }: { active: boolean }) {
  const { key, workspace } = useWorkbookWorkspace();
  const isMobile = useIsMobile();
  const layout = useExcelStore((state) => state.fullViewLayout);
  const container = useRef<HTMLDivElement>(null);
  const tabScroll = useRef<HTMLDivElement>(null);
  const tabRefs = useRef(new Map<string, HTMLButtonElement>());
  const workspaceId = useId();
  const [width, setWidth] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [draggingPath, setDraggingPath] = useState<string | null>(null);
  const [dropActive, setDropActive] = useState(false);

  const closeFile = async (path: string) => {
    setError("");
    const restoreFocus = tabRefs.current.get(path)?.parentElement?.contains(document.activeElement);
    if (!await useExcelStore.getState().closeWorkbook(path)) {
      setError(`${fileBaseName(path)} 尚未保存，已保留标签。请先处理保存问题。`);
    } else if (restoreFocus) {
      const next = useWorkbookWorkspaceStore.getState().workspaces[key]?.focused;
      if (next) tabRefs.current.get(next)?.focus();
    }
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

  useEffect(() => {
    if (!active) return;
    const scroll = tabScroll.current;
    const tab = workspace.focused && tabRefs.current.get(workspace.focused)?.parentElement;
    if (!scroll || !tab) return;
    const viewport = scroll.getBoundingClientRect();
    const bounds = tab.getBoundingClientRect();
    if (bounds.left < viewport.left + 10) scroll.scrollLeft -= viewport.left + 10 - bounds.left;
    else if (bounds.right > viewport.right - 10) scroll.scrollLeft += bounds.right - viewport.right + 10;
  }, [active, workspace.focused, workspace.files.length, width]);

  const primary = workspace.files[0];
  const focusedFile = workspace.files.find((file) => file.path === workspace.focused) ?? primary;
  const compareTarget = workspace.focused !== primary?.path
    ? workspace.focused
    : paths.find((path) => path !== primary?.path) ?? workspace.files[1]?.path;
  const mergePaths = [...new Set([
    primary?.path,
    ...paths,
    ...(paths.length === 1 && compareTarget ? [compareTarget] : []),
  ])].filter((path): path is string => Boolean(path));

  const groupAction = async (kind: "compare" | "merge" | "reference") => {
    if (!primary || busy) return;
    setBusy(true);
    setError("");
    try {
      const files = kind === "compare"
        ? [primary.path, compareTarget!]
        : kind === "merge"
          ? mergePaths
          : paths;
      await prepareWorkbookGroupAction(kind, files);
      if (kind === "reference" && isMobile) useExcelStore.getState().closeFullView();
    } catch (err) {
      setError(err instanceof Error ? err.message : "表格操作失败，请重试");
    } finally {
      setBusy(false);
    }
  };

  if (!primary) return null;

  const closeWorkspace = () => useExcelStore.getState().closeFullView();
  const toggleLayout = () => {
    const state = useWorkbookWorkspaceStore.getState().workspaces[key];
    const focused = state?.files.find((file) => file.path === state.focused) ?? primary;
    useExcelStore.getState().openFullView(
      focused.path,
      focused.sheet,
      layout === "split" ? "embedded" : "split",
    );
  };

  const openWorkbookPicker = () => useWorkbookConversationStore.getState().openPicker(layout);

  const setLayout = (value: string) => {
    const count = Number(value);
    if (count === 1 || count === 2 || count === 3) {
      useWorkbookWorkspaceStore.getState().setPaneCount(key, count);
    }
  };

  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % workspace.files.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + workspace.files.length) % workspace.files.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = workspace.files.length - 1;
    else if (event.key === "Delete") {
      event.preventDefault();
      void closeFile(workspace.files[index].path);
      return;
    } else return;
    event.preventDefault();
    const path = workspace.files[next].path;
    prefetchExcelView(path);
    useExcelStore.getState().focusWorkbook(path);
    tabRefs.current.get(path)?.focus();
  };

  const readDraggedPath = (event: DragEvent<HTMLElement>) =>
    event.dataTransfer.getData("application/x-excelmanus-workbook")
      || event.dataTransfer.getData("text/plain");

  const beginTabDrag = (event: DragEvent<HTMLDivElement>, path: string) => {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-excelmanus-workbook", path);
    event.dataTransfer.setData("text/plain", path);
    setDraggingPath(path);
  };

  const finishTabDrag = () => {
    setDraggingPath(null);
    setDropActive(false);
  };

  const handleGridDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const path = readDraggedPath(event);
    if (!path || !workspace.files.some((file) => file.path === path)) {
      finishTabDrag();
      return;
    }
    useExcelStore.getState().focusWorkbook(path);
    const count = Math.min(3, Math.max(2, workspace.paneCount, workspace.files.length > 2 ? 3 : 2)) as 1 | 2 | 3;
    useWorkbookWorkspaceStore.getState().setPaneCount(key, count);
    finishTabDrag();
  };

  return (
    <div ref={container} className={styles.workspace} data-workbook-workspace data-pane-count={paths.length}>
      <header className={styles.toolbar}>
        <div className={styles.tabs}>
          <div ref={tabScroll} className={styles.tabScroll} role="tablist" aria-label="已打开的表格">
            {workspace.files.map((file, index) => {
              const focused = workspace.focused === file.path;
              return (
                <div
                  key={file.path}
                  className={styles.tab}
                  data-focused={focused}
                  data-dragging={draggingPath === file.path}
                  data-workbook-tab={file.path}
                  role="presentation"
                  draggable={width >= 720 && workspace.files.length > 1}
                  onDragStart={(event) => beginTabDrag(event, file.path)}
                  onDragEnd={finishTabDrag}
                  onAuxClick={(event) => {
                    if (event.button === 1) { event.preventDefault(); void closeFile(file.path); }
                  }}
                >
                  <button
                    type="button"
                    role="tab"
                    id={`${workspaceId}-tab-${encodeURIComponent(file.path)}`}
                    aria-controls={paths.includes(file.path) ? `${workspaceId}-pane-${encodeURIComponent(file.path)}` : undefined}
                    ref={(element) => {
                      if (element) tabRefs.current.set(file.path, element);
                      else tabRefs.current.delete(file.path);
                    }}
                    className={styles.tabName}
                    title={`${file.path}${file.sheet ? `\n当前工作表：${file.sheet}` : ""}`}
                    aria-selected={focused}
                    tabIndex={focused ? 0 : -1}
                    onKeyDown={(event) => handleTabKeyDown(event, index)}
                    onClick={() => {
                      prefetchExcelView(file.path);
                      useExcelStore.getState().focusWorkbook(file.path);
                    }}
                  >
                    <FileSpreadsheet size={16} className={styles.tabIcon} aria-hidden="true" />
                    <span className={styles.tabFileName}>{fileBaseName(file.path)}</span>
                  </button>
                  <button
                    type="button"
                    className={styles.tabClose}
                    aria-label={`关闭标签 ${fileBaseName(file.path)}`}
                    title="关闭此表格"
                    tabIndex={focused ? 0 : -1}
                    onClick={() => void closeFile(file.path)}
                  >
                    <X size={13} />
                  </button>
                </div>
              );
            })}
          </div>
          <button
            type="button"
            className={styles.tabAdd}
            aria-label="添加表格"
            title="打开另一份表格"
            onClick={openWorkbookPicker}
          >
            <Plus size={16} />
          </button>
        </div>

        <div className={styles.headerActions}>
          {workspace.files.length > 1 && <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button type="button" className={styles.iconButton} aria-label="已打开的表格列表" title="已打开的表格列表">
                <ChevronDown size={16} />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className={styles.fileMenu}>
              <DropdownMenuLabel className={styles.menuSectionLabel}>{workspace.files.length} 份已打开的表格</DropdownMenuLabel>
              <DropdownMenuRadioGroup value={workspace.focused ?? primary.path} onValueChange={(path) => {
                prefetchExcelView(path);
                useExcelStore.getState().focusWorkbook(path);
              }}>
                {workspace.files.map((file, index) => <DropdownMenuRadioItem key={file.path} value={file.path} title={file.path}>
                  <FileSpreadsheet />
                  <span className={styles.fileMenuName}>{fileBaseName(file.path)}</span>
                  {index === 0 && <span className={styles.menuHint}>主表</span>}
                </DropdownMenuRadioItem>)}
              </DropdownMenuRadioGroup>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={openWorkbookPicker}><Plus /><span>打开表格</span></DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button type="button" className={styles.button} aria-label="视图" title="视图与布局">
                <Columns2 size={16} />
                <span className={styles.actionLabel}>视图</span>
                <ChevronDown size={12} className={styles.actionLabel} />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className={styles.actionMenu}>
              <DropdownMenuLabel className={styles.menuSectionLabel}>并列显示</DropdownMenuLabel>
              <DropdownMenuRadioGroup value={String(paths.length)} onValueChange={setLayout} aria-label="同时显示的表格数量">
                {LAYOUTS.map(({ count, label, icon: Icon }) => (
                  <DropdownMenuRadioItem key={count} value={String(count)} disabled={count > workspace.files.length || (width < 720 && count > 1)}>
                    <Icon />
                    <span>{label}</span>
                    <span className={styles.menuHint}>{count === 1 ? "聚焦当前" : `同时 ${count} 张`}</span>
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
              {width < 720 && <p className={styles.menuNote}>当前宽度显示单表，通过标签切换</p>}
              <DropdownMenuSeparator />
              <DropdownMenuCheckboxItem
                checked={workspace.linkSelection}
                disabled={paths.length < 2}
                onCheckedChange={(checked) => useWorkbookWorkspaceStore.getState().setLinkSelection(key, checked === true)}
                onSelect={(event) => event.preventDefault()}
              >
                <Link2 />
                <span>同步定位</span>
                <span className={styles.menuHint}>{workspace.linkSelection ? "已开启" : "已关闭"}</span>
              </DropdownMenuCheckboxItem>
              <DropdownMenuItem onSelect={toggleLayout} disabled={isMobile}>
                <MessageSquare />
                <span>{layout === "split" ? "展开表格" : "并排对话"}</span>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button type="button" className={styles.iconButton} aria-label="更多" title="更多" disabled={busy}>
                {busy ? <Loader2 className={styles.spin} size={16} /> : <MoreHorizontal size={18} />}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className={styles.actionMenu}>
              <DropdownMenuLabel className={styles.menuSectionLabel}>当前表格</DropdownMenuLabel>
              <DropdownMenuItem onSelect={openWorkbookPicker}><FolderOpen /><span>打开表格</span></DropdownMenuItem>
              <DropdownMenuItem disabled={focusedFile.path === primary.path} onSelect={() => useExcelStore.getState().setPrimaryWorkbook(focusedFile.path)}>
                <Star /><span>设为主表</span>{focusedFile.path === primary.path && <span className={styles.menuHint}>已是主表</span>}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => void closeFile(focusedFile.path)}><X /><span>关闭当前表格</span></DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuLabel className={styles.menuSectionLabel}>多表协作</DropdownMenuLabel>
              <DropdownMenuItem disabled={!compareTarget || busy} onSelect={() => void groupAction("compare")}>
                <GitCompare />
                <span>与主表对比</span>
                <span className={styles.menuHint}>{compareTarget ? "两张表" : "需再打开一张"}</span>
              </DropdownMenuItem>
              <DropdownMenuItem disabled={mergePaths.length < 2 || busy} onSelect={() => void groupAction("merge")}>
                <Combine />
                <span>生成合并方案</span>
                <span className={styles.menuHint}>{mergePaths.length} 张表</span>
              </DropdownMenuItem>
              <DropdownMenuItem disabled={paths.length === 0 || busy} onSelect={() => void groupAction("reference")}>
                <AtSign />
                <span>引用同屏表格</span>
                <span className={styles.menuHint}>{paths.length} 张表</span>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <span className={styles.actionDivider} aria-hidden="true" />
          <button type="button" className={styles.iconButton} onClick={closeWorkspace} aria-label="返回对话" title="返回对话，保留已打开的表格">
            <ArrowLeft size={15} />
          </button>
        </div>
      </header>

      {error && <p className={styles.error} role="alert"><span aria-hidden="true">!</span>{error}</p>}

      <div
        className={`${styles.grid} ${paths.length === 3 && width < 1440 ? styles.stacked : ""}`}
        data-drop-active={dropActive}
        onDragOver={(event) => {
          if (!draggingPath) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
          setDropActive(true);
        }}
        onDragLeave={(event) => {
          if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
          setDropActive(false);
        }}
        onDrop={handleGridDrop}
        style={{ gridTemplateColumns: paths.length === 3 && width < 1440 ? undefined : `repeat(${paths.length}, minmax(0, 1fr))` }}
      >
        {dropActive && <div className={styles.dropHint}>松开以并列查看表格</div>}
        {paths.map((path) => (
          <WorkbookPane
            key={path}
            id={`${workspaceId}-pane-${encodeURIComponent(path)}`}
            labelledBy={`${workspaceId}-tab-${encodeURIComponent(path)}`}
            path={path}
            active={active}
            focused={workspace.focused === path}
            onClose={paths.length > 1 ? () => void closeFile(path) : undefined}
            onExpand={workspace.files.length > 1 ? () => {
              useExcelStore.getState().focusWorkbook(path);
              useWorkbookWorkspaceStore.getState().setPaneCount(key, workspace.paneCount === 1 ? 3 : 1);
            } : undefined}
            expandTitle={workspace.paneCount === 1 ? "恢复多表展示" : "单独查看此表"}
          />
        ))}
      </div>

    </div>
  );
}
