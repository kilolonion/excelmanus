"use client";

import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import { AlertCircle, RefreshCw, Wifi, WifiOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  LoadingBrandMark,
  LoadingProgressBar,
  LoadingStatusSpinner,
} from "@/components/ui/loading-visual";
import { useConnectionStore } from "@/stores/connection-store";

/**
 * 全局重启/断连等待遮罩 — 以 portal 形式挂载到 body，覆盖整个应用。
 *
 * 当 connection-store 的 status 为 "restarting" 或 "disconnected" 时渲染。
 * 与 LoadingScreen 共用表格卡片 + Logo 环形进度的等待视觉。
 */
export function GlobalRestartOverlay() {
  const status = useConnectionStore((s) => s.status);
  const restartReason = useConnectionStore((s) => s.restartReason);
  const restartTimeout = useConnectionStore((s) => s.restartTimeout);
  const elapsedSeconds = useConnectionStore((s) => s.elapsedSeconds);
  const phase = useConnectionStore((s) => s.phase);
  const reset = useConnectionStore((s) => s.reset);

  const visible = status === "restarting" || status === "disconnected";

  if (typeof document === "undefined" || !visible) return null;

  const isRestarting = status === "restarting";
  const formatElapsed = (s: number) => {
    if (s < 60) return `${s}s`;
    return `${Math.floor(s / 60)}m ${s % 60}s`;
  };

  const overlay = (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="global-restart-overlay"
          className="fixed inset-0 z-[200] flex h-dvh min-h-[100dvh] flex-col overflow-hidden bg-background select-none"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.3 }}
        >
          <div className="pointer-events-none absolute inset-0">
            <div className="absolute left-1/2 top-[42%] h-[min(72vw,520px)] w-[min(72vw,520px)] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[var(--em-primary)] opacity-[0.045] blur-[90px] md:top-1/2 md:h-[560px] md:w-[560px]" />
          </div>

          <div className="relative z-10 flex flex-1 flex-col items-center justify-center px-6 text-center md:px-16">
            <LoadingBrandMark />

            {restartTimeout ? (
              <TimeoutContent />
            ) : (
              <ActiveContent
                isRestarting={isRestarting}
                phase={phase}
                restartReason={restartReason}
                elapsedSeconds={elapsedSeconds}
                formatElapsed={formatElapsed}
              />
            )}
          </div>

          {restartTimeout && (
            <button
              onClick={reset}
              className="absolute right-4 top-4 rounded-lg p-2 text-xs text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
              title="关闭"
            >
              关闭
            </button>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );

  return createPortal(overlay, document.body);
}

function ActiveContent({
  isRestarting,
  phase,
  restartReason,
  elapsedSeconds,
  formatElapsed,
}: {
  isRestarting: boolean;
  phase: string;
  restartReason: string | null;
  elapsedSeconds: number;
  formatElapsed: (s: number) => string;
}) {
  return (
    <>
      <h1 className="mt-7 flex items-center gap-2 text-[1.5rem] font-semibold tracking-tight text-[var(--em-text)] md:mt-9 md:text-[1.75rem]">
        {isRestarting ? (
          <RefreshCw
            className="size-5"
            style={{ color: "var(--em-primary)" }}
          />
        ) : (
          <WifiOff
            className="size-5"
            style={{ color: "var(--em-primary)" }}
          />
        )}
        {isRestarting ? "服务重启中" : "连接已中断"}
      </h1>

      <motion.p
        key={phase}
        className="mt-2.5 max-w-[22em] text-[14px] leading-relaxed text-[var(--em-text-secondary)] md:text-[15px]"
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {phase}
      </motion.p>

      <div className="mt-8 flex items-center gap-2 text-[13px] text-[var(--em-text-secondary)]">
        <LoadingStatusSpinner />
        <span>{isRestarting ? "正在恢复服务..." : "正在重新连接..."}</span>
      </div>
      <LoadingProgressBar className="mt-3.5 w-[min(70vw,220px)] md:w-[168px]" />

      {restartReason && (
        <div
          className="mt-6 max-w-[280px] rounded-lg px-4 py-2.5 text-xs"
          style={{
            backgroundColor: "var(--em-primary-alpha-06)",
            color: "var(--em-primary)",
          }}
        >
          {restartReason}
        </div>
      )}

      <p className="mt-5 text-[11px] tabular-nums text-muted-foreground/50">
        已等待 {formatElapsed(elapsedSeconds)}
      </p>
      <p className="mt-2 max-w-[28em] pb-[max(1.5rem,env(safe-area-inset-bottom))] text-[11px] leading-relaxed text-muted-foreground/40 md:pb-0">
        请勿关闭页面。停机更新安装依赖和构建前端可能需要几分钟，恢复后将自动刷新。
      </p>
    </>
  );
}

function TimeoutContent() {
  return (
    <>
      <div className="mt-7 flex items-center gap-2 md:mt-9">
        <AlertCircle className="h-5 w-5 text-destructive" />
        <h1 className="text-[1.5rem] font-semibold tracking-tight md:text-[1.75rem]">
          连接恢复超时
        </h1>
      </div>
      <div className="mt-3 space-y-2 text-center">
        <p className="text-sm text-muted-foreground">
          后端未能在预期时间内恢复
        </p>
        <p className="text-xs text-muted-foreground/60">
          请查看升级日志（/tmp/excelmanus-upgrade.log）或手动刷新页面
        </p>
      </div>
      <div className="mt-6 flex gap-3 pb-[max(1.5rem,env(safe-area-inset-bottom))] md:pb-0">
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          onClick={() => window.location.reload()}
        >
          <RefreshCw className="h-3.5 w-3.5" />
          刷新页面
        </Button>
        <Button
          size="sm"
          className="gap-1.5"
          onClick={() => {
            useConnectionStore.getState().reset();
            window.location.reload();
          }}
        >
          <Wifi className="h-3.5 w-3.5" />
          重新连接
        </Button>
      </div>
    </>
  );
}
