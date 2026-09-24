"use client";

import { useEffect, useState, useCallback } from "react";
import { useConnectionStore } from "@/stores/connection-store";
import {
  Loader2,
  Save,
  CheckCircle2,
  Bot,
  Gauge,
  Shrink,
  History,
  Clock,
  Users,
  AlertCircle,
  BookOpen,
  Layers,
  Zap,
  MessageSquare,
  FileText,
  ChevronDown,
  Timer,
  Sparkles,
  Cpu,
  ArrowRight,
  SlidersHorizontal,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";

import { apiGet, apiPut } from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useUIStore } from "@/stores/ui-store";
import { DISPATCH_LABELS } from "@/stores/dispatch-store";
import { WorkbookChatSettings } from "./WorkbookChatSettings";
import { SettingsPageLayout, SettingsPagePanel } from "./SettingsPageLayout";

interface RuntimeConfig {
  // 会话
  session_ttl_seconds: number;
  max_sessions: number;
  max_consecutive_failures: number;
  turn_timeout_seconds: number;
  message_dispatch_default: "steer" | "interrupt" | "queue";
  responses_continuation_enabled: boolean;
  responses_background_enabled: boolean;
  turn_token_budget: number;
  turn_cost_budget_usd: number;
  input_cost_per_1k_usd: number;
  output_cost_per_1k_usd: number;
  // 执行与安全
  subagent_enabled: boolean;
  agent_self_management_enabled: boolean;
  friendly_error_messages: boolean;
  // 上下文与记忆
  max_context_tokens: number;
  memory_enabled: boolean;
  memory_expire_days: number;
  chat_history_enabled: boolean;
  // 记忆维护
  memory_maintenance_enabled: boolean;
  memory_maintenance_min_entries: number;
  memory_maintenance_new_threshold: number;
  memory_maintenance_interval_hours: number;
  memory_maintenance_model: string;
  // 压缩与缓存
  compaction_enabled: boolean;
  compaction_threshold_ratio: number;
  compaction_keep_recent_turns: number;
  compaction_max_summary_tokens: number;
  prompt_cache_key_enabled: boolean;
  // 子代理
  subagent_timeout_seconds: number;
  subagent_max_consecutive_failures: number;
  parallel_subagent_max: number;
  // LLM 重试
  llm_retry_max_attempts: number;
  llm_retry_base_delay_seconds: number;
  llm_retry_max_delay_seconds: number;
  // 视觉
  main_model_vision: string;
  image_pixel_budget: number | string;
  image_max_bytes: number;
  image_files_api: string;
  // 工具与 Hook
  tool_result_hard_cap_chars: number;
  parallel_readonly_tools: boolean;
  parallel_tool_max: number;
  hooks_command_enabled: boolean;
  hooks_command_timeout_seconds: number;
  hooks_output_max_chars: number;
  log_level: string;
  // 代码策略
  code_policy_enabled: boolean;
  code_policy_green_auto_approve: boolean;
  code_policy_yellow_auto_approve: boolean;
  tool_schema_validation_mode: string;
  tool_schema_validation_canary_percent: number;
  tool_schema_strict_path: boolean;
  // 技能发现
  skills_context_char_budget: number;
  skills_discovery_enabled: boolean;
  skills_discovery_scan_workspace_ancestors: boolean;
  skills_discovery_include_agents: boolean;
  skills_discovery_scan_external_tool_dirs: boolean;
  jev_experimental_enabled: boolean;
}

interface SelectOption {
  value: string;
  label: string;
}

interface ToggleItem {
  key: keyof RuntimeConfig;
  label: string;
  desc: string;
  icon: React.ReactNode;
  coachId?: string;
  type: "bool" | "int" | "float" | "select" | "string";
  options?: SelectOption[];
  min?: number;
  max?: number;
}

interface ItemGroup {
  title: string;
  icon: React.ReactNode;
  items: ToggleItem[];
}

