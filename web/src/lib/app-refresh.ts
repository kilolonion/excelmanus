import { hasUnsavedWorkbookEdits } from "@/lib/excel-cell-edit";
import { useSessionStore } from "@/stores/session-store";

export function appRefreshBlocker(): string | null {
  if (hasUnsavedWorkbookEdits()) return "表格还有未保存或保存失败的修改，请先保存或处理冲突后再更新。";
  if (useSessionStore.getState().sessions.some(session => session.inFlight)) return "还有任务正在运行，请等待任务完成后再更新。";
  return null;
}

export function refreshApp(): string | null {
  const blocked = appRefreshBlocker();
  if (!blocked && typeof window !== "undefined") window.location.reload();
  return blocked;
}
