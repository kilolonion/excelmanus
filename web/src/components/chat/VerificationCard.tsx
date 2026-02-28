"use client";

import { CheckCircle2, AlertTriangle, HelpCircle, Shield } from "lucide-react";

interface VerificationCardProps {
  verdict: "pass" | "fail" | "unknown";
  confidence: "high" | "medium" | "low";
  checks: string[];
  issues: string[];
  mode: "advisory" | "blocking";
}

const verdictConfig = {
  pass: {
    icon: CheckCircle2,
    label: "验证通过",
    bgColor: "bg-emerald-50 dark:bg-emerald-950/30",
    borderColor: "border-emerald-200 dark:border-emerald-800",
    iconColor: "text-emerald-600 dark:text-emerald-400",
    labelColor: "text-emerald-700 dark:text-emerald-300",
  },
  fail: {
    icon: AlertTriangle,
    label: "验证未通过",
    bgColor: "bg-amber-50 dark:bg-amber-950/30",
    borderColor: "border-amber-200 dark:border-amber-800",
    iconColor: "text-amber-600 dark:text-amber-400",
    labelColor: "text-amber-700 dark:text-amber-300",
  },
  unknown: {
    icon: HelpCircle,
    label: "验证不确定",
    bgColor: "bg-slate-50 dark:bg-slate-900/30",
    borderColor: "border-slate-200 dark:border-slate-700",
    iconColor: "text-slate-500 dark:text-slate-400",
    labelColor: "text-slate-600 dark:text-slate-300",
  },
};

const confidenceBadge = {
  high: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
  medium: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
  low: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
};

export default function VerificationCard({
  verdict,
  confidence,
  checks,
  issues,
  mode,
}: VerificationCardProps) {
  const config = verdictConfig[verdict] || verdictConfig.unknown;
  const Icon = config.icon;

  return (
    <div
      className={`my-2 rounded-lg border ${config.borderColor} ${config.bgColor} p-3 text-sm`}
    >
      {/* Header */}
      <div className="flex items-center gap-2">
        <Icon className={`h-4 w-4 ${config.iconColor}`} />
        <span className={`font-medium ${config.labelColor}`}>
          {config.label}
        </span>
        {({ high: "高", medium: "中", low: "低" } as Record<string, string>)[confidence] && (
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-medium ${confidenceBadge[confidence] || confidenceBadge.low}`}
          >
            {({ high: "高", medium: "中", low: "低" } as Record<string, string>)[confidence]}
          </span>
        )}
        {mode === "blocking" && (
          <span className="flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700 dark:bg-red-900/40 dark:text-red-300">
            <Shield className="h-3 w-3" />
            阻断
          </span>
        )}
      </div>

      {/* Checks */}
      {checks.length > 0 && (
        <div className="mt-2 space-y-1">
          {checks.map((check, i) => (
            <div
              key={i}
              className="flex items-start gap-1.5 text-xs text-slate-600 dark:text-slate-400"
            >
              <span className="mt-0.5 text-emerald-500">✓</span>
              <span>{check}</span>
            </div>
          ))}
        </div>
      )}

      {/* Issues */}
      {issues.length > 0 && (
        <div className="mt-2 space-y-1">
          {issues.map((issue, i) => (
            <div
              key={i}
              className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400"
            >
              <span className="mt-0.5">⚠</span>
              <span>{issue}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
