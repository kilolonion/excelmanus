"use client";

import { LocateFixed } from "lucide-react";
import { parseWorkbookPresentation, showWorkbookPresentation } from "@/lib/workbook-interaction";
import { useSessionStore } from "@/stores/session-store";
import { useState } from "react";

export function WorkbookPresentationCard({ result }: { result?: string }) {
  const presentation = parseWorkbookPresentation(result);
  const [error, setError] = useState("");
  if (!presentation) return null;
  const label = presentation.stage === "planned" ? "准备修改" : presentation.stage === "changed" ? "已修改" : "定位查看";
  return <div className="my-2 rounded-xl border border-border bg-background px-3 py-3 space-y-2">
    <p className="text-sm font-medium flex items-center gap-2"><LocateFixed className="h-4 w-4" />{label}</p>
    <p className="text-sm text-muted-foreground">{presentation.summary}</p>
    <button type="button" className="text-xs text-left underline underline-offset-4 break-all" style={{ color: "var(--em-primary)" }} onClick={() => {
      const sid = useSessionStore.getState().activeSessionId;
      setError(sid && showWorkbookPresentation(presentation, sid) ? "" : "请先完成当前选区，或返回此文件所在的工作区");
    }}>{presentation.target.file_path} · {presentation.target.sheet}!{presentation.target.ranges.join("、")}</button>
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
  </div>;
}
