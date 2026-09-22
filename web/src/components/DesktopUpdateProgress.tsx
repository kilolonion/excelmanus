"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useDesktopUpdate } from "@/hooks/use-desktop-update";
import { appRefreshBlocker } from "@/lib/app-refresh";
import { Button } from "@/components/ui/button";

function bytes(value: number) {
  return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MB` : `${(value / 1024).toFixed(1)} KB`;
}

// Lives outside Settings so downloads/menu updates survive navigation, and a
// reopened renderer restores the actual main-process state instead of 0%.
export function DesktopUpdateProgress() {
  const status = useDesktopUpdate();
  const [error, setError] = useState("");
  const [dismissed, setDismissed] = useState<number | null>(null);
  useEffect(() => {
    if (!window.excelManusDesktop) return;
    const protectWork = (event: BeforeUnloadEvent) => {
      if (appRefreshBlocker()) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", protectWork);
    return () => window.removeEventListener("beforeunload", protectWork);
  }, []);
  if (!status || status.phase === "idle" || dismissed === status.revision) return null;

  const downloading = status.phase === "downloading";
  const retryable = status.phase === "error" || status.phase === "cancelled";
  const title = downloading ? "正在下载更新" : status.phase === "verifying" ? "正在校验安装包"
    : status.phase === "installing" ? "正在退出并打开安装包" : status.phase === "ready" ? "安装包已就绪"
      : status.phase === "cancelled" ? "下载已取消" : "更新未完成";
  const action = async (kind: "cancel" | "retry" | "install") => {
    setError("");
    if (kind !== "cancel") {
      const blocked = appRefreshBlocker();
      if (blocked) { setError(blocked); return; }
    }
    try {
      const bridge = window.excelManusDesktop!;
      if (kind === "cancel") await bridge.cancelUpdate?.();
      else if (kind === "retry") await bridge.downloadUpdate?.();
      else await bridge.installUpdate?.();
    } catch (err) { setError(err instanceof Error ? err.message : "更新失败，请重试"); }
  };
  return createPortal(<section aria-label="桌面更新进度" className="fixed bottom-4 right-4 z-[250] w-[min(24rem,calc(100vw-2rem)) rounded-lg border bg-background p-4 shadow-xl space-y-3">
    <p role="status" className="text-sm font-semibold">{title}{status.latest ? ` · v${status.latest}` : ""}</p>
    {downloading && <>
      <progress aria-label="安装包下载进度" className="w-full h-2" max={100} value={status.percent ?? undefined} />
      <p className="text-xs tabular-nums text-muted-foreground">
        {status.percent === null ? "大小未知" : `${status.percent.toFixed(1)}%`} · {bytes(status.received)}
        {status.total ? ` / ${bytes(status.total)}` : ""} · {bytes(status.bytesPerSecond)}/s
      </p>
      <p className="text-xs text-muted-foreground">下载校验完成后自动退出并打开安装包；有未保存修改或运行任务时暂停退出。</p>
    </>}
    {(error || status.error) && <p role="alert" className="text-sm text-destructive">{error || status.error}</p>}
    <div className="flex gap-2">
      {downloading && <Button size="sm" variant="outline" onClick={() => void action("cancel")}>取消下载</Button>}
      {retryable && <Button size="sm" onClick={() => void action("retry")}>重新下载</Button>}
      {status.phase === "ready" && <Button size="sm" onClick={() => void action("install")}>退出并更新</Button>}
      {(retryable || status.phase === "ready") && <Button size="sm" variant="outline" onClick={() => setDismissed(status.revision)}>稍后</Button>}
    </div>
  </section>, document.body);
}
