"use client";

import {
  ShieldAlert,
  KeyRound,
  Settings,
  ArrowRight,
} from "lucide-react";
import { motion } from "framer-motion";
import { useUIStore } from "@/stores/ui-store";

export function ConfigErrorCard({ items }: { items: { name: string; field: string; model: string }[] }) {
  const openSettings = useUIStore((s) => s.openSettings);

  const friendlyName = (name: string) => {
    if (name === "main") return "主模型";
    if (name === "vision" || name === "vlm") return "视觉模型";
    return name;
  };

  const friendlyField = (field: string) => {
    if (field === "api_key") return "API Key";
    if (field === "base_url") return "Base URL";
    if (field === "model") return "Model";
    return field;
  };

  return (
    <motion.div
      className="my-2 rounded-xl border border-amber-500/25 bg-gradient-to-br from-amber-50/80 to-orange-50/40 dark:from-amber-950/30 dark:to-orange-950/20 overflow-hidden shadow-sm"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.4, 0, 0.2, 1] }}
    >
      {/* 顶部标题栏 */}
      <div className="flex items-center gap-3 px-4 pt-4 pb-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-amber-500/15 dark:bg-amber-500/20">
          <ShieldAlert className="h-[18px] w-[18px] text-amber-600 dark:text-amber-400" />
        </div>
        <div className="flex-1 min-w-0">
          <h4 className="text-sm font-semibold text-amber-900 dark:text-amber-200">
            模型尚未配置
          </h4>
          <p className="text-xs text-amber-700/70 dark:text-amber-400/60 mt-0.5">
            当前模型的 API 配置为空或仍为默认占位符
          </p>
        </div>
      </div>

      {/* 缺失项列表 */}
      <div className="mx-4 mt-2 mb-3 rounded-lg border border-amber-500/15 bg-white/60 dark:bg-white/5 divide-y divide-amber-500/10">
        {items.map((item, i) => (
          <div key={i} className="flex items-center gap-3 px-3 py-2.5">
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-amber-500/10 dark:bg-amber-500/15">
              <KeyRound className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-[13px] font-medium text-foreground truncate">
                {friendlyName(item.name)}
              </p>
              <p className="text-[11px] text-muted-foreground truncate">
                {item.model || "未设置模型"}
                <span className="mx-1.5 opacity-40">·</span>
                <span className="text-amber-600 dark:text-amber-400 font-medium">
                  {friendlyField(item.field)} 缺失
                </span>
              </p>
            </div>
          </div>
        ))}
      </div>

      {/* 底部操作栏 */}
      <div className="px-4 pb-3.5">
        <button
          type="button"
          onClick={() => openSettings("model")}
          className="group/btn flex items-center justify-center gap-2 w-full rounded-lg px-3 py-2 text-sm font-medium transition-all cursor-pointer bg-amber-500/90 hover:bg-amber-500 text-white shadow-sm hover:shadow"
        >
          <Settings className="h-3.5 w-3.5" />
          前往设置
          <ArrowRight className="h-3.5 w-3.5 opacity-60 group-hover/btn:translate-x-0.5 transition-transform" />
        </button>
      </div>
    </motion.div>
  );
}
