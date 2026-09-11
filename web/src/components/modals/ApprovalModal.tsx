"use client";

import { ShieldCheck, ShieldX, ShieldAlert, History, Unlock, Loader2, Check, AlertCircle } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { submitApproval, abortChat } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  OverlayCard,
  OverlayCardAction,
  OverlayCardBadge,
  OverlayCardBody,
  OverlayCardFooter,
  OverlayCardHeader,
  OverlayCardInset,
  type OverlayTone,
} from "@/components/ui/overlay-card";

type SubmitPhase = "idle" | "submitting" | "success" | "error";

const RISK_TONE: Record<"high" | "medium" | "low", OverlayTone> = {
  high: "danger",
  medium: "warning",
  low: "success",
};

const RISK_LABEL: Record<"high" | "medium" | "low", string> = {
  high: "高风险",
  medium: "中风险",
  low: "低风险",
};

const RISK_ICON = {
  high: ShieldAlert,
  medium: ShieldAlert,
  low: ShieldCheck,
} as const;

const ACTION_LABELS: Record<string, { ing: string; done: string }> = {
  accept: { ing: "执行中…", done: "已允许执行" },
  reject: { ing: "拒绝中…", done: "已拒绝" },
  fullaccess: { ing: "授权中…", done: "已全部允许" },
};

export function ApprovalModal() {
  const pendingApproval = useChatStore((s) => s.pendingApproval);
  if (!pendingApproval) return null;
  return <ApprovalModalInner key={pendingApproval.id} />;
}

function ApprovalModalInner() {
  const pendingApproval = useChatStore((s) => s.pendingApproval);
  const dismissApproval = useChatStore((s) => s.dismissApproval);
  const messages = useChatStore((s) => s.messages);
  const [showHistory, setShowHistory] = useState(false);
  const [phase, setPhase] = useState<SubmitPhase>("idle");
  const [chosenAction, setChosenAction] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const autoDismissTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  useEffect(() => {
    return () => {
      if (autoDismissTimer.current) clearTimeout(autoDismissTimer.current);
    };
  }, []);

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
  const RiskIcon = RISK_ICON[riskLevel];
  const argsSummary = pendingApproval.argsSummary || {};
  const argEntries = Object.entries(argsSummary);
  const isBusy = phase === "submitting" || phase === "success";
  const actionMeta = chosenAction ? ACTION_LABELS[chosenAction] : null;

  const preventWhileBusy = (e: Event) => {
    if (isBusy) e.preventDefault();
  };

  return (
    <OverlayCard
      open
      onOpenChange={(open) => {
        if (!open) void handleDismiss();
      }}
      size="md"
      tone={RISK_TONE[riskLevel]}
      onEscapeKeyDown={preventWhileBusy}
      onPointerDownOutside={preventWhileBusy}
      onInteractOutside={preventWhileBusy}
    >
      <OverlayCardHeader
        icon={<RiskIcon className="h-5 w-5" />}
        pulse
        title="工具审批请求"
        badge={<OverlayCardBadge>{RISK_LABEL[riskLevel]}</OverlayCardBadge>}
        description={
          <>
            即将执行{" "}
            <code className="font-mono font-medium text-foreground/90 bg-muted/60 px-1.5 py-0.5 rounded text-xs border border-border/40">
              {pendingApproval.toolName}
            </code>
          </>
        }
        onClose={() => void handleDismiss()}
        closeDisabled={isBusy}
        closeTitle="取消并终止任务"
      />

      <OverlayCardBody>
        {argEntries.length > 0 && (
          <OverlayCardInset title="参数详情" bodyClassName="space-y-2 sm:space-y-2.5 max-h-[140px] sm:max-h-[200px] overflow-y-auto overscroll-contain">
            {argEntries.map(([key, val]) => (
              <div key={key} className="flex gap-3 items-start text-[13px]">
                <span className="text-muted-foreground shrink-0 min-w-[4rem] sm:min-w-[5rem] text-right font-medium tabular-nums">{key}</span>
                <span className="text-border shrink-0 select-none">│</span>
                <span className="font-mono text-foreground/85 break-all leading-relaxed text-xs">{String(val)}</span>
              </div>
            ))}
          </OverlayCardInset>
        )}

        {approvalHistory.length > 0 && (
          <div className={argEntries.length > 0 ? "mt-3 sm:mt-4" : undefined}>
            <button
              type="button"
              onClick={() => setShowHistory(!showHistory)}
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors py-1"
            >
              <History className="h-3.5 w-3.5" />
              <span>本会话历史 ({approvalHistory.length})</span>
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

        <AnimatePresence mode="wait">
          {phase === "error" && errorMsg && (
            <motion.div
              key="error-banner"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="mt-3 overflow-hidden"
            >
              <div className="flex items-center gap-2 px-3 py-2.5 rounded-xl bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 text-sm">
                <AlertCircle className="h-4 w-4 flex-shrink-0" />
                <span className="flex-1 min-w-0 truncate">{errorMsg}</span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </OverlayCardBody>

      <OverlayCardFooter className="flex-col sm:flex-row sm:justify-stretch">
        <OverlayCardAction
          action="primary"
          disabled={isBusy}
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
        </OverlayCardAction>

        <OverlayCardAction
          action="danger"
          disabled={isBusy}
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
        </OverlayCardAction>

        <OverlayCardAction
          action="ghost"
          disabled={isBusy}
          className="sm:flex-none"
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
        </OverlayCardAction>
      </OverlayCardFooter>

      <AnimatePresence>
        {phase === "submitting" && actionMeta && (
          <motion.p
            key="submitting-hint"
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="text-center text-xs text-muted-foreground pb-3 sm:hidden"
          >
            {actionMeta.ing}
          </motion.p>
        )}
      </AnimatePresence>
    </OverlayCard>
  );
}
