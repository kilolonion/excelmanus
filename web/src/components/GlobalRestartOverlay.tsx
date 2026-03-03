"use client";

import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import { AlertCircle, RefreshCw, Wifi, WifiOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useConnectionStore } from "@/stores/connection-store";

/**
 * 全局重启/断连等待遮罩 — 以 portal 形式挂载到 body，覆盖整个应用。
 *
 * 当 connection-store 的 status 为 "restarting" 或 "disconnected" 时渲染。
 * 复用 LoadingScreen 的设计语言（品牌 logo + 光环 + 进度条 + 浮动光点）。
 */
export function GlobalRestartOverlay() {
  const status = useConnectionStore((s) => s.status);
  const restartReason = useConnectionStore((s) => s.restartReason);
  const restartTimeout = useConnectionStore((s) => s.restartTimeout);
  const elapsedSeconds = useConnectionStore((s) => s.elapsedSeconds);
  const phase = useConnectionStore((s) => s.phase);
  const reset = useConnectionStore((s) => s.reset);

  const visible = status === "restarting" || status === "disconnected";

  // Portal 需要在客户端渲染，SSR 阶段 document 不可用
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
          className="fixed inset-0 z-[200] flex flex-col items-center justify-center bg-background select-none"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.3 }}
        >
          {/* ── 背景装饰层 ── */}
          <div className="absolute inset-0 pointer-events-none overflow-hidden">
            <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] rounded-full bg-[var(--em-primary)] opacity-[0.04] blur-[120px] loading-bg-breathe" />
            <div className="absolute top-[40%] left-[55%] -translate-x-1/2 -translate-y-1/2 w-[300px] h-[300px] rounded-full bg-[var(--em-primary-light)] opacity-[0.03] blur-[80px] loading-bg-breathe-delayed" />
            <div className="loading-float-particle absolute top-[30%] left-[20%] w-1 h-1 rounded-full bg-[var(--em-primary)] opacity-20" />
            <div className="loading-float-particle-delayed absolute top-[60%] left-[75%] w-1.5 h-1.5 rounded-full bg-[var(--em-primary-light)] opacity-15" />
            <div className="loading-float-particle-slow absolute top-[45%] left-[85%] w-1 h-1 rounded-full bg-[var(--em-primary)] opacity-10" />
          </div>

          {/* ── 中心内容 ── */}
          <div className="relative flex flex-col items-center gap-6 px-6 max-w-md">
            {/* Logo + 光环 */}
            <div className="relative flex items-center justify-center">
              <div className="absolute w-28 h-28 rounded-full border border-[var(--em-primary-alpha-06)] loading-ring-outer" />
              <div className="absolute w-24 h-24 rounded-full border border-dashed border-[var(--em-primary-alpha-15)] loading-ring-spin" />
              <div className="absolute w-20 h-20 rounded-full border border-[var(--em-primary-alpha-10)] loading-ring-inner" />

              <div className="loading-logo-pulse relative z-10">
                <div className="w-14 h-14 rounded-2xl overflow-hidden shadow-lg shadow-[var(--em-primary-alpha-20)] ring-1 ring-[var(--em-primary-alpha-10)]">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src="/icon.png"
                    alt="ExcelManus"
                    width={56}
                    height={56}
                    className="w-full h-full object-cover"
                  />
                </div>
                <div className="absolute inset-0 rounded-2xl loading-ring-pulse" />
              </div>
            </div>

            {/* 状态标题 */}
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

          {/* ── 底部进度条 ── */}
          {!restartTimeout && (
            <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-[var(--em-primary-alpha-06)] overflow-hidden">
              <div className="h-full w-1/4 loading-progress-bar rounded-full bg-gradient-to-r from-transparent via-[var(--em-primary)] to-transparent opacity-80" />
            </div>
          )}

          {/* 右上角关闭按钮（仅超时时显示） */}
          {restartTimeout && (
            <button
              onClick={reset}
              className="absolute top-4 right-4 text-muted-foreground hover:text-foreground transition-colors p-2 rounded-lg hover:bg-muted/50"
              title="关闭"
            >
              <span className="text-xs">关闭</span>
            </button>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );

  return createPortal(overlay, document.body);
}

/** 活跃状态（正在重启/正在重连） */
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
      {/* 状态图标 */}
      <div className="flex items-center gap-2">
        {isRestarting ? (
          <RefreshCw
            className="h-4 w-4 animate-spin"
            style={{ color: "var(--em-primary)" }}
          />
        ) : (
          <WifiOff
            className="h-4 w-4"
            style={{ color: "var(--em-primary)" }}
          />
        )}
        <span className="text-base font-semibold text-foreground/80">
          {isRestarting ? "服务重启中" : "连接已中断"}
        </span>
      </div>

      {/* 阶段描述 */}
      <div className="flex flex-col items-center gap-2">
        <motion.p
          key={phase}
          className="text-sm text-muted-foreground text-center"
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          {phase}
        </motion.p>

        {/* 三点加载指示器 */}
        <div className="flex items-center gap-1.5">
          <div className="w-1.5 h-1.5 rounded-full bg-[var(--em-primary)] loading-dot-bounce" style={{ animationDelay: "0ms" }} />
          <div className="w-1.5 h-1.5 rounded-full bg-[var(--em-primary)] loading-dot-bounce" style={{ animationDelay: "160ms" }} />
          <div className="w-1.5 h-1.5 rounded-full bg-[var(--em-primary)] loading-dot-bounce" style={{ animationDelay: "320ms" }} />
        </div>
      </div>

      {/* 重启原因 */}
      {restartReason && (
        <div
          className="rounded-lg px-4 py-2.5 text-xs text-center max-w-[280px]"
          style={{
            backgroundColor: "var(--em-primary-alpha-06)",
            color: "var(--em-primary)",
          }}
        >
          {restartReason}
        </div>
      )}

      {/* 已等待时长 */}
      <p className="text-[11px] text-muted-foreground/50 tabular-nums">
        已等待 {formatElapsed(elapsedSeconds)}
      </p>

      {/* 温馨提示 */}
      <p className="text-[11px] text-muted-foreground/40 text-center leading-relaxed">
        请勿关闭页面，连接恢复后将自动刷新
      </p>
    </>
  );
}

/** 超时内容 */
function TimeoutContent() {
  return (
    <>
      <div className="flex items-center gap-2">
        <AlertCircle className="h-5 w-5 text-destructive" />
        <span className="text-base font-semibold">连接恢复超时</span>
      </div>
      <div className="text-center space-y-2">
        <p className="text-sm text-muted-foreground">
          后端未能在预期时间内恢复
        </p>
        <p className="text-xs text-muted-foreground/60">
          请检查服务日志确认后端状态，或尝试手动刷新页面
        </p>
      </div>
      <div className="flex gap-3">
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
