"use client";

import { RefreshCw, X, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

interface VersionUpdateToastProps {
  /** 新版本可用（软提示） */
  newVersionAvailable: boolean;
  /** API schema 不兼容（强制刷新） */
  apiIncompatible: boolean;
  /** 远端后端版本号 */
  remoteVersion: string | null;
  /** 关闭提示 */
  onDismiss: () => void;
  /** 立即刷新 */
  onRefresh: () => void;
}

/**
 * 版本更新提示 toast — 固定在页面右下角。
 *
 * - 新版本可用：可关闭的柔性提示
 * - API 不兼容：不可关闭的强制提示，3 秒后自动刷新
 */
export function VersionUpdateToast({
  newVersionAvailable,
  apiIncompatible,
  remoteVersion,
  onDismiss,
  onRefresh,
}: VersionUpdateToastProps) {
  if (!newVersionAvailable && !apiIncompatible) return null;

  if (apiIncompatible) {
    return (
      <div className="fixed bottom-4 right-4 z-9999 max-w-sm animate-in slide-in-from-bottom-4 fade-in duration-300">
        <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/10 p-4 shadow-lg backdrop-blur-sm">
          <AlertTriangle className="h-5 w-5 text-destructive shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-destructive">
              版本不兼容
            </p>
            <p className="text-xs text-destructive/80 mt-1">
              后端已更新{remoteVersion ? ` (v${remoteVersion})` : ""}，
              当前页面版本不兼容，即将自动刷新…
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed bottom-4 right-4 z-9999 max-w-sm animate-in slide-in-from-bottom-4 fade-in duration-300">
      <div className="flex items-start gap-3 rounded-lg border border-border bg-background p-4 shadow-lg">
        <RefreshCw
          className="h-5 w-5 shrink-0 mt-0.5"
          style={{ color: "var(--em-primary)" }}
        />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium">
            新版本已就绪
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            后端已更新{remoteVersion ? ` 到 v${remoteVersion}` : ""}，
            刷新页面以获取最新体验。
          </p>
          <div className="flex items-center gap-2 mt-2.5">
            <Button
              variant="default"
              size="sm"
              className="h-7 text-xs gap-1.5"
              onClick={onRefresh}
            >
              <RefreshCw className="h-3 w-3" />
              立即刷新
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs text-muted-foreground"
              onClick={onDismiss}
            >
              稍后
            </Button>
          </div>
        </div>
        <button
          className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
          onClick={onDismiss}
          aria-label="关闭"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
