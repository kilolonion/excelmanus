"use client";

import { ShieldCheck, ShieldX, ShieldAlert, History, X, ChevronDown, Unlock, Loader2, Check, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { submitApproval, abortChat } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type SubmitPhase = "idle" | "submitting" | "success" | "error";

const RISK_CONFIG = {
  high: {
    label: "高风险",
    icon: ShieldAlert,
    color: "text-red-500",
    bg: "bg-red-500/10",
    border: "border-red-500/20",
    pulse: "bg-red-500",
    gradient: "from-red-500/20 via-red-500/5 to-transparent",
    stripColor: "bg-red-500",
  },
  medium: {
    label: "中风险",
    icon: ShieldAlert,
    color: "text-amber-500",
    bg: "bg-amber-500/10",
    border: "border-amber-500/20",
    pulse: "bg-amber-500",
    gradient: "from-amber-500/20 via-amber-500/5 to-transparent",
    stripColor: "bg-amber-500",
  },
  low: {
    label: "低风险",
    icon: ShieldCheck,
    color: "text-emerald-500",
    bg: "bg-emerald-500/10",
    border: "border-emerald-500/20",
    pulse: "bg-emerald-500",
    gradient: "from-emerald-500/20 via-emerald-500/5 to-transparent",
    stripColor: "bg-emerald-500",
  },
} as const;

const ACTION_LABELS: Record<string, { ing: string; done: string; icon: typeof ShieldCheck }> = {
  accept:     { ing: "执行中…",   done: "已允许执行", icon: ShieldCheck },
  reject:     { ing: "拒绝中…",   done: "已拒绝",     icon: ShieldX },
  fullaccess: { ing: "授权中…",   done: "已全部允许", icon: Unlock },
};

export function ApprovalModal() {
  const pendingApproval = useChatStore((s) => s.pendingApproval);
  const dismissApproval = useChatStore((s) => s.dismissApproval);
  const messages = useChatStore((s) => s.messages);
  const [showHistory, setShowHistory] = useState(false);
  const [phase, setPhase] = useState<SubmitPhase>("idle");
  const [chosenAction, setChosenAction] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const autoDismissTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 从会话消息中提取同工具的历史审批记录（最近 3 条）
  const approvalHistory = useMemo(() => {
    if (!pendingApproval) return [];
    const history: { approvalId: string; toolName: string; success: boolean }[] = [];
    for (const msg of messages) {
      if (msg.role !== "assistant") continue;
      for (const block of msg.blocks) {
        if (
          block.type === "approval_action" &&
          block.toolName === pendingApproval.toolName
        ) {
          history.push({
            approvalId: block.approvalId,
            toolName: block.toolName,
            success: block.success,
          });
        }
      }
    }
    return history.slice(-3);
  }, [messages, pendingApproval]);

  // 清理定时器
  useEffect(() => {
    return () => {
      if (autoDismissTimer.current) clearTimeout(autoDismissTimer.current);
    };
  }, []);

  // 当 pendingApproval 变化时重置状态
  useEffect(() => {
    setPhase("idle");
    setChosenAction(null);
    setErrorMsg(null);
  }, [pendingApproval?.id]);

  const handleAction = useCallback(async (action: "accept" | "reject" | "fullaccess") => {
    if (!pendingApproval || phase === "submitting") return;
    const approvalId = pendingApproval.id;
    const sessionId = useSessionStore.getState().activeSessionId;
    if (!sessionId || !approvalId) return;

    setPhase("submitting");
    setChosenAction(action);
    setErrorMsg(null);

    try {
      await submitApproval(sessionId, approvalId, action);
      setPhase("success");
      // 短暂展示成功状态后自动关闭
      autoDismissTimer.current = setTimeout(() => {
        dismissApproval(approvalId);
      }, 600);
    } catch (err) {
      console.error("[ApprovalModal] submitApproval failed:", err);
      setPhase("error");
      setErrorMsg(err instanceof Error ? err.message : "提交失败，请重试");
    }
  }, [pendingApproval, phase, dismissApproval]);

  const handleDismiss = useCallback(async () => {
    if (!pendingApproval || phase === "submitting") return;
    const sid = useSessionStore.getState().activeSessionId;
    const approvalId = pendingApproval.id;
    if (sid && approvalId) {
      try {
        await submitApproval(sid, approvalId, "reject");
      } catch {
        abortChat(sid).catch(() => {});
      }
    }
    dismissApproval(approvalId);
  }, [pendingApproval, phase, dismissApproval]);

  if (!pendingApproval) return null;

  const riskLevel = pendingApproval.riskLevel || "high";
  const risk = RISK_CONFIG[riskLevel];
  const RiskIcon = risk.icon;
  const argsSummary = pendingApproval.argsSummary || {};
  const argEntries = Object.entries(argsSummary);
  const isBusy = phase === "submitting" || phase === "success";
  const actionMeta = chosenAction ? ACTION_LABELS[chosenAction] : null;

  return (
    <AnimatePresence>
      {/* 半透明背景遮罩 */}
      <motion.div
        key="approval-backdrop"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.2 }}
        className="fixed inset-0 z-[49] bg-black/20 dark:bg-black/40 backdrop-blur-[2px]"
        onClick={handleDismiss}
      />

      {/* 桌面端居中 / 移动端底部弹出 */}
      <motion.div
        key="approval-card"
        initial={{ y: 40, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 30, opacity: 0 }}
        transition={{ type: "spring", damping: 28, stiffness: 320 }}
        className={
          "fixed z-50 pointer-events-none " +
          /* 移动端：底部对齐 */
          "inset-x-0 bottom-0 flex items-end justify-center p-0 " +
          /* 桌面端：居中 */
          "sm:inset-0 sm:items-center sm:justify-center sm:p-4"
        }
      >
        <div className="pointer-events-auto w-full sm:w-[520px] sm:max-w-[92vw]">
        <div
          className={
            "approval-card relative overflow-hidden shadow-2xl " +
            /* 移动端：无顶部圆角，底部安全区 */
            "rounded-t-3xl sm:rounded-3xl"
          }
          style={{ paddingBottom: "max(0px, env(safe-area-inset-bottom, 0px))" }}
        >
          {/* 移动端拖拽指示条 */}
          <div className="flex justify-center pt-2.5 pb-0 sm:hidden">
            <div className="w-10 h-1 rounded-full bg-muted-foreground/20" />
          </div>

          {/* 顶部风险色条 */}
          <div className={`absolute top-0 inset-x-0 h-1.5 ${risk.stripColor} rounded-t-3xl`} />

          {/* 背景光晕 */}
          <div className={`absolute top-0 inset-x-0 h-24 bg-gradient-to-b ${risk.gradient} pointer-events-none`} />

          <div className="relative px-5 pt-5 pb-5 sm:p-6">
            {/* 标题行 */}
            <div className="flex items-start gap-3 sm:gap-4 mb-4 sm:mb-5">
              {/* 风险图标 + 脉冲 */}
              <div className="relative flex-shrink-0 mt-0.5">
                <div className={`absolute inset-0 rounded-full ${risk.pulse} opacity-20 approval-icon-pulse`} />
                <div className={`relative flex items-center justify-center w-10 h-10 sm:w-11 sm:h-11 rounded-full ${risk.bg} ${risk.border} border`}>
                  <RiskIcon className={`h-5 w-5 ${risk.color}`} />
                </div>
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 sm:gap-2.5">
                  <h3 className="font-semibold text-[15px] sm:text-base text-foreground">工具审批请求</h3>
                  <span
                    className={`text-[11px] px-2 sm:px-2.5 py-0.5 rounded-full font-medium ${risk.color} ${risk.bg} ${risk.border} border`}
                  >
                    {risk.label}
                  </span>
                </div>
                <p className="text-sm text-muted-foreground mt-1">
                  即将执行 <code className="font-mono font-medium text-foreground/90 bg-muted/60 px-1.5 py-0.5 rounded text-xs border border-border/40">{pendingApproval.toolName}</code>
                </p>
              </div>

              {/* 关闭按钮 */}
              <button
                onClick={handleDismiss}
                disabled={isBusy}
                className="flex-shrink-0 text-muted-foreground/50 hover:text-foreground transition-colors p-2 sm:p-1.5 -mr-1 rounded-xl hover:bg-muted/80 active:scale-95 disabled:opacity-40"
                title="取消并终止任务"
              >
                <X className="h-5 w-5 sm:h-[18px] sm:w-[18px]" />
              </button>
            </div>

            {/* 参数摘要 — 移动端限高更小 */}
            {argEntries.length > 0 && (
              <div className="mb-4 sm:mb-5 rounded-2xl border border-border/50 bg-muted/20 dark:bg-muted/15 overflow-hidden">
                <div className="px-4 py-2 sm:py-2.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-widest border-b border-border/30 bg-muted/30 dark:bg-muted/20">
                  参数详情
                </div>
                <div className="px-4 py-2.5 sm:py-3 space-y-2 sm:space-y-2.5 max-h-[140px] sm:max-h-[200px] overflow-y-auto overscroll-contain">
                  {argEntries.map(([key, val]) => (
                    <div key={key} className="flex gap-3 items-start text-[13px]">
                      <span className="text-muted-foreground shrink-0 min-w-[4rem] sm:min-w-[5rem] text-right font-medium tabular-nums">{key}</span>
                      <span className="text-border shrink-0 select-none">│</span>
                      <span className="font-mono text-foreground/85 break-all leading-relaxed text-xs">{String(val)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 历史审批记录（可折叠） */}
            {approvalHistory.length > 0 && (
              <div className="mb-3 sm:mb-4">
                <button
                  onClick={() => setShowHistory(!showHistory)}
                  className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors group py-1"
                >
                  <History className="h-3.5 w-3.5" />
                  <span>本会话历史 ({approvalHistory.length})</span>
                  <ChevronDown className={`h-3 w-3 transition-transform duration-200 ${showHistory ? "rotate-180" : ""}`} />
                </button>
                <AnimatePresence>
                  {showHistory && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.2 }}
                      className="overflow-hidden"
                    >
                      <div className="mt-2 space-y-1 pl-5">
                        {approvalHistory.map((h) => (
                          <div
                            key={h.approvalId}
                            className="flex items-center gap-2 text-xs text-muted-foreground"
                          >
                            <span className={`inline-flex items-center gap-1 ${h.success ? "text-emerald-500" : "text-red-500"}`}>
                              <span className={`w-1.5 h-1.5 rounded-full ${h.success ? "bg-emerald-500" : "bg-red-500"}`} />
                              {h.success ? "已执行" : "已拒绝"}
                            </span>
                            <span className="font-mono text-muted-foreground/50 text-[11px]">{h.approvalId.slice(-8)}</span>
                          </div>
                        ))}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            )}

            {/* 分隔线 */}
            <div className="h-px bg-border/40 mb-4 sm:mb-5" />

            {/* 提交状态反馈区 */}
            <AnimatePresence mode="wait">
              {phase === "error" && errorMsg && (
                <motion.div
                  key="error-banner"
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="mb-3 sm:mb-4 overflow-hidden"
                >
                  <div className="flex items-center gap-2 px-3 py-2.5 rounded-xl bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 text-sm">
                    <AlertCircle className="h-4 w-4 flex-shrink-0" />
                    <span className="flex-1 min-w-0 truncate">{errorMsg}</span>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* 操作按钮 — 移动端纵向，桌面端横向 */}
            <div className="flex flex-col sm:flex-row gap-2.5 sm:gap-3">
              <Button
                size="default"
                disabled={isBusy}
                className={
                  "flex-1 gap-2 text-white font-semibold text-sm rounded-xl shadow-md approval-btn-accept transition-all active:scale-[0.97] " +
                  "h-12 sm:h-11 text-[15px] sm:text-sm"
                }
                style={{ backgroundColor: "var(--em-primary)" }}
                onClick={() => handleAction("accept")}
              >
                {phase === "submitting" && chosenAction === "accept" ? (
                  <Loader2 className="h-4.5 w-4.5 animate-spin" />
                ) : phase === "success" && chosenAction === "accept" ? (
                  <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: "spring", damping: 12 }}>
                    <Check className="h-5 w-5" />
                  </motion.div>
                ) : (
                  <ShieldCheck className="h-4.5 w-4.5" />
                )}
                {phase === "success" && chosenAction === "accept" ? "已允许" : "允许执行"}
              </Button>

              <Button
                size="default"
                variant="outline"
                disabled={isBusy}
                className={
                  "flex-1 gap-2 font-semibold text-sm rounded-xl text-red-600 dark:text-red-400 border-red-200 dark:border-red-500/30 hover:bg-red-50 dark:hover:bg-red-500/10 transition-all active:scale-[0.97] " +
                  "h-12 sm:h-11 text-[15px] sm:text-sm"
                }
                onClick={() => handleAction("reject")}
              >
                {phase === "submitting" && chosenAction === "reject" ? (
                  <Loader2 className="h-4.5 w-4.5 animate-spin" />
                ) : phase === "success" && chosenAction === "reject" ? (
                  <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: "spring", damping: 12 }}>
                    <Check className="h-5 w-5" />
                  </motion.div>
                ) : (
                  <ShieldX className="h-4.5 w-4.5" />
                )}
                {phase === "success" && chosenAction === "reject" ? "已拒绝" : "拒绝"}
              </Button>

              <Button
                size="default"
                variant="ghost"
                disabled={isBusy}
                className={
                  "gap-2 font-medium text-sm rounded-xl text-muted-foreground hover:text-foreground transition-all active:scale-[0.97] " +
                  "h-12 sm:h-11 px-4 text-[15px] sm:text-sm"
                }
                onClick={() => handleAction("fullaccess")}
                title="允许本会话所有后续操作"
              >
                {phase === "submitting" && chosenAction === "fullaccess" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : phase === "success" && chosenAction === "fullaccess" ? (
                  <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: "spring", damping: 12 }}>
                    <Check className="h-5 w-5 text-emerald-500" />
                  </motion.div>
                ) : (
                  <Unlock className="h-4 w-4" />
                )}
                {phase === "success" && chosenAction === "fullaccess" ? "已授权" : "全部允许"}
              </Button>
            </div>

            {/* 移动端底部提交中状态提示 */}
            <AnimatePresence>
              {phase === "submitting" && actionMeta && (
                <motion.p
                  key="submitting-hint"
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  className="text-center text-xs text-muted-foreground mt-3 sm:hidden"
                >
                  {actionMeta.ing}
                </motion.p>
              )}
            </AnimatePresence>
          </div>
        </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
