"use client";

import { useEffect, useState, type ReactNode } from "react";
import dynamic from "next/dynamic";
import { useExcelStore } from "@/stores/excel-store";
import { useWordStore } from "@/stores/word-store";
import {
  resolveWorkspaceSurface,
  workspaceKeepAliveLayerClass,
} from "@/lib/workspace-surface";

const loading = () => <div role="status" className="flex h-full items-center justify-center text-sm text-muted-foreground">正在准备工作区…</div>;
const ExcelCompareView = dynamic(() => import("@/components/excel/ExcelCompareView").then((m) => m.ExcelCompareView), { ssr: false, loading });
const ExcelFullView = dynamic(() => import("@/components/excel/ExcelFullView").then((m) => m.ExcelFullView), { ssr: false, loading });
const WordFullView = dynamic(() => import("@/components/word/WordFullView").then((m) => m.WordFullView), { ssr: false, loading });

export function WorkspaceViewHost({ children }: { children: ReactNode }) {
  const fullViewPath = useExcelStore((s) => s.fullViewPath);
  const compareMode = useExcelStore((s) => s.compareMode);
  const wordFullViewPath = useWordStore((s) => s.fullViewPath);
  const [excelMounted, setExcelMounted] = useState(() => !!fullViewPath);

  if (fullViewPath && !excelMounted) setExcelMounted(true);

  const surface = resolveWorkspaceSurface({
    wordFullViewPath,
    compareMode,
    fullViewPath,
  });

  useEffect(() => {
    if (surface !== "excel") return;
    const id = requestAnimationFrame(() => {
      window.dispatchEvent(new Event("resize"));
    });
    return () => cancelAnimationFrame(id);
  }, [surface]);

  return (
    <div className="relative flex-1 min-h-0" data-workspace-surface={surface}>
      <div
        className={workspaceKeepAliveLayerClass(surface === "chat")}
        aria-hidden={surface !== "chat"}
        inert={surface !== "chat" ? true : undefined}
      >
        {children}
      </div>
      {excelMounted && (
        <div
          className={workspaceKeepAliveLayerClass(surface === "excel")}
          aria-hidden={surface !== "excel"}
          inert={surface !== "excel" ? true : undefined}
        >
          <ExcelFullView />
        </div>
      )}
      {surface === "compare" && (
        <div className="relative flex flex-col h-full min-h-0">
          <ExcelCompareView />
        </div>
      )}
      {surface === "word" && (
        <div className="relative flex flex-col h-full min-h-0">
          <WordFullView />
        </div>
      )}
    </div>
  );
}
