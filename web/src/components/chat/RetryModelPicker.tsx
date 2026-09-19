"use client";

import { useCallback, useState } from "react";
import { ArrowRightLeft, Loader2, RefreshCw } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ModelListBottomSheet } from "@/components/chat/ModelListBottomSheet";
import { apiGet } from "@/lib/api";
import { displayModelLabel } from "@/lib/model-display";
import type { ModelInfo } from "@/lib/types";
import { useIsMobile } from "@/hooks/use-mobile";
import { useUIStore } from "@/stores/ui-store";

const DEFAULT_TRIGGER =
  "touch-compact inline-flex h-9 sm:h-8 items-center justify-center gap-1.5 rounded-lg px-2.5 text-[12px] font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50";

interface RetryModelPickerProps {
  onSelect: (modelName: string) => void;
  triggerClassName?: string;
  triggerLabel?: string;
  labelClassName?: string;
}

export function RetryModelPicker({
  onSelect,
  triggerClassName = DEFAULT_TRIGGER,
  triggerLabel = "换模型",
  labelClassName,
}: RetryModelPickerProps) {
  const currentModel = useUIStore((s) => s.currentModel);
  const isMobile = useIsMobile();
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modelsLoaded, setModelsLoaded] = useState(false);
  const [mobileSheetOpen, setMobileSheetOpen] = useState(false);

  const fetchModelsOnce = useCallback(() => {
    if (modelsLoaded) return;
    setModelsLoaded(true);
    apiGet<{ models: ModelInfo[] }>("/models")
      .then((data) => setModels(data.models))
      .catch(() => {});
  }, [modelsLoaded]);

  if (isMobile) {
    return (
      <>
        <button
          type="button"
          aria-label={triggerLabel}
          onClick={() => {
            fetchModelsOnce();
            setMobileSheetOpen(true);
          }}
          className={triggerClassName}
        >
          <ArrowRightLeft className="h-3.5 w-3.5 shrink-0" />
          <span className={labelClassName}>{triggerLabel}</span>
        </button>
        <ModelListBottomSheet
          open={mobileSheetOpen}
          onOpenChange={setMobileSheetOpen}
          models={models}
          currentModel={currentModel}
          onSelect={onSelect}
          mode="retry"
        />
      </>
    );
  }

  return (
    <DropdownMenu onOpenChange={(open) => { if (open) fetchModelsOnce(); }}>
      <DropdownMenuTrigger asChild>
        <button type="button" aria-label={triggerLabel} className={triggerClassName}>
          <ArrowRightLeft className="h-3.5 w-3.5 shrink-0" />
          <span className={labelClassName}>{triggerLabel}</span>
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
            return (
              <button
                key={m.name}
                type="button"
                onClick={() => onSelect(m.name)}
                className={[
                  "w-full text-left px-3 py-2 flex items-center gap-2",
                  "transition-all duration-150 ease-out cursor-pointer",
                  "hover:bg-accent/50",
                  isCurrent ? "bg-[var(--em-primary-alpha-06)]" : "",
                ].join(" ")}
              >
                <span className="text-xs font-medium truncate flex-1">{displayModelLabel(m)}</span>
                {isCurrent && (
                  <span
                    className="text-[9px] px-1.5 py-px rounded-full font-medium"
                    style={{ backgroundColor: "var(--em-primary-alpha-10)", color: "var(--em-primary)" }}
                  >
                    当前
                  </span>
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
  );
}
