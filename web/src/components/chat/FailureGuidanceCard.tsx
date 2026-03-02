"use client";

import React, { useCallback, useState } from "react";
import {
  AlertTriangle,
  RotateCcw,
  Settings,
  Copy,
  Check,
  ShieldAlert,
  Wifi,
  CreditCard,
  HelpCircle,
} from "lucide-react";
import { motion } from "framer-motion";
import { useUIStore } from "@/stores/ui-store";

const STAGE_LABELS: Record<string, string> = {
  initializing: "初始化",
  routing: "任务分析",
  calling_llm: "模型通信",
  streaming: "流式接收",
  connecting: "建立连接",
  reconnecting: "重新连接",
  save_command: "保存命令",
  subscribe_resume: "重连恢复",
  llm_retrying: "模型重试",
};

const CATEGORY_CONFIG: Record<
  string,
  {
    icon: React.ElementType;
    borderColor: string;
    bgFrom: string;
    bgTo: string;
    iconBg: string;
    iconColor: string;
    titleColor: string;
    descColor: string;
  }
> = {
  model: {
    icon: ShieldAlert,
    borderColor: "border-red-500/25",
    bgFrom: "from-red-50/80",
    bgTo: "to-rose-50/40",
    iconBg: "bg-red-500/15 dark:bg-red-500/20",
    iconColor: "text-red-600 dark:text-red-400",
    titleColor: "text-red-900 dark:text-red-200",
    descColor: "text-red-700/70 dark:text-red-400/60",
  },
  transport: {
    icon: Wifi,
    borderColor: "border-amber-500/25",
    bgFrom: "from-amber-50/80",
    bgTo: "to-orange-50/40",
    iconBg: "bg-amber-500/15 dark:bg-amber-500/20",
    iconColor: "text-amber-600 dark:text-amber-400",
    titleColor: "text-amber-900 dark:text-amber-200",
    descColor: "text-amber-700/70 dark:text-amber-400/60",
  },
  quota: {
    icon: CreditCard,
    borderColor: "border-orange-500/25",
    bgFrom: "from-orange-50/80",
    bgTo: "to-amber-50/40",
    iconBg: "bg-orange-500/15 dark:bg-orange-500/20",
    iconColor: "text-orange-600 dark:text-orange-400",
    titleColor: "text-orange-900 dark:text-orange-200",
    descColor: "text-orange-700/70 dark:text-orange-400/60",
  },
  config: {
    icon: Settings,
    borderColor: "border-amber-500/25",
    bgFrom: "from-amber-50/80",
    bgTo: "to-orange-50/40",
    iconBg: "bg-amber-500/15 dark:bg-amber-500/20",
    iconColor: "text-amber-600 dark:text-amber-400",
    titleColor: "text-amber-900 dark:text-amber-200",
    descColor: "text-amber-700/70 dark:text-amber-400/60",
  },
  unknown: {
    icon: HelpCircle,
    borderColor: "border-red-500/25",
    bgFrom: "from-red-50/80",
    bgTo: "to-rose-50/40",
    iconBg: "bg-red-500/15 dark:bg-red-500/20",
    iconColor: "text-red-600 dark:text-red-400",
    titleColor: "text-red-900 dark:text-red-200",
    descColor: "text-red-700/70 dark:text-red-400/60",
  },
};

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
  onRetry?: () => void;
  onRetryWithModel?: (modelName: string) => void;
}

export function FailureGuidanceCard({
  category,
  title,
  message,
  stage,
  retryable,
  diagnosticId,
  actions,
  provider,
  model,
  onRetry,
}: FailureGuidanceCardProps) {
  const openSettings = useUIStore((s) => s.openSettings);
  const [copied, setCopied] = useState(false);

  const config = CATEGORY_CONFIG[category] || CATEGORY_CONFIG.unknown;
  const Icon = config.icon;
  const stageLabel = STAGE_LABELS[stage] || stage;

  const handleAction = useCallback(
    (actionType: string) => {
      switch (actionType) {
        case "retry":
          onRetry?.();
          break;
        case "open_settings":
          openSettings("model");
          break;
        case "copy_diagnostic":
          if (diagnosticId) {
            navigator.clipboard.writeText(diagnosticId).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            }).catch(() => {});
          }
          break;
      }
    },
    [onRetry, openSettings, diagnosticId],
  );

  const actionIcon = (type: string) => {
    switch (type) {
      case "retry":
        return <RotateCcw className="h-3.5 w-3.5" />;
      case "open_settings":
        return <Settings className="h-3.5 w-3.5" />;
      case "copy_diagnostic":
        return copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />;
      default:
        return null;
    }
  };

  return (
    <motion.div
      className={`my-2 rounded-xl border ${config.borderColor} bg-gradient-to-br ${config.bgFrom} ${config.bgTo} dark:from-neutral-950/30 dark:to-neutral-950/20 overflow-hidden shadow-sm`}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.4, 0, 0.2, 1] }}
    >
      {/* 顶部标题栏 */}
      <div className="flex items-center gap-3 px-4 pt-4 pb-2">
        <div className={`flex h-9 w-9 items-center justify-center rounded-lg ${config.iconBg}`}>
          <Icon className={`h-[18px] w-[18px] ${config.iconColor}`} />
        </div>
        <div className="flex-1 min-w-0">
          <h4 className={`text-sm font-semibold ${config.titleColor}`}>
            {title}
          </h4>
          <p className={`text-xs ${config.descColor} mt-0.5`}>
            {message}
          </p>
        </div>
        {retryable && (
          <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 dark:bg-amber-500/20 px-2 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-400">
            <AlertTriangle className="h-3 w-3" />
            可重试
          </span>
        )}
      </div>

      {/* 元数据行 */}
      <div className="px-4 pb-2 flex items-center gap-3 text-[11px] text-muted-foreground/60">
        {stageLabel && (
          <span>
            阶段: <span className="font-medium text-muted-foreground/80">{stageLabel}</span>
          </span>
        )}
        {provider && (
          <>
            <span className="opacity-30">·</span>
            <span>{provider}{model ? ` / ${model}` : ""}</span>
          </>
        )}
        {diagnosticId && (
          <>
            <span className="opacity-30">·</span>
            <span className="font-mono truncate max-w-[120px]" title={diagnosticId}>
              {diagnosticId.slice(0, 8)}
            </span>
          </>
        )}
      </div>

      {/* 操作按钮栏 */}
      {actions.length > 0 && (
        <div className="px-4 pb-3.5 flex items-center gap-2">
          {actions.map((action, i) => {
            const isPrimary = i === 0;
            return (
              <button
                key={action.type}
                type="button"
                onClick={() => handleAction(action.type)}
                className={`group/btn flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-all cursor-pointer ${
                  isPrimary
                    ? "bg-[var(--em-accent)] hover:bg-[var(--em-accent)]/90 text-white shadow-sm hover:shadow"
                    : "bg-muted/50 hover:bg-muted text-foreground/70 hover:text-foreground"
                }`}
              >
                {actionIcon(action.type)}
                {action.type === "copy_diagnostic" && copied ? "已复制" : action.label}
              </button>
            );
          })}
        </div>
      )}
    </motion.div>
  );
}
