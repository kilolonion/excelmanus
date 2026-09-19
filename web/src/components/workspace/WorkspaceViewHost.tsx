"use client";

import { useEffect, useState, type ReactNode } from "react";
import { ExcelCompareView } from "@/components/excel/ExcelCompareView";
import { ExcelFullView } from "@/components/excel/ExcelFullView";
import { WordFullView } from "@/components/word/WordFullView";
import { useExcelStore } from "@/stores/excel-store";
import { useWordStore } from "@/stores/word-store";
import {
  resolveWorkspaceSurface,
  workspaceKeepAliveLayerClass,
} from "@/lib/workspace-surface";

export function WorkspaceViewHost({ children }: { children: ReactNode }) {
  const fullViewPath = useExcelStore((s) => s.fullViewPath);
  const compareMode = useExcelStore((s) => s.compareMode);
  const wordFullViewPath = useWordStore((s) => s.fullViewPath);
  const [excelMounted, setExcelMounted] = useState(() => !!fullViewPath);

  useEffect(() => {
    if (fullViewPath) setExcelMounted(true);
  }, [fullViewPath]);

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
