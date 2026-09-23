"use client";

import { useEffect, useRef, useState, type DragEvent } from "react";
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
  GripVertical,
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
  { count: 1 as const, label: "单表", shortLabel: "1", icon: LayoutGrid },
  { count: 2 as const, label: "双表", shortLabel: "2", icon: Columns2 },
  { count: 3 as const, label: "三表", shortLabel: "3", icon: Columns3 },
];

export function WorkbookWorkspace({ active }: { active: boolean }) {
  const { key, workspace } = useWorkbookWorkspace();
  const isMobile = useIsMobile();
  const layout = useExcelStore((state) => state.fullViewLayout);
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [draggingPath, setDraggingPath] = useState<string | null>(null);
  const [dropActive, setDropActive] = useState(false);

  const closeFile = async (path: string) => {
    setError("");
    if (!await useExcelStore.getState().closeWorkbook(path)) {
      setError(`${fileBaseName(path)} 尚未保存，已保留标签。请先处理保存问题。`);
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

  const primary = workspace.files[0];
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
        <div className={styles.identity}>
          <div className={styles.workspaceIcon} aria-hidden="true"><LayoutGrid size={17} /></div>
        </div>

        <div
          className={styles.tabs}
          role="group"
          aria-label="表格工作区"
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
          onDrop={(event) => {
            event.preventDefault();
            const path = readDraggedPath(event);
            if (path && workspace.files.some((file) => file.path === path)) {
              useExcelStore.getState().focusWorkbook(path);
              useWorkbookWorkspaceStore.getState().setPaneCount(key, Math.min(3, Math.max(2, workspace.paneCount)) as 1 | 2 | 3);
            }
            finishTabDrag();
          }}
        >
          <div className={styles.tabScroll} role="tablist" aria-label="已打开的表格">
            {workspace.files.map((file, index) => {
              const focused = workspace.focused === file.path;
              return (
                <div
                  key={file.path}
                  className={styles.tab}
                  data-focused={focused}
                  data-dragging={draggingPath === file.path}
                  draggable
                  onDragStart={(event) => beginTabDrag(event, file.path)}
                  onDragEnd={finishTabDrag}
                >
                  <button
                    type="button"
                    role="tab"
                    className={styles.tabName}
                    title={file.path}
                    aria-selected={focused}
                    onClick={() => useExcelStore.getState().focusWorkbook(file.path)}
                  >
                    <GripVertical size={12} className={styles.tabDragIcon} aria-hidden="true" />
                    <FileSpreadsheet size={14} className={styles.tabIcon} aria-hidden="true" />
                    <span className={styles.badge}>{index === 0 ? "主表" : "参考"}</span>
                    <span className={styles.tabSheet}>{file.sheet ?? "工作表"}</span>
                    <span className={styles.tabFileName}>{fileBaseName(file.path)}</span>
                  </button>
                  {index > 0 && <button
                    type="button"
                    className={styles.tabPrimary}
                    aria-label={`将 ${fileBaseName(file.path)} 设为主表`}
                    title="设为主表"
                    onClick={() => useExcelStore.getState().setPrimaryWorkbook(file.path)}
                  >
                    <Star size={12} />
                  </button>}
                  <button
                    type="button"
                    className={styles.tabClose}
                    aria-label={`关闭标签 ${fileBaseName(file.path)}`}
                    title="关闭此表格"
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
            title="添加表格并打开多表工作区"
            onClick={openWorkbookPicker}
          >
            <Plus size={16} />
          </button>
        </div>

        <div className={styles.headerActions}>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button type="button" className={`${styles.button} ${styles.moreButton}`} aria-label="更多" title="更多" disabled={busy}>
                {busy ? <Loader2 className={styles.spin} size={15} /> : <MoreHorizontal size={15} />}
                <span>{busy ? "处理中…" : "更多"}</span>
                {!busy && <ChevronDown size={13} />}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className={styles.actionMenu}>
              <DropdownMenuLabel>处理当前工作区</DropdownMenuLabel>
              <DropdownMenuLabel className={styles.menuSectionLabel}>并列显示</DropdownMenuLabel>
              <DropdownMenuRadioGroup value={String(workspace.paneCount)} onValueChange={setLayout} aria-label="同时显示的表格数量">
                {LAYOUTS.map(({ count, label, icon: Icon }) => (
                  <DropdownMenuRadioItem key={count} value={String(count)}>
                    <Icon />
                    <span>{label}</span>
                    <span className={styles.menuHint}>{count === 1 ? "聚焦当前" : `同时 ${count} 张`}</span>
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
              <DropdownMenuCheckboxItem
                checked={workspace.linkSelection}
                onCheckedChange={(checked) => useWorkbookWorkspaceStore.getState().setLinkSelection(key, checked === true)}
                onSelect={(event) => event.preventDefault()}
              >
                <Link2 />
                <span>同步定位</span>
                <span className={styles.menuHint}>{workspace.linkSelection ? "已开启" : "已关闭"}</span>
              </DropdownMenuCheckboxItem>
              <DropdownMenuItem onSelect={toggleLayout}>
                <MessageSquare />
                <span>{layout === "split" ? "展开表格" : "并排对话"}</span>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
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
              <DropdownMenuItem onSelect={openWorkbookPicker}>
                <FolderOpen />
                <span>打开表格</span>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={closeWorkspace}>
                <ArrowLeft />
                <span>返回对话</span>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <button type="button" className={styles.iconButton} onClick={closeWorkspace} aria-label="返回对话" title="返回对话">
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
        {paths.map((path) => (
          <WorkbookPane
            key={path}
            path={path}
            active={active}
            focused={workspace.focused === path}
            onClose={() => void closeFile(path)}
            onExpand={() => {
              useExcelStore.getState().focusWorkbook(path);
              useWorkbookWorkspaceStore.getState().setPaneCount(key, workspace.paneCount === 1 ? 3 : 1);
            }}
            expandTitle={workspace.paneCount === 1 ? "恢复多表展示" : "单独查看此表"}
          />
        ))}
      </div>

      <footer className={styles.status}>
        <span className={styles.statusItem}><span className={styles.statusDot} aria-hidden="true" />{workspace.files.length} 张已打开</span>
        <span className={styles.statusItem}>{paths.length} 张同屏</span>
        {width < 720 && workspace.paneCount > 1 && <span className={styles.statusNote}>当前宽度使用单表，可通过上方标签切换</span>}
        {workspace.linkSelection && <span className={styles.statusItem}><Link2 size={12} />同名工作表同步定位</span>}
        <span className={styles.statusSpacer} />
        <span className={styles.statusHint}>主表：{fileBaseName(primary.path)}</span>
      </footer>
    </div>
  );
}
