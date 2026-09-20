"use client";

import { useEffect, useState, type ReactNode } from "react";
import dynamic from "next/dynamic";
import { useExcelStore } from "@/stores/excel-store";
import { useWordStore } from "@/stores/word-store";
import {
  resolveWorkspaceSurface,
  workspaceLayout,
} from "@/lib/workspace-surface";
import { useIsMobile } from "@/hooks/use-mobile";
import styles from "./WorkspaceViewHost.module.css";

const loading = () => <div role="status" className="flex h-full items-center justify-center text-sm text-muted-foreground">正在准备工作区…</div>;
const ExcelCompareView = dynamic(() => import("@/components/excel/ExcelCompareView").then((m) => m.ExcelCompareView), { ssr: false, loading });
const ExcelFullView = dynamic(() => import("@/components/excel/ExcelFullView").then((m) => m.ExcelFullView), { ssr: false, loading });
const WordFullView = dynamic(() => import("@/components/word/WordFullView").then((m) => m.WordFullView), { ssr: false, loading });

export function WorkspaceViewHost({ children, composer }: { children: ReactNode; composer?: ReactNode }) {
  const isMobile = useIsMobile();
  const fullViewPath = useExcelStore((s) => s.fullViewPath);
  const fullViewLayout = useExcelStore((s) => s.fullViewLayout);
  const compareMode = useExcelStore((s) => s.compareMode);
  const wordFullViewPath = useWordStore((s) => s.fullViewPath);
  const [excelMounted, setExcelMounted] = useState(() => !!fullViewPath);

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
        <div className={styles.chatHeading}>关于这份表格</div>
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
          <ExcelFullView />
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
