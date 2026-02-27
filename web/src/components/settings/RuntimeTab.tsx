"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Loader2,
  Save,
  CheckCircle2,
  Shield,
  ShieldOff,
  Bot,
  FolderArchive,
  RotateCcw,
  Gauge,
  Shrink,
  History,
  Lock,
  Clock,
  Users,
  AlertCircle,
  Brain,
  BookOpen,
  Layers,
  Eye,
  ScanEye,
  Zap,
  MessageSquare,
  Terminal,
  FileText,
  ChevronDown,
  Timer,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { apiGet, apiPut } from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";
import { useAuthStore } from "@/stores/auth-store";
import { useAuthConfigStore } from "@/stores/auth-config-store";

interface RuntimeConfig {
  auth_enabled: boolean;
  subagent_enabled: boolean;
  backup_enabled: boolean;
  checkpoint_enabled: boolean;
  external_safe_mode: boolean;
  max_iterations: number;
  compaction_enabled: boolean;
  compaction_threshold_ratio: number;
  code_policy_enabled: boolean;
  guard_mode: string;
  session_ttl_seconds: number;
  max_sessions: number;
  max_consecutive_failures: number;
  memory_enabled: boolean;
  memory_auto_extract_interval: number;
  max_context_tokens: number;
  summarization_enabled: boolean;
  window_perception_enabled: boolean;
  vlm_enhance: boolean;
  main_model_vision: string;
  parallel_readonly_tools: boolean;
  chat_history_enabled: boolean;
  hooks_command_enabled: boolean;
  log_level: string;
  tool_schema_validation_mode: string;
  tool_schema_validation_canary_percent: number;
  tool_schema_strict_path: boolean;
  thinking_effort: string;
  thinking_budget: number;
  subagent_max_iterations: number;
  subagent_timeout_seconds: number;
  parallel_subagent_max: number;
  prompt_cache_key_enabled: boolean;
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
  type: "bool" | "int" | "float" | "select";
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
    title: "会话与多用户",
    icon: <Users className="h-3.5 w-3.5" />,
    items: [
      {
        key: "auth_enabled",
        label: "启用多用户认证",
        desc: "开启用户注册/登录与会话隔离（修改后需重启生效）",
        icon: <Lock className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "max_sessions",
        label: "最大会话数",
        desc: "系统允许的最大并发会话数量",
        icon: <Users className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 10000,
      },
      {
        key: "session_ttl_seconds",
        label: "会话超时",
        desc: "会话无活动后自动过期的时间（秒）",
        icon: <Clock className="h-4 w-4" />,
        type: "int",
        min: 60,
        max: 86400,
      },
      {
        key: "max_consecutive_failures",
        label: "最大连续失败",
        desc: "连续工具调用失败达到此次数后停止",
        icon: <AlertCircle className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 50,
      },
    ],
  },
  {
    title: "执行与安全",
    icon: <Shield className="h-3.5 w-3.5" />,
    items: [
      {
        key: "subagent_enabled",
        label: "子代理",
        desc: "启用 Explorer / Verifier 等子代理",
        icon: <Bot className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "backup_enabled",
        label: "备份沙盒",
        desc: "文件操作前自动创建备份副本",
        icon: <FolderArchive className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "checkpoint_enabled",
        label: "轮次快照",
        desc: "每轮工具调用后自动快照被修改文件，支持按轮回退",
        icon: <History className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "max_iterations",
        label: "最大迭代次数",
        desc: "单轮对话中工具调用循环上限",
        icon: <RotateCcw className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 500,
      },
      {
        key: "guard_mode",
        label: "门禁模式",
        desc: "off：关闭执行守卫/写入门禁；soft：仅记录诊断不强制继续",
        icon: <ShieldOff className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "off", label: "关闭 (off)" },
          { value: "soft", label: "软提示 (soft)" },
        ],
      },
    ],
  },
  {
    title: "上下文与记忆",
    icon: <Layers className="h-3.5 w-3.5" />,
    items: [
      {
        key: "max_context_tokens",
        label: "上下文窗口",
        desc: "最大上下文 token 数（模型窗口大小）",
        icon: <Layers className="h-4 w-4" />,
        type: "int",
        min: 1000,
        max: 10000000,
      },
      {
        key: "compaction_enabled",
        label: "上下文压缩",
        desc: "Token 超阈值时自动摘要压缩",
        icon: <Shrink className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "summarization_enabled",
        label: "对话摘要",
        desc: "超阈值时用辅助模型压缩早期对话（需配置 aux_model）",
        icon: <BookOpen className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "memory_enabled",
        label: "跨会话记忆",
        desc: "启用跨会话持久记忆功能",
        icon: <Brain className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "chat_history_enabled",
        label: "聊天记录持久化",
        desc: "将聊天记录保存到数据库",
        icon: <MessageSquare className="h-4 w-4" />,
        type: "bool",
      },
    ],
  },
  {
    title: "感知与视觉",
    icon: <Eye className="h-3.5 w-3.5" />,
    items: [
      {
        key: "window_perception_enabled",
        label: "窗口感知",
        desc: "启用 Excel 窗口感知层，智能管理表格上下文",
        icon: <Eye className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "vlm_enhance",
        label: "VLM 增强",
        desc: "启用视觉语言模型增强描述（图片表格提取）",
        icon: <ScanEye className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "main_model_vision",
        label: "主模型视觉",
        desc: "主模型视觉能力：auto 自动检测 / true 强制开启 / false 关闭",
        icon: <ScanEye className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "auto", label: "自动 (auto)" },
          { value: "true", label: "开启 (true)" },
          { value: "false", label: "关闭 (false)" },
        ],
      },
    ],
  },
];

