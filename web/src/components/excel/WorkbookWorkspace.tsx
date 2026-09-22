"use client";

import { useEffect, useRef, useState } from "react";
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
  Rows3,
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
  DropdownMenuItem,
  DropdownMenuLabel,
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

  return (
    <div ref={container} className={styles.workspace} data-workbook-workspace data-pane-count={paths.length}>
      <header className={styles.header}>
        <div className={styles.identity}>
          <div className={styles.workspaceIcon} aria-hidden="true"><LayoutGrid size={17} /></div>
          <div className={styles.titleStack}>
            <div className={styles.titleRow}>
              <h1 className={styles.title}>多表工作区</h1>
              <span className={styles.titleCount}>{workspace.files.length} 个工作簿</span>
            </div>
            <p className={styles.subtitle}>
              {paths.length > 1 ? `正在同屏查看 ${paths.length} 张表格` : "聚焦一张表格，也可随时加入更多工作簿"}
            </p>
          </div>
          <div className={styles.primaryPill} title={`主表：${primary.path}`}>
            <span className={styles.primaryDot} aria-hidden="true" />
            <span className={styles.primaryLabel}>主表</span>
            <strong>{fileBaseName(primary.path)}</strong>
          </div>
        </div>

        <div className={styles.headerActions}>
          <button
            type="button"
            className={`${styles.button} ${styles.primaryButton}`}
            aria-label="打开表格"
            title="打开表格"
            onClick={() => useWorkbookConversationStore.getState().openPicker(layout)}
          >
            <FolderOpen size={14} />
            <span>打开表格</span>
          </button>
          <button
            type="button"
            className={`${styles.button} ${styles.secondaryButton}`}
            aria-label={layout === "split" ? "展开表格" : "并排对话"}
            title={layout === "split" ? "展开表格" : "并排对话"}
            onClick={toggleLayout}
          >
            <MessageSquare size={14} />
            <span>{layout === "split" ? "展开表格" : "并排对话"}</span>
          </button>
          <button type="button" className={styles.iconButton} onClick={closeWorkspace} aria-label="返回对话" title="返回对话">
            <ArrowLeft size={15} />
          </button>
        </div>
      </header>

      <div className={styles.controlBar}>
        <div className={styles.controlGroup}>
          <span className={styles.controlLabel}>同屏布局</span>
          <div className={styles.segmented} role="group" aria-label="同时显示的表格数量">
            {LAYOUTS.map(({ count, label, shortLabel, icon: Icon }) => (
              <button
                key={count}
                type="button"
                className={styles.segmentButton}
                data-active={workspace.paneCount === count}
                aria-pressed={workspace.paneCount === count}
                onClick={() => useWorkbookWorkspaceStore.getState().setPaneCount(key, count)}
                title={`显示${label}`}
              >
                <Icon size={14} />
                <span className={styles.segmentLongLabel}>{label}</span>
                <span className={styles.segmentShortLabel}>{shortLabel}</span>
              </button>
            ))}
          </div>
        </div>

        <button
          type="button"
          className={styles.linkToggle}
          data-active={workspace.linkSelection}
          aria-pressed={workspace.linkSelection}
          aria-label="同步定位"
          title="在同名工作表中同步定位选中的区域，不会同步修改内容"
          onClick={() => useWorkbookWorkspaceStore.getState().setLinkSelection(key, !workspace.linkSelection)}
        >
          <Link2 size={14} />
          <span>同步定位</span>
          <span className={styles.toggleState}>{workspace.linkSelection ? "已开启" : "已关闭"}</span>
        </button>

        <div className={styles.controlSpacer} />
        <span className={styles.focusSummary} title={focusedFile?.path}>
          <span className={styles.focusSummaryDot} aria-hidden="true" />
          当前聚焦：{fileBaseName(focusedFile?.path ?? primary.path)}
        </span>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button type="button" className={`${styles.button} ${styles.moreButton}`} aria-label="更多操作" title="更多操作" disabled={busy}>
              {busy ? <Loader2 className={styles.spin} size={15} /> : <MoreHorizontal size={15} />}
              <span>{busy ? "处理中…" : "更多操作"}</span>
              {!busy && <ChevronDown size={13} />}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className={styles.actionMenu}>
            <DropdownMenuLabel>处理当前工作区</DropdownMenuLabel>
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
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={closeWorkspace}>
              <ArrowLeft />
              <span>返回对话</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className={styles.tabs} role="tablist" aria-label="已打开的表格">
        <div className={styles.tabScroll}>
          {workspace.files.map((file, index) => {
            const focused = workspace.focused === file.path;
            return (
              <div key={file.path} className={styles.tab} data-focused={focused}>
                <button
                  type="button"
                  role="tab"
                  className={styles.tabName}
                  title={file.path}
                  aria-selected={focused}
                  onClick={() => useExcelStore.getState().focusWorkbook(file.path)}
                >
                  <FileSpreadsheet size={14} className={styles.tabIcon} aria-hidden="true" />
                  <span className={styles.badge}>{index === 0 ? "主表" : "参考"}</span>
                  <span className={styles.tabFileName}>{fileBaseName(file.path)}</span>
                  <span className={styles.tabSheet}>{file.sheet ?? "工作表"}</span>
                </button>
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
        <span className={styles.tabHint}><Rows3 size={13} />点击标签切换聚焦表格</span>
      </div>

      {error && <p className={styles.error} role="alert"><span aria-hidden="true">!</span>{error}</p>}

      <div
        className={`${styles.grid} ${paths.length === 3 && width < 1440 ? styles.stacked : ""}`}
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