const BASIC_GROUPS: ItemGroup[] = [
  {
    title: "对话与上下文",
    icon: <Layers className="h-3.5 w-3.5" />,
    items: [
      {
        key: "message_dispatch_default",
        label: "执行中消息的发送方式",
        desc: "引导当前任务：下一步采用新要求；中断并发送：停止当前任务并等待操作收尾；排队发送：当前任务完成后处理。",
        icon: <MessageSquare className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "steer", label: DISPATCH_LABELS.steer },
          { value: "interrupt", label: DISPATCH_LABELS.interrupt },
          { value: "queue", label: DISPATCH_LABELS.queue },
        ],
      },
      {
        key: "max_context_tokens",
        label: "默认上下文窗口",
        desc: "对话可用的 token 上限。保存后立即同步到已打开的对话并锁定；未保存时按当前模型自动推断。",
        icon: <Layers className="h-4 w-4" />,
        type: "int",
        min: 1000,
        max: 10000000,
      },
      {
        key: "compaction_enabled",
        label: "上下文压缩",
        desc: "占用超过窗口乘以阈值时，自动摘要旧消息并保留最近几轮。保存后立即同步到已打开的对话。",
        icon: <Shrink className="h-4 w-4" />,
        coachId: "coach-settings-runtime-compaction",
        type: "bool",
      },
      {
        key: "chat_history_enabled",
        label: "聊天记录持久化",
        desc: "将会话写入本地数据库以便下次恢复。关闭后服务端不再保存或恢复。保存后将重启服务。",
        icon: <MessageSquare className="h-4 w-4" />,
        type: "bool",
      },
    ],
  },
  {
    title: "能力",
    icon: <Zap className="h-3.5 w-3.5" />,
    items: [
      {
        key: "agent_self_management_enabled",
        label: "Agent 自我管理",
        desc: "默认开启。提供自我管理技能及查询、配置工具，可查看自身能力并调整当前对话的推理、上下文和工具开关。仅影响当前对话，不修改密钥或审批权限；保存开关后立即生效。",
        icon: <SlidersHorizontal className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "subagent_enabled",
        label: "子代理",
        desc: "允许主模型把子任务委派出去。未指定名称时用通用子代理；只读探查需显式指定 explorer。关闭后工具仍可见，但执行会被拒绝。已打开的对话需新开，或使用 /subagent on|off。",
        icon: <Bot className="h-4 w-4" />,
        type: "bool",
      },
    ],
  },
];

const ADVANCED_GROUPS: ItemGroup[] = [
  {
    title: "会话与熔断",
    icon: <Users className="h-3.5 w-3.5" />,
    items: [
      {
        key: "max_sessions",
        label: "内存会话上限",
        desc: "同时留在内存里的会话数量上限（含正在创建的）。超出后无法新建。不影响历史记录。保存后将重启服务。",
        icon: <Users className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 10000,
      },
      {
        key: "session_ttl_seconds",
        label: "空闲会话回收",
        desc: "内存中空闲且未在处理的会话，超过此秒数后从内存移除（历史仍保留）。保存后将重启服务。",
        icon: <Clock className="h-4 w-4" />,
        type: "int",
        min: 60,
        max: 86400,
      },
      {
        key: "max_consecutive_failures",
        label: "工具连续失败上限",
        desc: "同一条用户消息处理中，连续工具失败达到此次数后结束本轮剩余工具。成功一次即重新计数。不含模型调用失败，也不作用于子代理。已打开的对话需新开后生效。",
        icon: <AlertCircle className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 50,
      },
      {
        key: "friendly_error_messages",
        label: "友好错误消息",
        desc: "将接口返回的内部错误改成更易读的提示（如 404 / 429 / 500）。不影响对话里的工具错误。",
        icon: <AlertCircle className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "log_level",
        label: "日志级别",
        desc: "后端模块日志详细程度。保存后立即生效，不影响访问日志。",
        icon: <FileText className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "DEBUG", label: "DEBUG" },
          { value: "INFO", label: "INFO" },
          { value: "WARNING", label: "WARNING" },
          { value: "ERROR", label: "ERROR" },
          { value: "CRITICAL", label: "CRITICAL" },
        ],
      },
      {
        key: "turn_timeout_seconds",
        label: "单轮 wall-clock 上限",
        desc: "限制一条用户消息的总执行时间（秒）。0 表示不限制；模型重试、工具、Code Mode 和同步子代理共用此上限。",
        icon: <Timer className="h-4 w-4" />,
        type: "int",
        min: 0,
        max: 86400,
      },
    ],
  },
  {
    title: "上下文压缩",
    icon: <Shrink className="h-3.5 w-3.5" />,
    items: [
      {
        key: "compaction_threshold_ratio",
        label: "压缩阈值比例",
        desc: "占用超过窗口乘以此比例时触发自动压缩。保存后立即同步到已打开的对话。",
        icon: <Gauge className="h-4 w-4" />,
        type: "float",
      },
      {
        key: "compaction_keep_recent_turns",
        label: "压缩保留轮次",
        desc: "压缩时保留的最近对话轮数。已打开的对话需新开后生效。",
        icon: <History className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 20,
      },
      {
        key: "compaction_max_summary_tokens",
        label: "压缩摘要上限",
        desc: "压缩摘要的最大 token 数。已打开的对话需新开后生效。",
        icon: <Shrink className="h-4 w-4" />,
        type: "int",
        min: 100,
        max: 10000,
      },
    ],
  },
  {
    title: "子代理",
    icon: <Bot className="h-3.5 w-3.5" />,
    items: [
      {
        key: "subagent_timeout_seconds",
        label: "子代理超时",
        desc: "单个子代理同步执行的最长等待时间（秒）。超时后终止并返回。已打开的对话需新开后生效。",
        icon: <Timer className="h-4 w-4" />,
        type: "int",
        min: 10,
        max: 3600,
      },
      {
        key: "parallel_subagent_max",
        label: "并行子代理上限",
        desc: "单次并行委派的子任务数量上限。两个写入子代理不能同时跑。",
        icon: <Layers className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 10,
      },
      {
        key: "subagent_max_consecutive_failures",
        label: "子代理连续失败上限",
        desc: "自定义子代理未指定时使用此默认值。内置通用子代理和 explorer 固定为 3，不受此项影响。",
        icon: <AlertCircle className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 50,
      },
    ],
  },
  {
    title: "工具执行",
    icon: <Zap className="h-3.5 w-3.5" />,
    items: [
      {
        key: "parallel_readonly_tools",
        label: "只读工具并发",
        desc: "同一轮回复中，无依赖的只读工具可以并发。写入和多数外部工具始终串行。已打开的对话需新开后生效。",
        icon: <Zap className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "parallel_tool_max",
        label: "只读工具并发上限",
        desc: "同一批最多同时运行的只读工具数，其余排队。可设置 1–32；新开对话后生效。",
        icon: <Layers className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 32,
      },
      {
        key: "tool_result_hard_cap_chars",
        label: "工具结果全局截断",
        desc: "工具返回内容的全局字符上限。0 表示关闭这一层；各工具自己的上限和上下文压缩仍可能截断。已打开的对话需新开后生效。",
        icon: <Shrink className="h-4 w-4" />,
        type: "int",
        min: 0,
        max: 100000,
      },
    ],
  },
];

