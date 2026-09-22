"use client";

import { ArrowRight, Brain, Check, Clock, Eye, Gauge, Loader2, RotateCcw, Save, CheckCircle2, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { THINKING_EFFORT_LEVELS, type ThinkingEffort } from "@/lib/thinking";
import { useUIStore } from "@/stores/ui-store";
import { useAdminModel } from "./admin-model-context";
import { ConfigTransferPanel } from "./ConfigTransferPanel";
import { ModelCapabilitiesPanel } from "./ModelCapabilitiesPanel";
import { RuntimeSettingsPanel, type RuntimeSettingGroup } from "../RuntimeSettingsPanel";

const MODEL_RUNTIME_SETTING_GROUPS: RuntimeSettingGroup[] = [
  {
    title: "模型请求",
    description: "控制模型能力判断、Responses 续接与提示词缓存等请求行为。",
    icon: <Zap className="h-4 w-4" />,
    items: [
      {
        key: "main_model_vision",
        label: "图片识别",
        desc: "自动时按模型能力判断；也可为当前对话统一开启或关闭图片输入。",
        icon: <Eye className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "auto", label: "自动" },
          { value: "true", label: "开启" },
          { value: "false", label: "关闭" },
        ],
      },
      {
        key: "responses_continuation_enabled",
        label: "Responses 原生续接",
        desc: "使用 previous_response_id 续接响应，让后续请求只发送新增输入。",
        icon: <ArrowRight className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "responses_background_enabled",
        label: "Responses 后台响应",
        desc: "使用后台响应并轮询到终态，适合耗时较长的模型任务。",
        icon: <Clock className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "prompt_cache_key_enabled",
        label: "提示词缓存",
        desc: "向模型接口发送缓存键，提高重复提示词的缓存命中率。",
        icon: <Zap className="h-4 w-4" />,
        type: "bool",
      },
    ],
  },
  {
    title: "单轮预算",
    description: "限制一次任务使用的 token 与估算成本；0 表示不限制。",
    icon: <Gauge className="h-4 w-4" />,
    defaultOpen: false,
    items: [
      {
        key: "turn_token_budget",
        label: "单轮 token 上限",
        desc: "限制本轮模型输入与输出 token 总数。",
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 0,
        max: 10000000,
      },
      {
        key: "turn_cost_budget_usd",
        label: "单轮成本上限",
        desc: "限制本轮累计模型成本（美元）。",
        icon: <Gauge className="h-4 w-4" />,
        type: "float",
        min: 0,
        max: 100000,
      },
      {
        key: "input_cost_per_1k_usd",
        label: "输入 token 估算单价",
        desc: "供应商未返回成本时，每 1K 输入 token 的估算美元单价。",
        icon: <Gauge className="h-4 w-4" />,
        type: "float",
        min: 0,
        max: 1000,
      },
      {
        key: "output_cost_per_1k_usd",
        label: "输出 token 估算单价",
        desc: "供应商未返回成本时，每 1K 输出 token 的估算美元单价。",
        icon: <Gauge className="h-4 w-4" />,
        type: "float",
        min: 0,
        max: 1000,
      },
    ],
  },
  {
    title: "失败重试",
    description: "设置模型请求遇到限流或网络错误时的重试次数和退避时间。",
    icon: <RotateCcw className="h-4 w-4" />,
    defaultOpen: false,
    items: [
      {
        key: "llm_retry_max_attempts",
        label: "最大尝试次数",
        desc: "模型调用失败时的总尝试次数，包含首次调用。",
        icon: <RotateCcw className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 10,
      },
      {
        key: "llm_retry_base_delay_seconds",
        label: "重试基准延迟",
        desc: "指数退避的起始等待时间（秒）。",
        icon: <Clock className="h-4 w-4" />,
        type: "float",
        min: 0,
        max: 300,
      },
      {
        key: "llm_retry_max_delay_seconds",
        label: "重试最大延迟",
        desc: "单次重试等待上限（秒）；Retry-After 仍会优先采用。",
        icon: <Clock className="h-4 w-4" />,
        type: "float",
        min: 0,
        max: 3600,
      },
    ],
  },
  {
    title: "图片传输",
    description: "限制发送给模型的图片尺寸与编码体积，并选择 Files API 的使用方式。",
    icon: <Eye className="h-4 w-4" />,
    defaultOpen: false,
    items: [
      {
        key: "image_pixel_budget",
        label: "请求像素预算",
        desc: "填写总像素上限，或输入 low 使用 512×512。",
        icon: <Eye className="h-4 w-4" />,
        type: "string",
      },
      {
        key: "image_max_bytes",
        label: "请求编码上限",
        desc: "单张请求图片的编码字节上限；超出时会自动降低质量。",
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 1024,
        max: 20971520,
      },
      {
        key: "image_files_api",
        label: "Files API 传输",
        desc: "自动时仅向明确支持的端点上传 file_id，失败会回退为内联图片。",
        icon: <Eye className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "auto", label: "自动" },
          { value: "true", label: "强制开启" },
          { value: "false", label: "关闭" },
        ],
      },
    ],
  },
];

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
      <RuntimeSettingsPanel groups={MODEL_RUNTIME_SETTING_GROUPS} />
    </div>
  );
}
