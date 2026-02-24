"use client";

import { useEffect, useState } from "react";
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
import type { ModelInfo } from "@/lib/types";

export function ModelSelector() {
  const currentModel = useUIStore((s) => s.currentModel);
  const setCurrentModel = useUIStore((s) => s.setCurrentModel);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);

  const fetchModels = () => {
    apiGet<{ models: ModelInfo[] }>("/models")
      .then((data) => {
        setModels(data.models);
        const active = data.models.find((m) => m.active);
        if (active) setCurrentModel(active.name);
      })
      .catch(() => {});
  };

  useEffect(() => {
    fetchModels();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSwitch = async (name: string) => {
    if (name === currentModel || switching) return;
    setSwitching(true);
    setSwitchError(null);
    try {
      await apiPut("/models/active", { name });
      setCurrentModel(name);
      fetchModels();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "切换失败";
      setSwitchError(msg);
      setTimeout(() => setSwitchError(null), 3000);
    } finally {
      setSwitching(false);
    }
  };

  const activeModel = models.find((m) => m.name === currentModel);
  const displayName = activeModel
    ? `${activeModel.name}`
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
              className="h-2 w-2 rounded-full flex-shrink-0"
              style={{ backgroundColor: "var(--em-primary)" }}
            />
            <div className="flex flex-col items-start min-w-0 flex-1">
              <span className="truncate font-medium">{displayName}</span>
              {activeModel?.model && activeModel.name !== activeModel.model && (
                <span className="truncate text-muted-foreground text-[10px]">
                  {activeModel.model}
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
                  {m.name}
                </span>
                {isSelected && (
                  <Check
                    className="h-3 w-3 ml-auto flex-shrink-0"
                    style={{ color: "var(--em-primary)" }}
                  />
                )}
              </div>
              <span className="text-[10px] text-muted-foreground truncate w-full">
                {m.model}
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
