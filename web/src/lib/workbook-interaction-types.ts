import { workbookFocusRanges } from "@/lib/workbook-focus";

export interface WorkbookTarget {
  file_path: string;
  workspace_id: string;
  sheet: string;
  ranges: string[];
  content_version: string;
}

export interface WorkbookPresentation {
  kind: "workbook_presentation";
  target: WorkbookTarget;
  stage: "inspect" | "planned" | "changed";
  summary: string;
}

export function parseWorkbookTarget(value: unknown): WorkbookTarget | undefined {
  if (!value || typeof value !== "object") return;
  const target = value as Partial<WorkbookTarget>;
  if (typeof target.file_path !== "string" || !target.file_path
    || typeof target.workspace_id !== "string" || !target.workspace_id
    || typeof target.sheet !== "string" || !target.sheet
    || typeof target.content_version !== "string" || !target.content_version
    || !Array.isArray(target.ranges) || target.ranges.length > 16
    || target.ranges.some((r) => typeof r !== "string" || workbookFocusRanges(r).length !== 1)) return;
  return target as WorkbookTarget;
}

export function parseWorkbookPresentation(result?: string): WorkbookPresentation | undefined {
  try {
    const value = JSON.parse(result || "null");
    if (value?.kind !== "workbook_presentation" || !parseWorkbookTarget(value.target)
      || !value.target.ranges.length || !["inspect", "planned", "changed"].includes(value.stage)
      || typeof value.summary !== "string") return;
    return value;
  } catch { return; }
}
