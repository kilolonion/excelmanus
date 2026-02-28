"use client";

import { ShieldCheck, ShieldX, ShieldAlert, History, X, ChevronDown, Unlock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { submitApproval, abortChat } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import { useMemo, useState } from "react";

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

export function ApprovalModal() {
  const pendingApproval = useChatStore((s) => s.pendingApproval);
  const dismissApproval = useChatStore((s) => s.dismissApproval);
  const messages = useChatStore((s) => s.messages);
  const [showHistory, setShowHistory] = useState(false);

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

  if (!pendingApproval) return null;

  const handleAction = (action: "accept" | "reject" | "fullaccess") => {
    const approvalId = pendingApproval.id;
    const sessionId = useSessionStore.getState().activeSessionId;
    dismissApproval(approvalId);
    if (sessionId && approvalId) {
      submitApproval(sessionId, approvalId, action).catch((err) =>
        console.error("[ApprovalModal] submitApproval failed:", err),
      );
    }
  };

  const riskLevel = pendingApproval.riskLevel || "high";
  const risk = RISK_CONFIG[riskLevel];
  const RiskIcon = risk.icon;
  const argsSummary = pendingApproval.argsSummary || {};
  const argEntries = Object.entries(argsSummary);

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
        onClick={() => {
          const sid = useSessionStore.getState().activeSessionId;
          dismissApproval(pendingApproval.id);
          if (sid) abortChat(sid).catch(() => {});
        }}
      />

      <motion.div
        key="approval-card"
        initial={{ y: 30, opacity: 0, scale: 0.92 }}
        animate={{ y: 0, opacity: 1, scale: 1 }}
        exit={{ y: 20, opacity: 0, scale: 0.95 }}
        transition={{ type: "spring", damping: 26, stiffness: 340 }}
        className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none"
      >
        <div className="pointer-events-auto w-full sm:w-[520px] max-w-[92vw]">
        <div className="approval-card relative overflow-hidden rounded-3xl shadow-2xl" style={{ paddingBottom: "max(0px, var(--sab, 0px))" }}>
          {/* 顶部风险色条 */}
          <div className={`absolute top-0 inset-x-0 h-1.5 ${risk.stripColor} rounded-t-3xl`} />

          {/* 背景光晕 */}
          <div className={`absolute top-0 inset-x-0 h-24 bg-gradient-to-b ${risk.gradient} pointer-events-none`} />

          <div className="relative p-6">
            {/* 标题行 */}
            <div className="flex items-start gap-4 mb-5">
              {/* 风险图标 + 脉冲 */}
              <div className="relative flex-shrink-0 mt-0.5">
                <div className={`absolute inset-0 rounded-full ${risk.pulse} opacity-20 approval-icon-pulse`} />
                <div className={`relative flex items-center justify-center w-11 h-11 rounded-full ${risk.bg} ${risk.border} border`}>
                  <RiskIcon className={`h-5 w-5 ${risk.color}`} />
                </div>
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2.5">
                  <h3 className="font-semibold text-base text-foreground">工具审批请求</h3>
                  <span
                    className={`text-[11px] px-2.5 py-0.5 rounded-full font-medium ${risk.color} ${risk.bg} ${risk.border} border`}
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
                onClick={() => {
                  const sid = useSessionStore.getState().activeSessionId;
                  dismissApproval(pendingApproval.id);
                  if (sid) abortChat(sid).catch(() => {});
                }}
                className="flex-shrink-0 text-muted-foreground/50 hover:text-foreground transition-colors p-1.5 rounded-xl hover:bg-muted/80 touch-compact"
                title="取消并终止任务"
              >
                <X className="h-4.5 w-4.5" />
              </button>
            </div>

            {/* 参数摘要 */}
            {argEntries.length > 0 && (
              <div className="mb-5 rounded-2xl border border-border/50 bg-muted/20 dark:bg-muted/15 overflow-hidden">
                <div className="px-4 py-2.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-widest border-b border-border/30 bg-muted/30 dark:bg-muted/20">
                  参数详情
                </div>
                <div className="px-4 py-3 space-y-2.5 max-h-[200px] overflow-y-auto">
                  {argEntries.map(([key, val]) => (
                    <div key={key} className="flex gap-3 items-start text-[13px]">
                      <span className="text-muted-foreground shrink-0 min-w-[5rem] text-right font-medium tabular-nums">{key}</span>
                      <span className="text-border shrink-0 select-none">│</span>
                      <span className="font-mono text-foreground/85 break-all leading-relaxed text-xs">{String(val)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 历史审批记录（可折叠） */}
            {approvalHistory.length > 0 && (
              <div className="mb-4">
                <button
                  onClick={() => setShowHistory(!showHistory)}
                  className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors group"
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
            <div className="h-px bg-border/40 mb-5" />

            {/* 操作按钮 */}
            <div className="flex gap-3">
              <Button
                size="default"
                className="flex-1 h-11 gap-2 text-white font-semibold text-sm rounded-xl shadow-md approval-btn-accept hover:shadow-lg transition-all"
                style={{ backgroundColor: "var(--em-primary)" }}
                onClick={() => handleAction("accept")}
              >
                <ShieldCheck className="h-4.5 w-4.5" />
                允许执行
              </Button>
              <Button
                size="default"
                variant="outline"
                className="flex-1 h-11 gap-2 font-semibold text-sm rounded-xl text-red-600 dark:text-red-400 border-red-200 dark:border-red-500/30 hover:bg-red-50 dark:hover:bg-red-500/10 transition-all"
                onClick={() => handleAction("reject")}
              >
                <ShieldX className="h-4.5 w-4.5" />
                拒绝
              </Button>
              <Button
                size="default"
                variant="ghost"
                className="h-11 gap-2 font-medium text-sm rounded-xl text-muted-foreground hover:text-foreground px-4 transition-all"
                onClick={() => handleAction("fullaccess")}
                title="允许本会话所有后续操作"
              >
                <Unlock className="h-4 w-4" />
                全部允许
              </Button>
            </div>
          </div>
        </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