interface GuideSection {
  key: "wizard" | "basic" | "advanced" | "settings";
  category: string;
  categoryColor: string;
  categoryBg: string;
  title: string;
  description: string;
  icon: React.ReactNode;
}

const GUIDE_SECTIONS: GuideSection[] = [
  {
    key: "wizard",
    category: "模型添加",
    categoryColor: "text-blue-600 dark:text-blue-400",
    categoryBg: "bg-blue-50 dark:bg-blue-950/40",
    title: "模型配置向导",
    description: "重新配置 AI 模型提供商、API 密钥和连接参数",
    icon: <Cpu className="h-4 w-4" />,
  },
  {
    key: "basic",
    category: "基础",
    categoryColor: "text-emerald-600 dark:text-emerald-400",
    categoryBg: "bg-emerald-50 dark:bg-emerald-950/40",
    title: "对话与任务",
    description: "练习工作区切换、文件引用、发送与暂停、对话模式和模型选择",
    icon: <BookOpen className="h-4 w-4" />,
  },
  {
    key: "advanced",
    category: "进阶",
    categoryColor: "text-amber-600 dark:text-amber-400",
    categoryBg: "bg-amber-50 dark:bg-amber-950/40",
    title: "文件与表格",
    description: "练习快捷命令、工作表切换、单元格引用、修改对比和文件类型选择",
    icon: <Sparkles className="h-4 w-4" />,
  },
  {
    key: "settings",
    category: "设置",
    categoryColor: "text-violet-600 dark:text-violet-400",
    categoryBg: "bg-violet-50 dark:bg-violet-950/40",
    title: "模型与扩展",
    description: "了解供应商、任务模型、订阅授权、规则、技能、MCP、记忆和系统设置",
    icon: <SlidersHorizontal className="h-4 w-4" />,
  },
];

