"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, X, Settings } from "lucide-react";
import { checkModelPlaceholder } from "@/lib/api";
import type { PlaceholderCheckResult } from "@/lib/api";

const DISMISS_KEY = "excelmanus_placeholder_alert_dismissed";

export function PlaceholderAlert() {
  const [data, setData] = useState<PlaceholderCheckResult | null>(null);
  const [dismissed, setDismissed] = useState(
    () => typeof window !== "undefined" && sessionStorage.getItem(DISMISS_KEY) === "1"
  );

  useEffect(() => {
    if (dismissed) return;
    checkModelPlaceholder()
      .then((result) => setData(result))
      .catch(() => {});
  }, [dismissed]);

  const handleDismiss = () => {
    setDismissed(true);
    sessionStorage.setItem(DISMISS_KEY, "1");
  };

  if (dismissed || !data?.has_placeholder) return null;

  const items = data.items;

  return (
    <div className="fixed bottom-20 left-1/2 -translate-x-1/2 z-50 w-[90vw] max-w-md animate-in slide-in-from-bottom-4 fade-in duration-300">
      <div className="rounded-lg border border-amber-500/30 bg-amber-50 dark:bg-amber-950/40 shadow-lg px-4 py-3">
        <div className="flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-amber-800 dark:text-amber-300">
              检测到模型配置可能未完成
            </p>
            <p className="text-xs text-amber-700/80 dark:text-amber-400/70 mt-1">
              以下配置的 API Key 为空或疑似占位符，可能导致对话失败：
            </p>
            <ul className="mt-1.5 space-y-0.5">
              {items.map((item, i) => (
                <li key={i} className="text-xs text-amber-700 dark:text-amber-300/80 flex items-center gap-1">
                  <span className="font-mono font-medium">{item.name}</span>
                  <span className="text-amber-600/60 dark:text-amber-400/50">·</span>
                  <span className="truncate">{item.model || "(未设置模型)"}</span>
                  <span className="text-amber-600/60 dark:text-amber-400/50">·</span>
                  <span>{item.field === "api_key" ? "API Key" : item.field}</span>
                </li>
              ))}
            </ul>
            <p className="text-[11px] text-amber-600/70 dark:text-amber-400/50 mt-2 flex items-center gap-1">
              <Settings className="h-3 w-3" />
              请在右上角设置中配置正确的 API Key
            </p>
          </div>
          <button
            onClick={handleDismiss}
            className="shrink-0 text-amber-600/60 hover:text-amber-800 dark:text-amber-400/50 dark:hover:text-amber-300 transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
