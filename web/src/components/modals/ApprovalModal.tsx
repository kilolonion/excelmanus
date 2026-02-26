"use client";

import { ShieldCheck, ShieldX, ShieldAlert, History, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { submitApproval, abortChat } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import { useMemo, useState } from "react";

const RISK_CONFIG = {
  high: { label: "高风险", color: "text-red-500", bg: "bg-red-500/10", border: "border-red-500/30" },
  medium: { label: "中风险", color: "text-amber-500", bg: "bg-amber-500/10", border: "border-amber-500/30" },
  low: { label: "低风险", color: "text-green-500", bg: "bg-green-500/10", border: "border-green-500/30" },
} as const;

export function ApprovalModal() {
  const pendingApproval = useChatStore((s) => s.pendingApproval);
  const setPendingApproval = useChatStore((s) => s.setPendingApproval);
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
    setPendingApproval(null);
    if (sessionId && approvalId) {
      submitApproval(sessionId, approvalId, action).catch((err) =>
        console.error("[ApprovalModal] submitApproval failed:", err),
      );
    }
  };

  const riskLevel = pendingApproval.riskLevel || "high";
  const risk = RISK_CONFIG[riskLevel];
  const argsSummary = pendingApproval.argsSummary || {};
  const argEntries = Object.entries(argsSummary);

  return (
    <AnimatePresence>
      <motion.div
        initial={{ y: 100, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 100, opacity: 0 }}
        className="fixed bottom-[max(1rem,env(safe-area-inset-bottom))] sm:bottom-24 left-1/2 -translate-x-1/2 z-50 w-[calc(100vw-2rem)] sm:w-[520px] max-w-[90vw]"
      >
        <div className="bg-card border border-border rounded-2xl shadow-lg p-4" style={{ paddingBottom: "max(1rem, var(--sab, 0px))" }}>
          {/* 标题行：风险等级 + 工具名 + 取消按钮 */}
          <div className="flex items-center gap-2 mb-3">
            <ShieldAlert className={`h-5 w-5 ${risk.color}`} />
            <span className="font-semibold text-sm">工具审批请求</span>
            <span
              className={`ml-auto text-xs px-2 py-0.5 rounded-full font-medium ${risk.color} ${risk.bg} ${risk.border} border`}
            >
              {risk.label}
            </span>
            <button
              onClick={() => {
                const sid = useSessionStore.getState().activeSessionId;
                setPendingApproval(null);
                if (sid) abortChat(sid).catch(() => {});
              }}
              className="text-muted-foreground hover:text-foreground transition-colors p-0.5 rounded"
              title="取消并终止任务"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* 工具名 */}
          <p className="text-sm text-muted-foreground mb-2">
            工具: <span className="font-mono font-medium text-foreground">{pendingApproval.toolName}</span>
          </p>

          {/* 参数摘要 */}
          {argEntries.length > 0 && (
            <div className="bg-muted/50 rounded-lg p-2.5 mb-3 text-xs space-y-1">
              {argEntries.map(([key, val]) => (
                <div key={key} className="flex gap-2">
                  <span className="text-muted-foreground shrink-0">{key}:</span>
                  <span className="font-mono text-foreground truncate">{val}</span>
                </div>
              ))}
            </div>
          )}

          {/* 历史审批记录（可折叠） */}
          {approvalHistory.length > 0 && (
            <div className="mb-3">
              <button
                onClick={() => setShowHistory(!showHistory)}
                className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                <History className="h-3 w-3" />
                <span>本会话历史 ({approvalHistory.length})</span>
              </button>
              {showHistory && (
                <div className="mt-1.5 space-y-1">
                  {approvalHistory.map((h) => (
                    <div
                      key={h.approvalId}
                      className="flex items-center gap-2 text-xs text-muted-foreground"
                    >
                      <span className={h.success ? "text-green-500" : "text-red-500"}>
                        {h.success ? "✓ 已执行" : "✗ 已拒绝"}
                      </span>
                      <span className="font-mono truncate">{h.approvalId.slice(-8)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* 操作按钮 */}
          <div className="grid grid-cols-3 gap-2">
            <Button
              size="sm"
              className="gap-1 text-white"
              style={{ backgroundColor: "var(--em-primary)" }}
              onClick={() => handleAction("accept")}
            >
              <ShieldCheck className="h-4 w-4" />
              允许
            </Button>
            <Button
              size="sm"
              variant="destructive"
              className="gap-1"
              onClick={() => handleAction("reject")}
            >
              <ShieldX className="h-4 w-4" />
              拒绝
            </Button>
            <Button
              size="sm"
              variant="secondary"
              className="gap-1"
              onClick={() => handleAction("fullaccess")}
            >
              全部允许
            </Button>
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
