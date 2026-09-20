"use client";

import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, Check, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
  DropdownMenuLabel,
} from "@/components/ui/dropdown-menu";
import { useUIStore } from "@/stores/ui-store";
import { apiGet, apiPut } from "@/lib/api";
import { displayModelLabel, formatModelIdForDisplay } from "@/lib/model-display";
import type { ModelInfo } from "@/lib/types";
import { applyVisionFromModel } from "@/lib/vision-capability";

export function ModelSelector() {
  const currentModel = useUIStore((s) => s.currentModel);
  const setCurrentModel = useUIStore((s) => s.setCurrentModel);
  const modelProfileVersion = useUIStore((s) => s.modelProfileVersion);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const modelsRequestRef = useRef(0);

  const fetchModels = () => {
    const requestId = ++modelsRequestRef.current;
    const profileVersion = useUIStore.getState().modelProfileVersion;
    apiGet<{ models: ModelInfo[] }>("/models")
      .then((data) => {
        if (requestId !== modelsRequestRef.current || profileVersion !== useUIStore.getState().modelProfileVersion) return;
        setModels(data.models);
        const active = data.models.find((m) => m.active);
        setCurrentModel(active?.name ?? "");
        applyVisionFromModel(active);
        if (!active) useUIStore.getState().setVisionCapable(null);
      })
      .catch(() => {});
  };

  useEffect(() => {
    fetchModels();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 当 Settings 页 profile 变更时自动刷新模型列表
  useEffect(() => {
    if (modelProfileVersion > 0) fetchModels();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelProfileVersion]);

  const handleSwitch = async (name: string) => {
    if (name === currentModel || switching) return;
    setSwitching(true);
    setSwitchError(null);
    try {
      await apiPut("/models/active", { name });
      setCurrentModel(name);
      useUIStore.getState().bumpModelProfiles();
      applyVisionFromModel(models.find((m) => m.name === name));
    } catch (e) {
      const msg = e instanceof Error ? e.message : "切换失败";
      setSwitchError(msg);
      setTimeout(() => setSwitchError(null), 3000);
    } finally {
      setSwitching(false);
    }
  };

  const activeModel = models.find((m) => m.name === currentModel);
  const resolvedModel = (m: ModelInfo) => formatModelIdForDisplay(m.resolved_model || m.model);
  const displayName = activeModel
    ? displayModelLabel(activeModel)
    : currentModel || "模型未加载";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="group w-full justify-between text-sm h-auto py-1.5 min-h-[32px] focus-visible:ring-2 focus-visible:ring-[var(--em-primary)]"
        >
          <div className="flex items-center gap-2 min-w-0 flex-1">
            {/* Brand_Color 状态指示器圆点 */}
            <span
              className="h-2 w-2 rounded-full flex-shrink-0 transition-all duration-300"
              style={{ backgroundColor: "var(--em-primary)" }}
            />
            <div className="flex flex-col items-start min-w-0 flex-1">
              <AnimatePresence mode="wait" initial={false}>
                <motion.span
                  key={currentModel || "_none"}
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  transition={{ duration: 0.15, ease: [0.4, 0, 0.2, 1] }}
                  className="truncate font-medium"
                >
                  {displayName}
                </motion.span>
              </AnimatePresence>
              {activeModel?.model && activeModel.name !== resolvedModel(activeModel) && (
                <span className="truncate text-muted-foreground text-[10px]">
                  {resolvedModel(activeModel)}
                </span>
              )}
            </div>
          </div>
          {switching ? (
            <Loader2
              className="h-3 w-3 ml-1 animate-spin flex-shrink-0"
              style={{ color: "var(--em-primary)" }}
            />
          ) : (
            <ChevronDown className="h-3 w-3 ml-1 flex-shrink-0 transition-colors duration-150 ease-out group-hover:text-[var(--em-primary)]" />
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-72"
        style={{ animationDuration: "150ms" }}
      >
        <DropdownMenuLabel className="text-xs text-muted-foreground">
          可用模型 ({models.length})
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {models.map((m) => {
          const isSelected = m.name === currentModel;
          return (
            <DropdownMenuItem
              key={m.name}
              onClick={() => handleSwitch(m.name)}
              className="flex flex-col items-start gap-0.5 py-2 cursor-pointer border-l-[3px] border-l-transparent transition-all duration-150 ease-out hover:border-l-[var(--em-primary)] min-h-[32px] focus-visible:ring-2 focus-visible:ring-[var(--em-primary)]"
              style={
                isSelected
                  ? { backgroundColor: "var(--em-primary-alpha-10)" }
                  : undefined
              }
            >
              <div className="flex items-center gap-2 w-full">
                <span className={isSelected ? "font-semibold" : ""}>
                  {displayModelLabel(m)}
                </span>
                {isSelected && (
                  <Check
                    className="h-3 w-3 ml-auto flex-shrink-0"
                    style={{ color: "var(--em-primary)" }}
                  />
                )}
              </div>
              <span className="text-[10px] text-muted-foreground truncate w-full">
                {resolvedModel(m)}
                {m.description ? ` · ${m.description}` : ""}
              </span>
            </DropdownMenuItem>
          );
        })}
        {models.length === 0 && (
          <DropdownMenuItem disabled>暂无可用模型</DropdownMenuItem>
        )}
        {switchError && (
          <div className="px-2 py-1.5 text-[11px] text-destructive">
            {switchError}
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
