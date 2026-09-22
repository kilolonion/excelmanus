"use client";

import { RefreshCw, X, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

interface VersionUpdateToastProps {
  /** 新版本可用（软提示） */
  newVersionAvailable: boolean;
  /** API schema 不兼容（提示保存后刷新） */
  apiIncompatible: boolean;
  /** 远端后端版本号 */
  remoteVersion: string | null;
  /** 关闭提示 */
  onDismiss: () => void;
  /** 立即刷新 */
  onRefresh: () => void;
  refreshError?: string | null;
}

/**
 * 版本更新提示 toast — 固定在页面右下角。
 *
 * - 新版本可用：可关闭的柔性提示
 * - API 不兼容：提示用户保存后刷新，不强制打断编辑
 */
export function VersionUpdateToast({
  newVersionAvailable,
  apiIncompatible,
  remoteVersion,
  onDismiss,
  onRefresh,
  refreshError,
}: VersionUpdateToastProps) {
  if (!newVersionAvailable && !apiIncompatible) return null;

  if (apiIncompatible) {
    return (
      <div className="em-update-toast fixed z-9999 animate-in slide-in-from-bottom-4 fade-in duration-300">
        <div className="em-update-card flex items-start gap-3 border border-destructive/30 bg-red-50/80 p-4 backdrop-blur-xl dark:bg-red-950/70">
          <AlertTriangle className="h-5 w-5 text-destructive shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-destructive">
              版本不兼容
            </p>
            <p className="text-xs text-destructive/80 mt-1">
              后端已更新{remoteVersion ? ` (v${remoteVersion})` : ""}，
              当前页面版本不兼容。请保存当前工作后刷新页面。
            </p>
            <div className="flex items-center gap-2 mt-2.5">
              <Button
                variant="destructive"
                size="sm"
                className="h-7 text-xs gap-1.5"
                onClick={onRefresh}
              >
                <RefreshCw className="h-3 w-3" />
                立即刷新
              </Button>
            </div>
            {refreshError && <p role="alert" className="mt-2 text-xs text-destructive">{refreshError}</p>}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="em-update-toast fixed z-9999 animate-in slide-in-from-bottom-4 fade-in duration-300">
      <div className="em-update-card flex items-start gap-3 border p-4">
        <RefreshCw
          className="h-5 w-5 shrink-0 mt-0.5"
          style={{ color: "var(--em-primary)" }}
        />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium">
            新版本已就绪
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            网页或服务已更新{remoteVersion ? `（服务 v${remoteVersion}）` : ""}。
            保存当前工作后刷新即可使用新版，工作区和用户文件保留原位。
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
          {refreshError && <p role="alert" className="mt-2 text-xs text-destructive">{refreshError}</p>}
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
