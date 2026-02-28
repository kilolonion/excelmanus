"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Check, Loader2, AlertTriangle, Search, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
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

/** Provider brand colors for visual distinction */
const PROVIDER_COLORS: Record<string, string> = {
  openai: "#10a37f",
  deepseek: "#4d6bfe",
  aliyuncs: "#ff6a00",
  dashscope: "#ff6a00",
  anthropic: "#d4a574",
  google: "#4285f4",
  moonshot: "#7c3aed",
  zhipu: "#2563eb",
  baidu: "#2932e1",
  groq: "#f55036",
  mistral: "#ff7000",
  together: "#6366f1",
  cohere: "#39594d",
  siliconflow: "#06b6d4",
};

/** Friendly display names for known providers */
const PROVIDER_DISPLAY: Record<string, string> = {
  openai: "OpenAI",
  deepseek: "DeepSeek",
  aliyuncs: "阿里云",
  dashscope: "DashScope",
  anthropic: "Anthropic",
  google: "Google",
  moonshot: "Moonshot",
  zhipu: "智谱",
  baidu: "百度",
  groq: "Groq",
  mistral: "Mistral",
  together: "Together",
  cohere: "Cohere",
  siliconflow: "SiliconFlow",
};

/**
 * 从 base_url 提取二级域名作为 provider 名称。
 * 例如 https://api.deepseek.com/v1 → deepseek
 *      https://dashscope.aliyuncs.com/compatible-mode/v1 → aliyuncs
 *      https://api.openai.com/v1 → openai
 */
function extractProvider(baseUrl: string | undefined): string {
  if (!baseUrl) return "unknown";
  try {
    const hostname = new URL(baseUrl).hostname;
    const parts = hostname.split(".");
    if (parts.length >= 2) return parts[parts.length - 2];
    return parts[0] || "unknown";
  } catch {
    return "unknown";
  }
}

function getProviderColor(provider: string): string {
  return PROVIDER_COLORS[provider] || "#888";
}

