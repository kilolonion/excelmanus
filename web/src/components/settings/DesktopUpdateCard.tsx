"use client";

import { useState } from "react";
import { Download, Loader2, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export function DesktopUpdateCard({ current }: { current: string }) {
  const [update, setUpdate] = useState<ExcelManusDesktopUpdate | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
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
    setBusy(true);
    setError("");
    try {
      await window.excelManusDesktop!.downloadUpdate!();
      setMessage("已在浏览器中开始下载安装包。下载完成后，保存工作并退出 ExcelManus，再运行安装包。");
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
    <p className="text-sm text-muted-foreground">检查新版本并下载安装包。Windows 会识别已有安装并替换旧程序，避免新旧版本并存。</p>
    <div className="flex flex-wrap gap-2">
      {canCheck && <Button variant="outline" size="sm" onClick={() => void check()} disabled={busy}>
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}检查更新
      </Button>}
      {update?.hasUpdate && update.downloadUrl && <Button size="sm" onClick={() => void download()} disabled={busy}>
        <Download className="h-4 w-4" />下载 v{update.latest} 安装包
      </Button>}
      <a href="https://github.com/kilolonion/excelmanus/releases" target="_blank" rel="noopener noreferrer" className="inline-flex items-center text-sm text-[var(--em-primary)] underline underline-offset-4">查看发布页面</a>
    </div>
    {message && <p role="status" className="text-sm">{message}</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {update?.hasUpdate && !update.downloadUrl && <p className="text-sm text-muted-foreground">该版本尚未发布适用于本机的安装包，请稍后重试或查看发布页面。</p>}
    <div className="rounded-md bg-muted/40 p-3 space-y-2 text-xs leading-relaxed">
      <p className="flex items-center gap-1.5 font-medium"><ShieldCheck className="h-4 w-4" />仅处理 ExcelManus 程序，保留你的数据和文件</p>
      <p><strong>迁移数据安装（推荐）</strong>：替换旧程序，继续使用现有设置、会话和快捷方式。数据保留原位，无需移动工作区。</p>
      <p><strong>卸载后安装</strong>：移除旧程序后重新安装，重新创建快捷方式，同样保留设置和会话。</p>
      <p>两种方式都不会删除、移动或修改工作区、表格、文档和其他用户文件。macOS 请将新版应用替换到原有位置。</p>
    </div>
    {update?.hasUpdate && update.releaseNotes && <details className="text-sm">
      <summary className="cursor-pointer">查看更新说明</summary>
      <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words font-sans text-xs text-muted-foreground">{update.releaseNotes}</pre>
    </details>}
  </div>;
}
