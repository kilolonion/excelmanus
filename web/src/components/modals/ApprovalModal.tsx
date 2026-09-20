"use client";

import {
  Shield,
  History,
  Loader2,
  Check,
  AlertCircle,
  FileSpreadsheet,
  ChevronRight,
  Info,
} from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { submitApproval } from "@/lib/api";
import { resumeAfterInteraction } from "@/lib/chat-actions";
import { useCallback, useMemo, useRef, useState } from "react";
import {
  OverlayCard,
  OverlayCardAction,
  OverlayCardBadge,
  OverlayCardBody,
  OverlayCardDisclosure,
  OverlayCardFooter,
  OverlayCardHeader,
  OverlayCardInset,
} from "@/components/ui/overlay-card";
import { approvalCopy, extractToolContext, isWriteTool } from "@/lib/tool-labels";

type SubmitPhase = "idle" | "submitting" | "success" | "error";

const ACTION_LABELS: Record<string, { ing: string; done: string; idle: string }> = {
  accept: { idle: "允许本次写入", ing: "提交中…", done: "已允许" },
  reject: { idle: "拒绝本次", ing: "拒绝中…", done: "已拒绝" },
  fullaccess: { idle: "允许本会话全部操作", ing: "授权中…", done: "已全部允许" },
};

function isApprovalGoneError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err ?? "");
  return /不存在或已处理|already.?resolv/i.test(msg);
}

function markLastPendingToolRunning() {
  const chat = useChatStore.getState();
  for (let i = chat.messages.length - 1; i >= 0; i--) {
    const message = chat.messages[i];
    if (message.role !== "assistant") continue;
    for (let j = message.blocks.length - 1; j >= 0; j--) {
      const block = message.blocks[j];
      if (block.type === "tool_call" && block.status === "pending") {
        chat.updateToolCallBlock(message.id, block.toolCallId ?? null, (current) => {
          if (current.type === "tool_call" && current.status === "pending") {
            return { ...current, status: "running" as const };
          }
          return current;
        });
        return;
      }
    }
    break;
  }
}

function finishApprovalLocally(
  approvalId: string,
  sessionId: string | null,
  opts?: { executing?: boolean },
) {
  useChatStore.getState().dismissApproval(approvalId);
  if (sessionId) {
    useSessionStore.getState().patchSession(sessionId, { pendingApproval: false });
  }
  if (opts?.executing) {
    markLastPendingToolRunning();
  }
}

export function ApprovalModal() {
  const pendingApproval = useChatStore((s) => s.pendingApproval);
  if (!pendingApproval) return null;
  return <ApprovalModalInner key={pendingApproval.id} />;
}