const ADVANCED_GROUPS: ItemGroup[] = [
  {
    title: "推理配置",
    icon: <Brain className="h-3.5 w-3.5" />,
    items: [
      {
        key: "thinking_effort",
        label: "推理深度",
        desc: "模型推理思考的深度等级",
        icon: <Brain className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "none", label: "关闭 (none)" },
          { value: "minimal", label: "最小 (minimal)" },
          { value: "low", label: "低 (low)" },
          { value: "medium", label: "中等 (medium)" },
          { value: "high", label: "高 (high)" },
          { value: "xhigh", label: "极高 (xhigh)" },
        ],
      },
      {
        key: "thinking_budget",
        label: "推理 Token 预算",
        desc: "精确推理 token 预算（>0 时覆盖推理深度换算值）",
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 0,
        max: 100000,
      },
    ],
  },
  {
    title: "子代理",
    icon: <Bot className="h-3.5 w-3.5" />,
    items: [
      {
        key: "subagent_max_iterations",
        label: "子代理最大迭代",
        desc: "单个子代理的工具调用循环上限",
        icon: <RotateCcw className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 500,
      },
      {
        key: "subagent_timeout_seconds",
        label: "子代理超时",
        desc: "单个子代理执行超时时间（秒）",
        icon: <Timer className="h-4 w-4" />,
        type: "int",
        min: 10,
        max: 3600,
      },
      {
        key: "parallel_subagent_max",
        label: "并行子代理上限",
        desc: "最大并发子代理数量",
        icon: <Layers className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 10,
      },
    ],
  },
  {
    title: "压缩与缓存",
    icon: <Shrink className="h-3.5 w-3.5" />,
    items: [
      {
        key: "compaction_threshold_ratio",
        label: "压缩阈值比例",
        desc: "Token 使用率超过此比例触发自动压缩 (0-1)",
        icon: <Gauge className="h-4 w-4" />,
        type: "float",
      },
      {
        key: "memory_auto_extract_interval",
        label: "记忆提取间隔",
        desc: "每 N 轮自动提取记忆（0 = 禁用）",
        icon: <Brain className="h-4 w-4" />,
        type: "int",
        min: 0,
        max: 100,
      },
      {
        key: "prompt_cache_key_enabled",
        label: "提示词缓存",
        desc: "向 API 发送缓存键提升 prompt 缓存命中率",
        icon: <Zap className="h-4 w-4" />,
        type: "bool",
      },
    ],
  },
  {
    title: "安全与策略",
    icon: <Shield className="h-3.5 w-3.5" />,
    items: [
      {
        key: "external_safe_mode",
        label: "安全模式",
        desc: "过滤 SSE 中的内部事件（工具调用/思考等）",
        icon: <Shield className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "code_policy_enabled",
        label: "代码策略",
        desc: "启用代码安全策略引擎（沙盒限制）",
        icon: <Shield className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "tool_schema_validation_mode",
        label: "Schema 校验",
        desc: "工具参数结构校验模式",
        icon: <Shield className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "off", label: "关闭 (off)" },
          { value: "shadow", label: "影子 (shadow)" },
          { value: "enforce", label: "强制 (enforce)" },
        ],
      },
      {
        key: "tool_schema_validation_canary_percent",
        label: "Schema 校验灰度",
        desc: "Schema 校验生效的请求百分比 (0-100)",
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 0,
        max: 100,
      },
      {
        key: "tool_schema_strict_path",
        label: "Schema 严格路径",
        desc: "启用工具参数路径的严格校验",
        icon: <Shield className="h-4 w-4" />,
        type: "bool",
      },
    ],
  },
  {
    title: "工具与系统",
    icon: <Zap className="h-3.5 w-3.5" />,
    items: [
      {
        key: "parallel_readonly_tools",
        label: "只读工具并发",
        desc: "同一轮次中相邻只读工具并发执行",
        icon: <Zap className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "hooks_command_enabled",
        label: "Hook 命令",
        desc: "启用外部命令 Hook（工具调用后触发自定义脚本）",
        icon: <Terminal className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "log_level",
        label: "日志级别",
        desc: "后端日志输出级别",
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
    ],
  },
];