function getProviderDisplayName(provider: string): string {
  return PROVIDER_DISPLAY[provider] || provider.charAt(0).toUpperCase() + provider.slice(1);
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
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

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

  // Auto-focus search input when dropdown opens
  useEffect(() => {
    if (open && models.length >= 4) {
      requestAnimationFrame(() => searchRef.current?.focus());
    }
  }, [open, models.length]);

  const handleSwitch = async (name: string) => {
    if (name === currentModel || switching) return;
    setSwitching(true);
    setSwitchError(null);
    try {
      await apiPut("/models/active", { name });
      setCurrentModel(name);
      setOpen(false);
      fetchModels();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "切换失败";
      setSwitchError(msg);
      setTimeout(() => setSwitchError(null), 3000);
    } finally {
      setSwitching(false);
    }
  };

  const displayLabel = (m: ModelInfo) => m.name === "default" ? m.model : m.name;

  const filtered = useMemo(() => {
    if (!search.trim()) return models;
    const q = search.toLowerCase();
    return models.filter(
      (m) =>
        m.name.toLowerCase().includes(q) ||
        m.model.toLowerCase().includes(q) ||
        m.description?.toLowerCase().includes(q)
    );
  }, [models, search]);

  const groups = groupByProvider(filtered);
  const activeModel = models.find((m) => m.name === currentModel);
  const displayName = activeModel ? displayLabel(activeModel) : currentModel || "模型";
  const currentModelUnhealthy = currentModel && capsMap[currentModel]?.healthy === false;
  const activeProvider = activeModel ? extractProvider(activeModel.base_url) : "unknown";
  const showSearch = models.length >= 4;

  return (
    <DropdownMenu open={open} onOpenChange={(o) => { setOpen(o); if (!o) setSearch(""); }}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="gap-1.5 px-2.5 h-9 text-base font-semibold group">
          {/* Provider color indicator dot */}
          <span
            className="h-2 w-2 rounded-full shrink-0 transition-transform duration-200 group-hover:scale-125"
            style={{
              backgroundColor: currentModelUnhealthy
                ? "var(--em-error)"
                : getProviderColor(activeProvider),
              boxShadow: currentModelUnhealthy
                ? "0 0 6px var(--em-error)"
                : `0 0 6px ${getProviderColor(activeProvider)}40`,
            }}
          />
          <span
            className={`truncate max-w-[100px] sm:max-w-[200px] ${
              currentModelUnhealthy ? "text-destructive" : ""
            }`}
          >
            {displayName}
          </span>
          {switching ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5 text-muted-foreground transition-transform duration-200 group-data-[state=open]:rotate-180" />
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-80 max-w-[calc(100vw-2rem)] p-0 overflow-hidden"
      >
        {/* ── Header ── */}
        <div className="px-3 pt-2.5 pb-2 border-b border-border/50">
          <div className="flex items-center gap-2">
            <Sparkles className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />
            <span className="text-xs font-medium text-foreground/70">模型选择</span>
            <span className="text-[10px] text-muted-foreground/50 ml-auto tabular-nums">
              {models.length} 个可用
            </span>
          </div>
        </div>

        {/* ── Search ── */}
        {showSearch && (
          <div className="px-2 py-1.5 border-b border-border/30">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground/40" />
              <input
                ref={searchRef}
                type="text"
                placeholder="搜索模型..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full h-8 pl-8 pr-3 text-sm bg-muted/30 rounded-md border-0 outline-none placeholder:text-muted-foreground/40 focus:bg-muted/50 transition-colors"
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => e.stopPropagation()}
              />
            </div>
          </div>
        )}

        {/* ── Model list ── */}
        <div className="max-h-[50vh] overflow-y-auto py-1 model-selector-scroll">
          {groups.map((group, gi) => {
            const color = getProviderColor(group.provider);
            return (
              <div key={group.provider}>
                {gi > 0 && <div className="h-px mx-3 my-1 bg-border/30" />}
                {/* Provider header */}
                <div className="flex items-center gap-2 px-3 pt-2.5 pb-1">
                  <span
                    className="h-1.5 w-1.5 rounded-full shrink-0"
                    style={{ backgroundColor: color }}
                  />
                  <span
                    className="text-[10px] font-semibold uppercase tracking-widest"
                    style={{ color }}
                  >
                    {getProviderDisplayName(group.provider)}
                  </span>
                </div>
                {/* Model items */}
                {group.models.map((m) => {
                  const isSelected = m.name === currentModel;
                  const isUnhealthy = capsMap[m.name]?.healthy === false;
                  const hasHealthData = m.name in capsMap;
                  return (
                    <button
                      key={m.name}
                      onClick={() => handleSwitch(m.name)}
                      disabled={switching}
                      className={[
                        "w-full text-left px-3 py-2 flex items-center gap-3",
                        "transition-all duration-150 ease-out cursor-pointer",
                        "border-l-[3px] border-l-transparent",
                        "hover:bg-accent/50",
                        isSelected
                          ? "bg-[var(--em-primary-alpha-06)] !border-l-[var(--em-primary)]"
                          : "hover:border-l-[color:var(--em-primary-alpha-25)]",
                        switching ? "opacity-50 pointer-events-none" : "",
                      ].join(" ")}
                    >
                      {/* Health indicator dot */}
                      <span
                        className="h-2 w-2 rounded-full shrink-0 mt-0.5 transition-colors duration-200"
                        style={{
                          backgroundColor: isUnhealthy
                            ? "var(--em-error)"
                            : hasHealthData && capsMap[m.name]?.healthy === true
                              ? "var(--em-primary)"
                              : "var(--muted-foreground)",
                          opacity: hasHealthData ? 1 : 0.25,
                        }}
                      />
                      {/* Model info */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span
                            className={`text-sm leading-tight ${
                              isSelected ? "font-semibold" : "font-medium"
                            }`}
                          >
                            {displayLabel(m)}
                          </span>
                          {isUnhealthy && (
                            <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-px rounded-full bg-destructive/10 text-destructive font-medium">
                              <AlertTriangle className="h-2 w-2" />
                              不可用
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-1 mt-0.5">
                          {m.name !== "default" && m.name !== m.model && !isUnhealthy && (
                            <span className="text-[10px] text-muted-foreground/50 font-mono truncate">
                              {m.model}
                            </span>
                          )}
                          {m.description && (
                            <span className="text-[10px] text-muted-foreground/40 truncate">
                              {m.name !== "default" && m.name !== m.model && !isUnhealthy
                                ? `· ${m.description}`
                                : m.description}
                            </span>
                          )}
                        </div>
                      </div>
                      {/* Selection check */}
                      {isSelected && (
                        <span
                          className="h-5 w-5 rounded-full flex items-center justify-center shrink-0"
                          style={{ backgroundColor: "var(--em-primary-alpha-15)" }}
                        >
                          <Check className="h-3 w-3" style={{ color: "var(--em-primary)" }} />
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            );
          })}

          {/* Empty states */}
          {filtered.length === 0 && models.length > 0 && (
            <div className="px-3 py-8 text-center">
              <Search className="h-5 w-5 text-muted-foreground/25 mx-auto mb-2" />
              <p className="text-xs text-muted-foreground/40">未找到匹配的模型</p>
            </div>
          )}
          {models.length === 0 && (
            <div className="px-3 py-8 text-center">
              <Loader2 className="h-5 w-5 text-muted-foreground/25 mx-auto mb-2 animate-spin" />
              <p className="text-xs text-muted-foreground/40">加载模型列表...</p>
            </div>
          )}
        </div>

        {/* ── Error banner ── */}
        {switchError && (
          <div className="px-3 py-2 border-t border-destructive/20 bg-destructive/5 flex items-center gap-2">
            <AlertTriangle className="h-3 w-3 text-destructive shrink-0" />
            <span className="text-[11px] text-destructive truncate">{switchError}</span>
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
