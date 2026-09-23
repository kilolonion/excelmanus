"use client";

import { useId, useState } from "react";
import { ChevronDown, Download, Loader2, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useDesktopUpdate } from "@/hooks/use-desktop-update";
import { appRefreshBlocker } from "@/lib/app-refresh";

export function DesktopUpdateCard({ current }: { current: string }) {
  const [update, setUpdate] = useState<ExcelManusDesktopUpdate | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [preservationInfoOpen, setPreservationInfoOpen] = useState(true);
  const preservationInfoId = useId();
  const status = useDesktopUpdate();
  const active = busy || ["downloading", "verifying", "installing"].includes(status?.phase || "");
  const canCheck = typeof window !== "undefined" && !!window.excelManusDesktop?.checkUpdate;

  async function check() {
    setBusy(true);
    setError("");
    setMessage("");
    setUpdate(null);
    try {
      const info = await window.excelManusDesktop!.checkUpdate!();
      setUpdate(info);
      setMessage(info.hasUpdate ? `发现新版本 v${info.latest}` : "当前已是最新正式版本");
    } catch (err) {
      setError(err instanceof Error ? err.message : "检查更新失败，请稍后重试");
    } finally { setBusy(false); }
  }

  async function download() {
    const blocked = appRefreshBlocker();
    if (blocked) { setError(blocked); return; }
    setBusy(true);
    setError("");
    try {
      await window.excelManusDesktop!.downloadUpdate!();
      if (!window.excelManusDesktop?.getUpdateStatus) {
        setMessage("旧版桌面壳已打开浏览器下载。下载完成后，保存工作并退出 ExcelManus，再运行安装包；升级后支持应用内进度。");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法打开下载，请重试");
    } finally { setBusy(false); }
  }

  return <div className="rounded-lg border border-border p-4 space-y-4">
    <div className="flex flex-wrap items-center gap-2 text-sm font-semibold">
      <Sparkles className="h-5 w-5 text-[var(--em-primary)]" />
      ExcelManus Desktop
      <Badge variant="secondary">v{update?.current || current}</Badge>
    </div>
    <p className="text-sm text-muted-foreground">检查新版本并下载安装包，页面实时显示进度。下载校验完成后自动退出并打开安装包，请先保存工作。Windows 会识别已有安装并替换旧程序。</p>
    <div className="flex flex-wrap gap-2">
      {canCheck && <Button variant="outline" size="sm" onClick={() => void check()} disabled={active}>
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}检查更新
      </Button>}
      {update?.hasUpdate && update.downloadUrl && <Button size="sm" onClick={() => void download()} disabled={active}>
        <Download className="h-4 w-4" />下载 v{update.latest} 安装包
      </Button>}
      <a href="https://github.com/kilolonion/excelmanus/releases" target="_blank" rel="noopener noreferrer" className="inline-flex items-center text-sm text-[var(--em-primary)] underline underline-offset-4">查看发布页面</a>
    </div>
    {message && <p role="status" className="text-sm">{message}</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {update?.hasUpdate && !update.downloadUrl && <p className="text-sm text-muted-foreground">该版本尚未发布适用于本机的安装包，请稍后重试或查看发布页面。</p>}
    <div className="rounded-md bg-muted/40 p-3 text-xs leading-relaxed">
      <button
        type="button"
        aria-expanded={preservationInfoOpen}
        aria-controls={preservationInfoId}
        onClick={() => setPreservationInfoOpen((open) => !open)}
        className="flex w-full items-center gap-1.5 text-left font-medium hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-muted"
      >
        <ShieldCheck className="h-4 w-4 shrink-0" />
        <span>仅处理 ExcelManus 程序，保留你的数据和文件</span>
        <ChevronDown className={`ml-auto h-4 w-4 shrink-0 text-muted-foreground transition-transform ${preservationInfoOpen ? "rotate-180" : ""}`} aria-hidden="true" />
      </button>
      <div id={preservationInfoId} hidden={!preservationInfoOpen} className="space-y-2 pt-2">
        <p><strong>迁移数据安装（推荐）</strong>：替换旧程序，继续使用现有设置、会话和快捷方式。数据保留原位，无需移动工作区。</p>
        <p><strong>卸载后安装</strong>：移除旧程序后重新安装，重新创建快捷方式，同样保留设置和会话。</p>
        <p>两种方式都不会删除、移动或修改工作区、表格、文档和其他用户文件。macOS 会打开已下载的 DMG，请在 Finder 中将新版应用替换到原有位置。系统权限或安全确认仍需你操作。</p>
      </div>
    </div>
    {update?.hasUpdate && update.releaseNotes && <details className="text-sm">
      <summary className="cursor-pointer">查看更新说明</summary>
      <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words font-sans text-xs text-muted-foreground">{update.releaseNotes}</pre>
    </details>}
  </div>;
}
