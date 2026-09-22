"use client";

import { useEffect, useState, type ReactNode } from "react";
import dynamic from "next/dynamic";
import { MessageSquareText } from "lucide-react";
import { useExcelStore } from "@/stores/excel-store";
import { useWordStore } from "@/stores/word-store";
import {
  resolveWorkspaceSurface,
  workspaceLayout,
} from "@/lib/workspace-surface";
import { useIsMobile } from "@/hooks/use-mobile";
import { useWorkbookWorkspace } from "@/hooks/use-workbook-workspace";
import styles from "./WorkspaceViewHost.module.css";
const loading = () => <div role="status" className="flex h-full items-center justify-center text-sm text-muted-foreground">正在准备工作区…</div>;
const ExcelCompareView = dynamic(() => import("@/components/excel/ExcelCompareView").then((m) => m.ExcelCompareView), { ssr: false, loading });
const WorkbookWorkspace = dynamic(() => import("@/components/excel/WorkbookWorkspace").then((m) => m.WorkbookWorkspace), { ssr: false, loading });
const WordFullView = dynamic(() => import("@/components/word/WordFullView").then((m) => m.WordFullView), { ssr: false, loading });

export function WorkspaceViewHost({ children, composer }: { children: ReactNode; composer?: ReactNode }) {
  const isMobile = useIsMobile();
  const fullViewPath = useExcelStore((s) => s.fullViewPath);
  const fullViewLayout = useExcelStore((s) => s.fullViewLayout);
  const compareMode = useExcelStore((s) => s.compareMode);
  const wordFullViewPath = useWordStore((s) => s.fullViewPath);
  const [excelMounted, setExcelMounted] = useState(() => !!fullViewPath);
  const { key, workspace, workspaceKey } = useWorkbookWorkspace();
  const activeWorkspaceKey = useExcelStore((s) => s.activeWorkspaceKey);
  const panelOpen = useExcelStore((s) => s.panelOpen);

  if (fullViewPath && !excelMounted) setExcelMounted(true);

  const surface = resolveWorkspaceSurface({
    wordFullViewPath,
    compareMode,
    fullViewPath,
  });
  const { split, chatVisible, composerVisible, fullHeightSheet } = workspaceLayout(surface, fullViewLayout, isMobile);

  useEffect(() => {
    if (surface !== "excel") return;
    const id = requestAnimationFrame(() => {
      window.dispatchEvent(new Event("resize"));
    });
    return () => cancelAnimationFrame(id);
  }, [surface, split]);

  return (
    <div className={`${styles.host} ${split ? styles.split : ""}`} data-workspace-surface={surface} data-workbook-layout={surface === "excel" ? fullViewLayout : undefined}>
      <div
        className={`${styles.chat} ${chatVisible ? "" : styles.inactive}`}
        aria-hidden={!chatVisible}
        inert={!chatVisible ? true : undefined}
      >
        <div className={styles.chatHeading}>
          <div className={styles.chatHeadingTitle}>
            <MessageSquareText size={15} aria-hidden="true" />
            <span>{workspace.files.length > 1 ? "关于这些表格" : "关于这份表格"}</span>
          </div>
          <span className={styles.chatHeadingHint}>{workspace.files.length > 1 ? "边看多张表，边和助手讨论" : "边看数据，边和助手讨论"}</span>
        </div>
        {children}
      </div>
      <div className={`${styles.composer} ${composerVisible ? "" : styles.inactive}`} aria-hidden={!composerVisible} inert={!composerVisible ? true : undefined}>
        {composer}
      </div>
      {excelMounted && (
        <div
          className={`${styles.sheet} ${fullHeightSheet ? styles.fullHeightSheet : ""} ${surface === "excel" ? "" : styles.inactive}`}
          aria-hidden={surface !== "excel"}
          inert={surface !== "excel" ? true : undefined}
        >
          {workspace.files.length > 0 && <WorkbookWorkspace key={key} active={surface === "excel" && activeWorkspaceKey === workspaceKey && !panelOpen} />}
        </div>
      )}
      {surface === "compare" && (
        <div className={`${styles.sheet} ${styles.fullHeightSheet}`}>
          <ExcelCompareView />
        </div>
      )}
      {surface === "word" && (
        <div className={styles.word}>
          <WordFullView />
        </div>
      )}
    </div>
  );
}
