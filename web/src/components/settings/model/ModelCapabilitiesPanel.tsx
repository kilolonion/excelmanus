"use client";

import { Zap, Wrench, ImageIcon, Brain, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAdminModel } from "./admin-model-context";
import { CapabilityRow } from "./capability-widgets";

export function ModelCapabilitiesPanel() {
  const {
    config,
    capsMap,
    probingAll,
    handleProbeAll,
    handleCapToggle,
  } = useAdminModel();

  const activeProfile = config?.profiles?.find((p) => p.name === config.active) || config?.profiles?.[0];
  const activeCaps = activeProfile ? capsMap[activeProfile.name] : undefined;

  return (
            <div>
              <div className="flex items-center justify-between gap-2 mb-3">
                <div className="min-w-0">
                  <p className="text-xs text-muted-foreground break-all">
                    当前模型: {activeProfile?.model || "未配置"}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs gap-1 flex-shrink-0"
                  onClick={handleProbeAll}
                  disabled={probingAll}
                >
                  {probingAll ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Zap className="h-3 w-3" />
                  )}
                  {probingAll ? "探测中..." : "一键探测全部"}
                </Button>
              </div>

              {activeCaps ? (
                <div className="space-y-2">
                  <CapabilityRow
                    icon={<Wrench className="h-3.5 w-3.5" />}
                    label="工具调用 (Tool Calling)"
                    desc="模型是否支持 function calling"
                    value={activeCaps.supports_tool_calling}
                    error={activeCaps.probe_errors?.tool_calling}
                    onToggle={(v) => handleCapToggle(activeProfile!.name, activeProfile!.model, activeProfile!.base_url, "supports_tool_calling", v)}
                  />
                  <CapabilityRow
                    icon={<ImageIcon className="h-3.5 w-3.5" />}
                    label="图像识别 (Vision)"
                    desc="模型是否支持图片输入"
                    value={activeCaps.supports_vision}
                    error={activeCaps.probe_errors?.vision}
                    onToggle={(v) => handleCapToggle(activeProfile!.name, activeProfile!.model, activeProfile!.base_url, "supports_vision", v)}
                  />
                  <CapabilityRow
                    icon={<Brain className="h-3.5 w-3.5" />}
                    label="思考输出 (Thinking)"
                    desc={activeCaps.thinking_type ? `类型: ${activeCaps.thinking_type}` : "模型是否支持输出推理过程"}
                    value={activeCaps.supports_thinking}
                    error={activeCaps.probe_errors?.thinking}
                    onToggle={(v) => handleCapToggle(activeProfile!.name, activeProfile!.model, activeProfile!.base_url, "supports_thinking", v)}
                  />
                  {activeCaps.detected_at && (
                    <p className="text-[10px] text-muted-foreground mt-2">
                      上次探测: {new Date(activeCaps.detected_at).toLocaleString()}
                      {activeCaps.manual_override && (
                        <Badge variant="secondary" className="ml-1.5 text-[9px]">手动覆盖</Badge>
                      )}
                    </p>
                  )}
                </div>
              ) : (
                <div className="text-center py-6">
                  <p className="text-xs text-muted-foreground mb-2">
                    尚未探测模型能力
                  </p>
                  <Button
                    size="sm"
                    className="h-7 text-xs gap-1 text-white"
                    style={{ backgroundColor: "var(--em-primary)" }}
                    onClick={handleProbeAll}
                    disabled={probingAll}
                  >
                    {probingAll ? <Loader2 className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3" />}
                    一键探测全部
                  </Button>
                </div>
              )}
            </div>

  );
}
