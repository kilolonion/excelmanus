"use client";

import React, { useCallback, useState } from "react";
import {
  Check,
  Copy,
  CreditCard,
  HardDrive,
  Play,
  Settings,
  ShieldAlert,
  Wifi,
  XCircle,
} from "lucide-react";
import { useUIStore } from "@/stores/ui-store";
import { useSessionStore } from "@/stores/session-store";
import { RetryModelPicker } from "@/components/chat/RetryModelPicker";
import { requestModelSubTab } from "@/components/settings/model/model-subtab";
import {
  canOfferRetry,
  displayFailureMessage,
  displayFailureTitle,
  FAILURE_STAGE_LABELS,
  resolveFailureActions,
} from "@/lib/failure-recovery";

const CATEGORY_ICON = {
  model: ShieldAlert,
  transport: Wifi,
  quota: CreditCard,
  config: HardDrive,
  unknown: XCircle,
} as const;

interface FailureGuidanceCardProps {
  category: "model" | "transport" | "config" | "quota" | "unknown";
  code: string;
  title: string;
  message: string;
  stage: string;
  retryable: boolean;
  diagnosticId: string;
  actions: { type: "retry" | "open_settings" | "copy_diagnostic"; label: string }[];
  provider?: string;
  model?: string;
  onRetryWithModel?: (modelName: string) => void;
}

const GHOST_BTN =
  "touch-compact inline-flex h-9 sm:h-8 items-center justify-center gap-1.5 rounded-lg px-2.5 text-[12px] font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50";

const PRIMARY_BTN =
  "touch-compact inline-flex h-9 sm:h-8 w-full sm:w-auto items-center justify-center gap-1.5 rounded-lg px-3 text-[13px] font-semibold text-white bg-[var(--em-primary)] hover:opacity-90";

export function FailureGuidanceCard({
  category,
  code,
  title,
  message,
  stage,
  retryable,
  diagnosticId,
  provider,
  model,
  onRetryWithModel,
}: FailureGuidanceCardProps) {
  const openSettings = useUIStore((s) => s.openSettings);
  const [copied, setCopied] = useState(false);
  const Icon = CATEGORY_ICON[category] || XCircle;
  const offerRetry = canOfferRetry(code);
  const resolvedActions = resolveFailureActions({ code, retryable });
  const shownTitle = displayFailureTitle(code, title);
  const shownMessage = displayFailureMessage(message);
  const stageLabel = FAILURE_STAGE_LABELS[stage] || stage;
  const meta = [provider && model ? `${provider} / ${model}` : provider || model, stageLabel]
    .filter(Boolean)
    .join(" · ");

  const handleAction = useCallback(
    (actionType: string) => {
      switch (actionType) {
        case "retry": {
          // 「继续」：后台发送隐藏的 continue 接续失败回合，不产生用户气泡，
          // 也不走回滚重发（continue 不回退任何文件变更）。
          const sessionId = useSessionStore.getState().activeSessionId;
          void import("@/lib/chat-actions").then(({ sendContinuation }) =>
            sendContinuation("continue", sessionId, { promptKind: "continue" }),
          );
          break;
        }
        case "open_settings":
          if (code === "model_oauth_expired") requestModelSubTab("subscription");
          openSettings("model");
          break;
        case "copy_diagnostic": {
          const diagPayload = JSON.stringify({
            diagnostic_id: diagnosticId,
            category,
            code,
            title,
            message,
            stage,
            retryable,
            provider: provider || undefined,
            model: model || undefined,
            timestamp: new Date().toISOString(),
          }, null, 2);
          navigator.clipboard.writeText(diagPayload).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
          }).catch(() => {});
          break;
        }
      }
    },
    [openSettings, diagnosticId, category, code, title, message, stage, retryable, provider, model],
  );

  return (
    <div className="my-2 rounded-2xl border border-[var(--em-hairline)] bg-background overflow-hidden">
      <div className="flex items-start gap-2 px-3 sm:px-3.5 py-2.5">
        <Icon className="h-4 w-4 flex-shrink-0 text-red-500 mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-[13px] font-semibold text-foreground truncate">{shownTitle}</span>
            <span className="text-[11px] font-medium px-1.5 py-px rounded-full bg-red-500/10 text-red-600 shrink-0">
              失败
            </span>
          </div>
          <p className="text-[12px] text-muted-foreground mt-0.5 leading-5 break-words">{shownMessage}</p>
          {meta && (
            <p className="text-[11px] text-muted-foreground/70 mt-1 truncate">{meta}</p>
          )}
          <div className="mt-2.5 flex items-center gap-1.5 flex-wrap">
            {resolvedActions.map((action, i) => {
              const isPrimary = action.type === "retry" && i === 0;
              if (action.type === "retry") {
                return (
                  <button
                    key={action.type}
                    type="button"
                    onClick={() => handleAction(action.type)}
                    className={isPrimary ? PRIMARY_BTN : GHOST_BTN}
                  >
                    <Play className="h-3.5 w-3.5 fill-current" />
                    {action.label}
                  </button>
                );
              }
              if (action.type === "open_settings") {
                return (
                  <button
                    key={action.type}
                    type="button"
                    onClick={() => handleAction(action.type)}
                    className={i === 0 ? PRIMARY_BTN : GHOST_BTN}
                  >
                    <Settings className="h-3.5 w-3.5" />
                    {action.label}
                  </button>
                );
              }
              return (
                <button
                  key={action.type}
                  type="button"
                  onClick={() => handleAction(action.type)}
                  className={GHOST_BTN}
                >
                  {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                  {copied ? "已复制" : action.label}
                </button>
              );
            })}
            {onRetryWithModel && offerRetry && (
              <RetryModelPicker onSelect={onRetryWithModel} triggerClassName={GHOST_BTN} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