function ApprovalModalInner() {
  const pendingApproval = useChatStore((s) => s.pendingApproval);
  const messages = useChatStore((s) => s.messages);
  const [showDetails, setShowDetails] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [phase, setPhase] = useState<SubmitPhase>("idle");
  const [chosenAction, setChosenAction] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const submittingRef = useRef(false);

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

  const closeLocally = useCallback((approvalId: string) => {
    const sessionId = useSessionStore.getState().activeSessionId;
    finishApprovalLocally(approvalId, sessionId);
  }, []);

  const handleAction = useCallback(async (action: "accept" | "reject" | "fullaccess") => {
    if (!pendingApproval || submittingRef.current) return;
    const approvalId = pendingApproval.id;
    const sessionId = useSessionStore.getState().activeSessionId;
    if (!sessionId || !approvalId) return;

    submittingRef.current = true;
    setPhase("submitting");
    setChosenAction(action);
    setErrorMsg(null);

    try {
      const response = await submitApproval(sessionId, approvalId, action);
      setPhase("success");
      finishApprovalLocally(approvalId, sessionId, {
        executing: action === "accept" || action === "fullaccess",
      });
      resumeAfterInteraction(sessionId, response);
    } catch (err) {
      console.error("[ApprovalModal] submitApproval failed:", err);
      if (isApprovalGoneError(err)) {
        finishApprovalLocally(approvalId, sessionId, {
          executing: action === "accept" || action === "fullaccess",
        });
        return;
      }
      setPhase("error");
      setErrorMsg(err instanceof Error ? err.message : "提交失败，请重试");
      submittingRef.current = false;
    }
  }, [pendingApproval]);

  const handleDismiss = useCallback(async () => {
    if (!pendingApproval || submittingRef.current || phase === "success") return;
    const sid = useSessionStore.getState().activeSessionId;
    const approvalId = pendingApproval.id;
    if (phase === "error") {
      closeLocally(approvalId);
      return;
    }
    if (sid && approvalId) {
      try {
        const response = await submitApproval(sid, approvalId, "reject");
        closeLocally(approvalId);
        resumeAfterInteraction(sid, response);
        return;
      } catch (err) {
        if (!isApprovalGoneError(err)) {
          console.error("[ApprovalModal] reject on dismiss failed:", err);
        }
      }
    }
    closeLocally(approvalId);
  }, [pendingApproval, phase, closeLocally]);

  if (!pendingApproval) return null;

  const argsSummary = pendingApproval.argsSummary || {};
  const argEntries = Object.entries(argsSummary);
  const ctx = extractToolContext(pendingApproval.arguments, argsSummary);
  const copy = approvalCopy(pendingApproval.toolName, ctx);
  const writeTool = isWriteTool(pendingApproval.toolName);
  const acceptIdle = writeTool ? "允许本次写入" : "允许本次操作";
  const isBusy = phase === "submitting" || phase === "success";
  const acceptMeta = ACTION_LABELS.accept;
  const canDismiss = phase === "idle" || phase === "error";

  const preventWhileBusy = (e: Event) => {
    if (!canDismiss) e.preventDefault();
  };

  const primaryLabel =
    phase === "submitting" && chosenAction === "accept"
      ? acceptMeta.ing
      : phase === "success" && chosenAction === "accept"
        ? acceptMeta.done
        : phase === "error" && chosenAction === "accept"
          ? "提交失败，请重试"
          : acceptIdle;

  return (
    <OverlayCard
      open
      onOpenChange={(open) => {
        if (!open && canDismiss) void handleDismiss();
      }}
      size="md"
      tone="warning"
      onEscapeKeyDown={preventWhileBusy}
      onPointerDownOutside={preventWhileBusy}
      onInteractOutside={preventWhileBusy}
    >
      <OverlayCardHeader
        icon={<Shield className="h-5 w-5" />}
        eyebrow="执行授权"
        title={copy.title}
        badge={writeTool ? <OverlayCardBadge>将修改文件</OverlayCardBadge> : undefined}
        description={copy.description}
        onClose={canDismiss ? () => void handleDismiss() : undefined}
        closeDisabled={!canDismiss}
        closeTitle="关闭"
      />

      <OverlayCardBody>
        <OverlayCardInset padded={false}>
          <div className="px-4 py-3 flex items-start gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-background border border-[var(--em-hairline)] flex-shrink-0">
              <FileSpreadsheet className="h-4 w-4 text-[var(--em-primary)]" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-foreground truncate">
                {ctx.filename || pendingApproval.toolName}
              </p>
              {(ctx.sheet || ctx.range) && (
                <p className="text-xs text-muted-foreground mt-0.5">
                  {[ctx.sheet, ctx.range].filter(Boolean).join(" · ")}
                </p>
              )}
            </div>
          </div>
          {ctx.cellCount != null && (
            <div className="px-4 py-2.5 border-t border-[var(--em-hairline)] flex items-center justify-between text-sm">
              <span className="text-muted-foreground">修改范围</span>
              <span className="font-medium tabular-nums">{ctx.cellCount} 个单元格</span>
            </div>
          )}
        </OverlayCardInset>

        <div className="mt-1">
          <OverlayCardDisclosure
            icon={<ChevronRight className={`h-3.5 w-3.5 transition-transform ${showDetails ? "rotate-90" : ""}`} />}
            label="查看执行详情"
            open={showDetails}
            onToggle={() => setShowDetails((v) => !v)}
          >
            <div className="rounded-xl border border-[var(--em-hairline)] bg-muted/20 px-3 py-2 space-y-1.5">
              <div className="flex gap-3 text-xs">
                <span className="text-muted-foreground w-16 flex-shrink-0">工具</span>
                <code className="font-mono text-[11px] text-foreground/80">{pendingApproval.toolName}</code>
              </div>
              {argEntries.map(([key, val]) => (
                <div key={key} className="flex gap-3 text-xs">
                  <span className="text-muted-foreground w-16 flex-shrink-0 truncate">{key}</span>
                  <span className="font-mono text-[11px] text-foreground/80 break-all">{String(val)}</span>
                </div>
              ))}
            </div>
          </OverlayCardDisclosure>

          {approvalHistory.length > 0 && (
            <OverlayCardDisclosure
              icon={<History className="h-3.5 w-3.5" />}
              label={`本会话授权记录 · ${approvalHistory.length}`}
              extra={
                <button
                  type="button"
                  className="text-[11px] text-muted-foreground hover:text-foreground"
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleDismiss();
                  }}
                >
                  终止当前任务
                </button>
              }
              open={showHistory}
              onToggle={() => setShowHistory((v) => !v)}
            >
              <div className="space-y-1 pl-6">
                {approvalHistory.map((h) => (
                  <div key={h.approvalId} className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className={h.success ? "text-emerald-600" : "text-red-500"}>
                      {h.success ? "已执行" : "已拒绝"}
                    </span>
                    <span className="font-mono text-[11px] text-muted-foreground/50">
                      {h.approvalId.slice(-8)}
                    </span>
                  </div>
                ))}
              </div>
            </OverlayCardDisclosure>
          )}
        </div>

        {phase === "error" && errorMsg && (
          <div className="mt-3 flex items-start gap-2 px-3 py-2.5 rounded-xl bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 text-sm">
            <AlertCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
            <span className="flex-1 min-w-0">{errorMsg}</span>
          </div>
        )}
      </OverlayCardBody>

      <OverlayCardFooter className="flex-col sm:flex-row-reverse sm:justify-between">
        <OverlayCardAction
          action="primary"
          disabled={isBusy}
          onClick={() => handleAction("accept")}
          className={phase === "error" && chosenAction === "accept" ? "bg-red-600 hover:bg-red-600/90" : undefined}
        >
          {phase === "submitting" && chosenAction === "accept" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : phase === "success" && chosenAction === "accept" ? (
            <Check className="h-4.5 w-4.5" />
          ) : null}
          {primaryLabel}
        </OverlayCardAction>

        <OverlayCardAction
          action="outline"
          disabled={isBusy}
          onClick={() => handleAction("reject")}
        >
          {phase === "submitting" && chosenAction === "reject" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : phase === "success" && chosenAction === "reject" ? (
            <Check className="h-4.5 w-4.5" />
          ) : null}
          {phase === "success" && chosenAction === "reject" ? "已拒绝" : "拒绝本次"}
        </OverlayCardAction>
      </OverlayCardFooter>

      <div className="px-5 sm:px-8 pb-5 -mt-1 text-center sm:text-left">
        <button
          type="button"
          disabled={isBusy}
          onClick={() => handleAction("fullaccess")}
          className="inline-flex items-center gap-1 text-[13px] font-medium text-[var(--em-primary)] hover:underline disabled:opacity-50"
        >
          {phase === "submitting" && chosenAction === "fullaccess" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : phase === "success" && chosenAction === "fullaccess" ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <Info className="h-3.5 w-3.5 text-muted-foreground" />
          )}
          {phase === "success" && chosenAction === "fullaccess" ? "已全部允许" : "允许本会话全部操作"}
        </button>
        <p className="text-[11px] text-muted-foreground mt-0.5">
          将允许本会话的所有后续操作
        </p>
      </div>
    </OverlayCard>
  );
}
