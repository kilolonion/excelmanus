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
  HardDrive,
  ArrowRightLeft,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { motion } from "framer-motion";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { apiGet } from "@/lib/api";
import { formatModelIdForDisplay } from "@/lib/model-display";
import { useUIStore } from "@/stores/ui-store";
import type { ModelInfo } from "@/lib/types";

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
  tool_execution: "工具执行",
  context_building: "上下文构建",
  session_init: "会话初始化",
  quota_check: "配额检查",
  file_processing: "文件处理",
  sandbox_exec: "沙箱执行",
  mcp_call: "MCP 调用",
};

/** 根据错误 code 生成用户友好的简短描述 */
const CODE_HINTS: Record<string, string> = {
  model_auth_failed: "API Key 无效或过期",
  model_not_found: "模型不存在或已下线",
  quota_exceeded: "API 额度耗尽",
  rate_limited: "请求频率超限",
  context_length_exceeded: "上下文超长",
  provider_internal_error: "服务内部错误",
  network_error: "网络连接异常",
  connect_timeout: "连接超时",
  stream_stalled: "流式响应停滞",
  ssl_error: "SSL/TLS 证书错误",
  proxy_error: "代理连接失败",
  content_filtered: "内容安全策略拦截",
  response_parse_error: "响应解析失败",
  stream_interrupted: "流式传输中断",
  encoding_error: "文件编码异常",
  disk_full: "磁盘空间不足",
  permission_denied: "权限不足",
  payload_too_large: "请求体过大",
  invalid_request: "请求参数无效",
  request_timeout: "请求超时",
  session_busy: "会话正在处理中",
  session_limit: "会话数量达到上限",
  session_not_found: "会话已过期",
  empty_message: "消息内容为空",
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
    icon: HardDrive,
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
  code,
  title,
  message,
  stage,
  retryable,
  diagnosticId,
  actions,
  provider,
  model,
  onRetry,
  onRetryWithModel,
}: FailureGuidanceCardProps) {
  const openSettings = useUIStore((s) => s.openSettings);
  const currentModel = useUIStore((s) => s.currentModel);
  const [copied, setCopied] = useState(false);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelsLoaded, setModelsLoaded] = useState(false);

  const fetchModelsOnce = useCallback(() => {
    if (modelsLoaded) return;
    setModelsLoaded(true);
    apiGet<{ models: ModelInfo[] }>("/models")
      .then((data) => setModels(data.models))
      .catch(() => {});
  }, [modelsLoaded]);

  const config = CATEGORY_CONFIG[category] || CATEGORY_CONFIG.unknown;
  const Icon = config.icon;
  const stageLabel = STAGE_LABELS[stage] || stage;
  const codeHint = CODE_HINTS[code] || "";

  const handleAction = useCallback(
    (actionType: string) => {
      switch (actionType) {
        case "retry":
          onRetry?.();
          break;
        case "open_settings":
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
    [onRetry, openSettings, diagnosticId, category, code, title, message, stage, retryable, provider, model],
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
      <div className="px-4 pb-2 flex items-center gap-3 flex-wrap text-[11px] text-muted-foreground/60">
        {stageLabel && (
          <span>
            阶段: <span className="font-medium text-muted-foreground/80">{stageLabel}</span>
          </span>
        )}
        {codeHint && (
          <>
            <span className="opacity-30">·</span>
            <span className="font-medium text-muted-foreground/80">{codeHint}</span>
          </>
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
        <div className="px-4 pb-3.5 flex items-center gap-2 flex-wrap">
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
          {/* 换模型重试下拉 */}
          {onRetryWithModel && retryable && (
            <DropdownMenu onOpenChange={(open) => { if (open) fetchModelsOnce(); }}>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  className="group/btn flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-all cursor-pointer bg-muted/50 hover:bg-muted text-foreground/70 hover:text-foreground"
                >
                  <ArrowRightLeft className="h-3.5 w-3.5" />
                  换模型重试
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-64 max-w-[calc(100vw-2rem)] p-0 overflow-hidden">
                <div className="px-3 pt-2.5 pb-2 border-b border-border/50">
                  <div className="flex items-center gap-2">
                    <RefreshCw className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />
                    <span className="text-xs font-medium text-foreground/70">选择模型重试</span>
                  </div>
                </div>
                <div className="max-h-[30vh] overflow-y-auto py-1">
                  {models.map((m) => {
                    const isCurrent = m.name === currentModel;
                    const label = m.name === "default" ? formatModelIdForDisplay(m.model) : (m.display_name || m.name);
                    return (
                      <button
                        key={m.name}
                        onClick={() => onRetryWithModel(m.name)}
                        className={[
                          "w-full text-left px-3 py-2 flex items-center gap-2",
                          "transition-all duration-150 ease-out cursor-pointer",
                          "hover:bg-accent/50",
                          isCurrent ? "bg-[var(--em-primary-alpha-06)]" : "",
                        ].join(" ")}
                      >
                        <span className="text-xs font-medium truncate flex-1">{label}</span>
                        {isCurrent && (
                          <span className="text-[9px] px-1.5 py-px rounded-full font-medium" style={{ backgroundColor: "var(--em-primary-alpha-10)", color: "var(--em-primary)" }}>当前</span>
                        )}
                      </button>
                    );
                  })}
                  {models.length === 0 && (
                    <div className="px-3 py-4 text-center">
                      <Loader2 className="h-4 w-4 text-muted-foreground/25 mx-auto mb-1 animate-spin" />
                      <p className="text-xs text-muted-foreground/40">加载模型列表...</p>
                    </div>
                  )}
                </div>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      )}
    </motion.div>
  );
}
