"use client";

import { Brain, Check, Loader2, Save, CheckCircle2, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { THINKING_EFFORT_LEVELS, type ThinkingEffort } from "@/lib/thinking";
import { useUIStore } from "@/stores/ui-store";
import { useAdminModel } from "./admin-model-context";
import { ConfigTransferPanel } from "./ConfigTransferPanel";
import { ModelCapabilitiesPanel } from "./ModelCapabilitiesPanel";

export function AdvancedDiagnosticsPanel() {
  const {
    thinkingEffort,
    thinkingEffortOptions,
    setThinkingEffortOptions,
    thinkingBudget,
    setThinkingBudget,
    thinkingEffectiveBudget,
    thinkingSaving,
    thinkingSaved,
    handleSaveThinking,
  } = useAdminModel();
  const currentEffortLabel =
    THINKING_EFFORT_LEVELS.find(({ key }) => key === thinkingEffort)?.label ?? "中";

  const toggleEffortOption = (level: ThinkingEffort) => {
    if (thinkingEffortOptions.includes(level)) {
      if (thinkingEffortOptions.length === 1) return;
      setThinkingEffortOptions(thinkingEffortOptions.filter((item) => item !== level));
      return;
    }
    const selected = new Set([...thinkingEffortOptions, level]);
    setThinkingEffortOptions(
      THINKING_EFFORT_LEVELS.map(({ key }) => key).filter((key) => selected.has(key)),
    );
  };

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
                选择聊天区思考深度菜单中允许显示的等级；可多选，至少保留一个
              </p>
              <div>
                <div className="mb-1.5 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span>可调等级（多选）</span>
                  <span>当前：{currentEffortLabel}</span>
                </div>
                <div className="grid grid-cols-3 sm:grid-cols-7 gap-1.5 sm:gap-1">
                  {THINKING_EFFORT_LEVELS.map(({ key, label }) => {
                    const isActive = thinkingEffortOptions.includes(key);
                    return (
                      <button
                        key={key}
                        type="button"
                        aria-pressed={isActive}
                        className={`inline-flex items-center justify-center gap-1 px-2.5 py-2 sm:py-1 rounded-md text-xs font-medium transition-colors border disabled:cursor-not-allowed disabled:opacity-60 ${
                          isActive
                            ? "text-white border-transparent"
                            : "border-border text-muted-foreground hover:bg-muted/60"
                        }`}
                        style={isActive ? { backgroundColor: "var(--em-primary)" } : undefined}
                        onClick={() => toggleEffortOption(key)}
                        disabled={thinkingSaving || (isActive && thinkingEffortOptions.length === 1)}
                      >
                        {isActive && <Check className="h-3 w-3" aria-hidden />}
                        {label}
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
                    onClick={() => handleSaveThinking(thinkingEffortOptions, thinkingBudget)}
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
        <ConfigTransferPanel
          onImported={() => {
            useUIStore.getState().bumpModelProfiles();
          }}
        />
      </div>
    </div>
  );
}
