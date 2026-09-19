"use client";

import { KeyRound, Settings, ShieldAlert } from "lucide-react";
import { useUIStore } from "@/stores/ui-store";

export function ConfigErrorCard({ items }: { items: { name: string; field: string; model: string }[] }) {
  const openSettings = useUIStore((s) => s.openSettings);

  const friendlyName = (name: string) => {
    if (name === "active") return "当前模型";
    if (name === "vision") return "视觉模型";
    return name;
  };

  const friendlyField = (field: string) => {
    if (field === "api_key") return "API Key";
    if (field === "base_url") return "Base URL";
    if (field === "model") return "Model";
    return field;
  };

  return (
    <div className="my-2 rounded-2xl border border-[var(--em-hairline)] bg-background overflow-hidden">
      <div className="flex items-start gap-2 px-3 sm:px-3.5 py-2.5">
        <ShieldAlert className="h-4 w-4 flex-shrink-0 text-amber-500 mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="whitespace-nowrap text-[13px] font-semibold text-foreground">模型尚未配置</span>
            <span className="text-[11px] font-medium px-1.5 py-px rounded-full whitespace-nowrap shrink-0 bg-amber-500/10 text-amber-700 dark:text-amber-400">
              待处理
            </span>
          </div>
          <p className="text-[12px] text-muted-foreground mt-0.5 leading-5">
            当前模型的 API 配置为空或仍为默认占位符
          </p>
          {items.length > 0 && (
            <div className="mt-2 space-y-1.5">
              {items.map((item, i) => (
                <div key={i} className="flex items-center gap-2 text-[12px] text-muted-foreground">
                  <KeyRound className="h-3.5 w-3.5 flex-shrink-0" />
                  <span className="truncate">
                    {friendlyName(item.name)}
                    <span className="mx-1.5 opacity-40">·</span>
                    {item.model || "未设置模型"}
                    <span className="mx-1.5 opacity-40">·</span>
                    {friendlyField(item.field)} 缺失
                  </span>
                </div>
              ))}
            </div>
          )}
          <button
            type="button"
            onClick={() => openSettings("model")}
            className="touch-compact mt-2.5 inline-flex h-9 sm:h-8 items-center gap-1.5 rounded-lg px-3 text-[13px] font-semibold text-white bg-[var(--em-primary)] hover:opacity-90"
          >
            <Settings className="h-3.5 w-3.5" />
            前往设置
          </button>
        </div>
      </div>
    </div>
  );
}
