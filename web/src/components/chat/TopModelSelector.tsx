"use client";

import { useEffect, useState } from "react";
import { ChevronDown, Check, Loader2, AlertTriangle } from "lucide-react";
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

interface ModelCapabilitySummary {
  name: string;
  model: string;
  base_url: string;
  capabilities: { healthy: boolean | null; health_error: string } | null;
}

/**
 * 从 base_url 提取二级域名作为 provider 名称。
 * 例如 https://api.deepseek.com/v1 → deepseek
 *      https://dashscope.aliyuncs.com/compatible-mode/v1 → aliyuncs
 *      https://api.openai.com/v1 → openai
 */
function extractProvider(baseUrl: string | undefined): string {
  if (!baseUrl) return "unknown";
  try {
    const hostname = new URL(baseUrl).hostname; // 例如 "api.deepseek.com"
    const parts = hostname.split(".");
    // 取倒数第二段作为二级域名
    if (parts.length >= 2) {
      return parts[parts.length - 2];
    }
    return parts[0] || "unknown";
  } catch {
    return "unknown";
  }
}

interface ProviderGroup {
  provider: string;
  models: ModelInfo[];
}

function groupByProvider(models: ModelInfo[]): ProviderGroup[] {
  const map = new Map<string, ModelInfo[]>();
  for (const m of models) {
    const provider = extractProvider(m.base_url);
    if (!map.has(provider)) map.set(provider, []);
    map.get(provider)!.push(m);
  }
  return Array.from(map.entries()).map(([provider, models]) => ({
    provider,
    models,
  }));
}

export function TopModelSelector() {
  const currentModel = useUIStore((s) => s.currentModel);
  const setCurrentModel = useUIStore((s) => s.setCurrentModel);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const [capsMap, setCapsMap] = useState<Record<string, { healthy: boolean | null; health_error: string }>>({});

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
    // 加载模型健康状态
    apiGet<{ items: ModelCapabilitySummary[] }>("/config/models/capabilities/all")
      .then((data) => {
        const map: Record<string, { healthy: boolean | null; health_error: string }> = {};
        for (const item of data.items) {
          if (item.capabilities) {
            map[item.name] = { healthy: item.capabilities.healthy, health_error: item.capabilities.health_error };
          }
        }
        setCapsMap(map);
      })
      .catch(() => {});
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
  const displayName = activeModel?.name || currentModel || "模型";
  const currentModelUnhealthy = currentModel && capsMap[currentModel]?.healthy === false;
  const groups = groupByProvider(models);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="gap-1 px-2 h-9 text-base font-semibold">
          {currentModelUnhealthy && (
            <AlertTriangle className="h-3.5 w-3.5 text-destructive shrink-0" />
          )}
          <span className={`truncate max-w-[100px] sm:max-w-[200px] ${currentModelUnhealthy ? "text-destructive" : ""}`}>{displayName}</span>
          {switching ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-72 max-w-[calc(100vw-2rem)] max-h-[50vh] overflow-y-auto">
        {groups.map((group, gi) => (
          <div key={group.provider}>
            {gi > 0 && <DropdownMenuSeparator />}
            <DropdownMenuLabel className="text-[10px] uppercase tracking-wider text-muted-foreground font-normal">
              {group.provider}
            </DropdownMenuLabel>
            {group.models.map((m) => (
              <DropdownMenuItem
                key={m.name}
                onClick={() => handleSwitch(m.name)}
                className="flex items-center gap-2 py-2 cursor-pointer"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span
                      className={`text-sm ${
                        m.name === currentModel ? "font-semibold" : ""
                      }`}
                    >
                      {m.name}
                    </span>
                    {capsMap[m.name]?.healthy === false && (
                      <span className="inline-flex items-center gap-0.5 text-[10px] text-destructive">
                        <AlertTriangle className="h-2.5 w-2.5" />
                        不可用
                      </span>
                    )}
                    {m.name !== m.model && capsMap[m.name]?.healthy !== false && (
                      <span className="text-[10px] text-muted-foreground font-mono truncate">
                        {m.model}
                      </span>
                    )}
                  </div>
                  {m.description && (
                    <span className="text-[10px] text-muted-foreground block truncate">
                      {m.description}
                    </span>
                  )}
                </div>
                {m.name === currentModel && (
                  <Check
                    className="h-3.5 w-3.5 flex-shrink-0"
                    style={{ color: "var(--em-primary)" }}
                  />
                )}
              </DropdownMenuItem>
            ))}
          </div>
        ))}
        {models.length === 0 && (
          <DropdownMenuItem disabled className="text-xs">
            暂无可用模型
          </DropdownMenuItem>
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