function OnboardingReplayCard() {
  const { wizardCompleted, coachMarksCompleted, resetToPhase } =
    useOnboardingStore();
  const closeSettings = useUIStore((s) => s.closeSettings);
  const [expanded, setExpanded] = useState(false);

  const handleReplayFrom = useCallback(
    (target: "wizard" | "basic" | "advanced" | "settings") => {
      resetToPhase(target);
      closeSettings();
    },
    [resetToPhase, closeSettings]
  );

  return (
    <div className="rounded-lg border border-border overflow-hidden">
      {/* Header */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between gap-3 p-4 hover:bg-muted/40 transition-colors"
      >
        <div className="flex items-start gap-2.5 min-w-0 text-left">
          <span
            className="mt-0.5 flex-shrink-0 w-7 h-7 rounded-md flex items-center justify-center"
            style={{ backgroundColor: "var(--em-primary-alpha-10)", color: "var(--em-primary)" }}
          >
            <BookOpen className="h-3.5 w-3.5" />
          </span>
          <div className="min-w-0">
            <div className="text-sm font-medium">新手引导</div>
            <div className="text-[11px] sm:text-xs text-muted-foreground">
              {wizardCompleted && coachMarksCompleted
                ? "已完成引导。展开选择章节重新播放。"
                : wizardCompleted
                  ? "配置向导已完成，界面指引进行中。"
                  : "尚未完成引导。"}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <span className="text-[11px] text-muted-foreground hidden sm:inline">
            重新引导
          </span>
          <ChevronDown
            className={`h-4 w-4 text-muted-foreground transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
          />
        </div>
      </button>

      {/* Expandable drawer */}
      {expanded && (
        <div className="border-t border-border bg-muted/20 px-3 pb-3 pt-2 space-y-2">
          <p className="text-[11px] text-muted-foreground px-1 mb-1">
            选择要进入的章节
          </p>
          {GUIDE_SECTIONS.map((section) => (
            <button
              key={section.key}
              type="button"
              onClick={() => handleReplayFrom(section.key)}
              className="w-full flex items-center gap-3 rounded-lg border border-border bg-background p-3 text-left hover:border-[var(--em-primary)] hover:shadow-sm transition-all group"
            >
              <span
                className={`flex-shrink-0 w-9 h-9 rounded-lg flex items-center justify-center ${section.categoryBg} ${section.categoryColor}`}
              >
                {section.icon}
              </span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-sm font-medium group-hover:text-[var(--em-primary)] transition-colors">
                    {section.title}
                  </span>
                  <span
                    className={`text-[10px] font-semibold px-1.5 py-px rounded-full ${section.categoryBg} ${section.categoryColor}`}
                  >
                    {section.category}
                  </span>
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed break-words">
                  {section.description}
                </p>
              </div>
              <ArrowRight className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0 transition-all" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function RuntimeTab() {
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [draft, setDraft] = useState<Partial<RuntimeConfig>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const triggerRestart = useConnectionStore((s) => s.triggerRestart);
  const setMessageDispatchDefault = useUIStore((s) => s.setMessageDispatchDefault);

  const fetchConfig = useCallback(async (force = false) => {
    if (!force) {
      const cached = settingsCache.get<RuntimeConfig>("/config/runtime");
      if (cached) {
        setConfig(cached);
        setDraft({});
        if (cached.message_dispatch_default) {
          setMessageDispatchDefault(cached.message_dispatch_default);
        }
        return;
      }
    }
    setLoading(true);
    try {
      const data = await apiGet<RuntimeConfig>("/config/runtime");
      settingsCache.set("/config/runtime", data);
      setConfig(data);
      setDraft({});
      if (data.message_dispatch_default) {
        setMessageDispatchDefault(data.message_dispatch_default);
      }
    } catch {
      // 后端未就绪或未授权
    } finally {
      setLoading(false);
    }
  }, [setMessageDispatchDefault]);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const merged = config
    ? { ...config, ...draft }
    : null;

  const hasChanges = Object.keys(draft).length > 0;

  const handleSave = async () => {
    if (!hasChanges) return;
    setSaving(true);
    try {
      const res = await apiPut<{ restarting?: boolean; restart_reason?: string }>("/config/runtime", draft);
      if (res?.restarting) {
        setSaving(false);
        triggerRestart(res.restart_reason || "配置已更新");
        return;
      }
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      await fetchConfig(true);
    } catch {
      // 忽略
    } finally {
      setSaving(false);
    }
  };

  if (loading && !config) {
    return (
      <SettingsPageLayout className="space-y-5">
        <SettingsPagePanel><WorkbookChatSettings /></SettingsPagePanel>
        <div className="flex items-center justify-center py-12 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin mr-2" />
          加载配置…
        </div>
      </SettingsPageLayout>
    );
  }

  if (!merged) {
    return (
      <SettingsPageLayout className="space-y-5">
        <SettingsPagePanel><WorkbookChatSettings /></SettingsPagePanel>
        <div className="text-center py-12 text-muted-foreground text-sm">
          无法获取系统配置
        </div>
      </SettingsPageLayout>
    );
  }

  const renderGroups = (groups: ItemGroup[]) =>
    groups.map((group) => (
      <section key={group.title} className="em-settings-runtime-group">
        <div className="flex items-center gap-1.5 mb-2.5">
          <span style={{ color: "var(--em-primary)" }}>{group.icon}</span>
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            {group.title}
          </h3>
        </div>
        <div className="space-y-3">
          {group.items.map((item, index) => {
            const value = merged[item.key];
            return (
              <div key={item.key} data-coach-id={item.coachId}>
                {item.type === "bool" ? (
                  /* Boolean toggle: always horizontal */
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-start gap-2.5 flex-1 min-w-0">
                      <span className="mt-0.5 text-muted-foreground flex-shrink-0">{item.icon}</span>
                      <div className="min-w-0">
                        <div className="text-sm font-medium">{item.label}</div>
                        <div className="text-[11px] sm:text-xs text-muted-foreground">{item.desc}</div>
                      </div>
                    </div>
                    <Switch
                      checked={value as boolean}
                      onCheckedChange={(checked: boolean) =>
                        setDraft((prev) => ({ ...prev, [item.key]: checked }))
                      }
                      className="flex-shrink-0"
                    />
                  </div>
                ) : (
                  /* Select / Number: stack vertically on mobile */
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 sm:gap-4">
                    <div className="flex items-start gap-2.5 flex-1 min-w-0">
                      <span className="mt-0.5 text-muted-foreground flex-shrink-0">{item.icon}</span>
                      <div className="min-w-0">
                        <div className="text-sm font-medium">{item.label}</div>
                        <div className="text-[11px] sm:text-xs text-muted-foreground">{item.desc}</div>
                      </div>
                    </div>
                    {item.type === "select" && item.options ? (
                      <select
                        aria-label={item.label}
                        className="w-full sm:w-32 h-9 sm:h-8 text-sm rounded-md border border-input bg-background px-2 flex-shrink-0 ml-0 sm:ml-auto"
                        value={value as string}
                        onChange={(e) =>
                          setDraft((prev) => ({ ...prev, [item.key]: e.target.value }))
                        }
                      >
                        {item.options.map((opt) => (
                          <option key={opt.value} value={opt.value}>
                            {opt.label}
                          </option>
                        ))}
                      </select>
                    ) : item.type === "string" ? (
                      <Input
                        type="text"
                        className="w-full sm:w-40 h-9 sm:h-8 text-xs font-mono flex-shrink-0"
                        value={(value as string) || ""}
                        placeholder={item.desc}
                        onChange={(e) =>
                          setDraft((prev) => ({ ...prev, [item.key]: e.target.value }))
                        }
                      />
                    ) : (
                      <Input
                        type="number"
                        className="w-full sm:w-24 h-9 sm:h-8 text-sm text-right flex-shrink-0"
                        step={item.type === "float" ? 0.05 : 1}
                        min={item.min ?? (item.type === "float" ? 0 : 1)}
                        max={item.max ?? (item.type === "float" ? 1 : 500)}
                        value={value as number}
                        onChange={(e) => {
                          const v =
                            item.type === "float"
                              ? parseFloat(e.target.value)
                              : parseInt(e.target.value, 10);
                          if (!isNaN(v)) {
                            setDraft((prev) => ({ ...prev, [item.key]: v }));
                          }
                        }}
                      />
                    )}
                  </div>
                )}
                {index < group.items.length - 1 && <Separator className="mt-3" />}
              </div>
            );
          })}
        </div>
        <div className="h-2" />
      </section>
    ));

  return (
    <SettingsPageLayout className="space-y-5">
      <SettingsPagePanel><WorkbookChatSettings /></SettingsPagePanel>
      <SettingsPagePanel className="em-settings-runtime-panel">
        {renderGroups(BASIC_GROUPS)}

        <button
          onClick={() => setShowAdvanced((prev) => !prev)}
          className="flex items-center gap-1.5 w-full text-left px-4 py-3 border-t border-border/60 group"
          data-coach-id="coach-settings-advanced-toggle"
        >
          <ChevronDown
            className={`h-3.5 w-3.5 text-muted-foreground transition-transform duration-200 ${showAdvanced ? "" : "-rotate-90"}`}
          />
          <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider group-hover:text-foreground transition-colors">
            高级设置
          </span>
        </button>

        {showAdvanced && renderGroups(ADVANCED_GROUPS)}

        <div className="em-settings-card-footer">
          <Button
            size="sm"
            disabled={!hasChanges || saving}
            onClick={handleSave}
            className="gap-1.5"
          >
            {saving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : saved ? (
              <CheckCircle2 className="h-3.5 w-3.5" />
            ) : (
              <Save className="h-3.5 w-3.5" />
            )}
            {saved ? "已保存" : "保存"}
          </Button>
        </div>
      </SettingsPagePanel>
      <OnboardingReplayCard />
    </SettingsPageLayout>
  );
}
