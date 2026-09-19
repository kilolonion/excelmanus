"use client";

import { Brain, Loader2, Save, CheckCircle2, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAdminModel } from "./admin-model-context";
import { ConfigTransferPanel } from "./ConfigTransferPanel";
import { ModelCapabilitiesPanel } from "./ModelCapabilitiesPanel";

export function AdvancedDiagnosticsPanel() {
  const {
    config,
    thinkingEffort,
    setThinkingEffort,
    thinkingBudget,
    setThinkingBudget,
    thinkingEffectiveBudget,
    thinkingSaving,
    thinkingSaved,
    handleSaveThinking,
  } = useAdminModel();

  return (
    <div className="space-y-6">
      <div>
        <h3 className="font-semibold text-sm flex items-center gap-1.5 mb-2">
          <Zap className="h-4 w-4" style={{ color: "var(--em-primary)" }} />
          模型能力
        </h3>
        <ModelCapabilitiesPanel />
      </div>
      <div>
        <h3 className="font-semibold text-sm flex items-center gap-1.5 mb-2">
          <Brain className="h-4 w-4" style={{ color: "var(--em-primary)" }} />
          推理深度
        </h3>
            <div className="space-y-3">
              <p className="text-xs text-muted-foreground">
                控制模型思考链的深度，影响推理质量和 token 消耗
              </p>
              <div>
                <label className="text-xs text-muted-foreground mb-1.5 block">思考等级</label>
                <div className="grid grid-cols-3 sm:grid-cols-7 gap-1.5 sm:gap-1">
                  {(["none", "minimal", "low", "medium", "high", "xhigh", "max"] as const).map((level) => {
                    const labels: Record<string, string> = {
                      none: "关闭", minimal: "极简", low: "低",
                      medium: "中", high: "高", xhigh: "极高", max: "最深",
                    };
                    const isActive = thinkingEffort === level;
                    return (
                      <button
                        key={level}
                        className={`px-2.5 py-2 sm:py-1 rounded-md text-xs font-medium transition-colors border ${
                          isActive
                            ? "text-white border-transparent"
                            : "border-border text-muted-foreground hover:bg-muted/60"
                        }`}
                        style={isActive ? { backgroundColor: "var(--em-primary)" } : undefined}
                        onClick={() => {
                          setThinkingEffort(level);
                          handleSaveThinking(level, thinkingBudget);
                        }}
                        disabled={thinkingSaving}
                      >
                        {labels[level]}
                      </button>
                    );
                  })}
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1.5 block">
                  Token 预算（可选，留空则按等级自动换算）
                </label>
                <div className="flex flex-col sm:flex-row sm:items-center gap-2">
                  <Input
                    value={thinkingBudget}
                    onChange={(e) => setThinkingBudget(e.target.value.replace(/\D/g, ""))}
                    className="h-8 text-xs font-mono w-full sm:w-32"
                    placeholder="自动"
                    inputMode="numeric"
                  />
                  <Button
                    size="sm"
                    className="h-8 sm:h-7 text-xs gap-1 text-white flex-shrink-0"
                    style={{ backgroundColor: "var(--em-primary)" }}
                    onClick={() => handleSaveThinking(thinkingEffort, thinkingBudget)}
                    disabled={thinkingSaving}
                  >
                    {thinkingSaving ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : thinkingSaved ? (
                      <CheckCircle2 className="h-3 w-3" />
                    ) : (
                      <Save className="h-3 w-3" />
                    )}
                    {thinkingSaved ? "已保存" : "保存"}
                  </Button>
                </div>
                {thinkingEffectiveBudget > 0 && (
                  <p className="text-[10px] text-muted-foreground mt-1">
                    当前生效预算: {thinkingEffectiveBudget.toLocaleString()} tokens
                  </p>
                )}
              </div>
            </div>

      </div>
      <div>
        <ConfigTransferPanel config={config} />
      </div>
    </div>
  );
}