export function RuntimeTab() {
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [draft, setDraft] = useState<Partial<RuntimeConfig>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [restarting, setRestarting] = useState(false);

  const user = useAuthStore((s) => s.user);
  const authEnabled = useAuthConfigStore((s) => s.authEnabled);
  const isAdmin = !authEnabled || !user || user.role === "admin";

  const fetchConfig = useCallback(async (force = false) => {
    if (!force) {
      const cached = settingsCache.get<RuntimeConfig>("/config/runtime");
      if (cached) { setConfig(cached); setDraft({}); return; }
    }
    setLoading(true);
    try {
      const data = await apiGet<RuntimeConfig>("/config/runtime");
      settingsCache.set("/config/runtime", data);
      setConfig(data);
      setDraft({});
    } catch {
      // 后端未就绪或未授权
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isAdmin) {
      fetchConfig();
    }
  }, [fetchConfig, isAdmin]);

  const merged = config
    ? { ...config, ...draft }
    : null;

  const hasChanges = Object.keys(draft).length > 0;

  const handleSave = async () => {
    if (!hasChanges || !isAdmin) return;
    setSaving(true);
    try {
      const res = await apiPut<{ restarting?: boolean }>("/config/runtime", draft);
      if (res?.restarting) {
        setRestarting(true);
        setSaving(false);
        // 直连后端健康检查 URL（绕过 Next.js 代理和 auth 拦截）
        const directBase = `http://${window.location.hostname}:8000`;
        const healthUrl = `${directBase}/api/v1/health`;
        // 等待后端进程退出
        await new Promise((r) => setTimeout(r, 5000));
        const poll = async () => {
          for (let i = 0; i < 60; i++) {
            try {
              const r = await fetch(healthUrl, { method: "GET", signal: AbortSignal.timeout(3000) });
              if (r.ok) { window.location.reload(); return; }
            } catch {
              // 连接失败 = 后端尚未就绪
            }
            await new Promise((r) => setTimeout(r, 2000));
          }
          setRestarting(false);
        };
        poll();
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

  if (restarting) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-4">
        <Loader2 className="h-8 w-8 animate-spin" style={{ color: "var(--em-primary)" }} />
        <div className="text-center space-y-1">
          <p className="text-sm font-medium">服务正在重启…</p>
          <p className="text-xs text-muted-foreground">认证配置已更新，正在等待后端就绪，请勿关闭页面</p>
        </div>
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="space-y-4">
        <div className="rounded-lg border border-border p-4">
          <div className="flex items-center gap-2 mb-2">
            <Lock className="h-4 w-4" style={{ color: "var(--em-primary)" }} />
            <h3 className="font-semibold text-sm">系统配置</h3>
          </div>
          <p className="text-xs text-muted-foreground">
            系统配置由管理员管理。如需调整，请联系管理员。
          </p>
        </div>
      </div>
    );
  }

  if (loading && !config) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin mr-2" />
        加载配置…
      </div>
    );
  }

  if (!merged) {
    return (
      <div className="text-center py-12 text-muted-foreground text-sm">
        无法获取系统配置
      </div>
    );
  }

  const renderGroups = (groups: ItemGroup[]) =>
    groups.map((group) => (
      <div key={group.title}>
        <div className="flex items-center gap-1.5 mb-2.5">
          <span style={{ color: "var(--em-primary)" }}>{group.icon}</span>
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            {group.title}
          </h3>
        </div>
        <div className="space-y-3">
          {group.items.map((item) => {
            const value = merged[item.key];
            return (
              <div key={item.key}>
                <div className="flex items-center justify-between gap-3 sm:gap-4">
                  <div className="flex items-start gap-2.5 sm:gap-3 flex-1 min-w-0">
                    <span className="mt-0.5 text-muted-foreground flex-shrink-0">{item.icon}</span>
                    <div className="min-w-0">
                      <div className="text-sm font-medium">{item.label}</div>
                      <div className="text-[11px] sm:text-xs text-muted-foreground">{item.desc}</div>
                    </div>
                  </div>

                  {item.type === "bool" ? (
                    <Switch
                      checked={value as boolean}
                      onCheckedChange={(checked: boolean) =>
                        setDraft((prev) => ({ ...prev, [item.key]: checked }))
                      }
                      className="flex-shrink-0"
                    />
                  ) : item.type === "select" && item.options ? (
                    <select
                      className="w-28 sm:w-32 h-8 text-sm rounded-md border border-input bg-background px-2 flex-shrink-0"
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
                  ) : (
                    <Input
                      type="number"
                      className="w-20 sm:w-24 h-8 text-sm text-right flex-shrink-0"
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
                <Separator className="mt-3" />
              </div>
            );
          })}
        </div>
        <div className="h-2" />
      </div>
    ));

  return (
    <div className="space-y-5">
      {renderGroups(BASIC_GROUPS)}

      <button
        onClick={() => setShowAdvanced((prev) => !prev)}
        className="flex items-center gap-1.5 w-full text-left py-1.5 group"
      >
        <ChevronDown
          className={`h-3.5 w-3.5 text-muted-foreground transition-transform duration-200 ${showAdvanced ? "" : "-rotate-90"}`}
        />
        <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider group-hover:text-foreground transition-colors">
          高级设置
        </span>
      </button>

      {showAdvanced && renderGroups(ADVANCED_GROUPS)}

      <div className="flex justify-end pt-2">
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
    </div>
  );
}
