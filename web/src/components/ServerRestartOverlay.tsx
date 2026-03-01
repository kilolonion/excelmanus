"use client";

import { Loader2, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";

interface ServerRestartOverlayProps {
  restarting: boolean;
  restartTimeout: boolean;
  /** 重启原因提示文字（可选） */
  reason?: string;
}

/**
 * 可复用的后端重启等待遮罩。
 *
 * 当 `restarting` 为 true 时渲染加载动画；
 * 当 `restartTimeout` 为 true 时渲染超时提示。
 * 当 `restarting` 为 false 时返回 null（不渲染）。
 */
export function ServerRestartOverlay({
  restarting,
  restartTimeout,
  reason,
}: ServerRestartOverlayProps) {
  if (!restarting) return null;

  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4">
      {restartTimeout ? (
        <>
          <AlertCircle className="h-8 w-8 text-destructive" />
          <div className="text-center space-y-1">
            <p className="text-sm font-medium">重启超时</p>
            <p className="text-xs text-muted-foreground">
              后端未能在预期时间内恢复，请检查服务日志
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => window.location.reload()}
          >
            手动刷新页面
          </Button>
        </>
      ) : (
        <>
          <Loader2
            className="h-8 w-8 animate-spin"
            style={{ color: "var(--em-primary)" }}
          />
          <div className="text-center space-y-1">
            <p className="text-sm font-medium">服务正在重启…</p>
            <p className="text-xs text-muted-foreground">
              {reason || "配置已更新，正在等待后端就绪，请勿关闭页面"}
            </p>
          </div>
        </>
      )}
    </div>
  );
}
