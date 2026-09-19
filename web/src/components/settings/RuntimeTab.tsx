"use client";

import { useEffect, useState, useCallback } from "react";
import { useConnectionStore } from "@/stores/connection-store";
import {
  Loader2,
  Save,
  CheckCircle2,
  Shield,
  Bot,
  RotateCcw,
  Gauge,
  Shrink,
  History,
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
  Sparkles,
  Cpu,
  ArrowRight,
  SlidersHorizontal,
  Code2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";

import { apiGet, apiPut, togglePresentAs } from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useUIStore } from "@/stores/ui-store";
import { useSessionStore } from "@/stores/session-store";

interface RuntimeConfig {
  // 会话
  session_ttl_seconds: number;
  max_sessions: number;
  max_consecutive_failures: number;
  // 执行与安全
  subagent_enabled: boolean;
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
        type: "bool",
      },
      {
        key: "memory_enabled",
        label: "跨会话记忆",
        desc: "关闭后不再读写持久记忆，也不再自动提取。已有记录会保留。新开对话后完全生效。",
        icon: <Brain className="h-4 w-4" />,
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
        key: "main_model_vision",
        label: "图片识别",
        desc: "控制当前对话模型能否处理图片。自动：按模型能力判断；开启：一律允许；关闭：不接受图片。图片由当前模型直接阅读，不会另开视觉模型。",
        icon: <ScanEye className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "auto", label: "自动" },
          { value: "true", label: "开启" },
          { value: "false", label: "关闭" },
        ],
      },
      {
        key: "subagent_enabled",
        label: "子代理",
        desc: "允许主模型把子任务委派出去。未指定名称时用通用子代理；只读探查需显式指定 explorer。关闭后工具仍可见，但执行会被拒绝。已打开的对话需新开，或使用 /subagent on|off。",
        icon: <Bot className="h-4 w-4" />,
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
    ],
  },
  {
    title: "压缩与缓存",
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
      {
        key: "prompt_cache_key_enabled",
        label: "提示词缓存",
        desc: "向模型接口发送缓存键，提高重复提示词命中率。已打开的对话需新开后生效。",
        icon: <Zap className="h-4 w-4" />,
        type: "bool",
      },
    ],
  },
  {
    title: "记忆维护",
    icon: <Sparkles className="h-3.5 w-3.5" />,
    items: [
      {
        key: "memory_maintenance_enabled",
        label: "记忆自动维护",
        desc: "提取新记忆后，按条目数、增量和间隔合并清理。需先开启跨会话记忆。",
        icon: <Sparkles className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "memory_maintenance_min_entries",
        label: "维护最少条目数",
        desc: "记忆少于此数时不触发维护。",
        icon: <Layers className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 200,
      },
      {
        key: "memory_maintenance_new_threshold",
        label: "维护新增阈值",
        desc: "新增条目达到此数后才可能触发维护。",
        icon: <Layers className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 50,
      },
      {
        key: "memory_maintenance_interval_hours",
        label: "维护最小间隔",
        desc: "两次维护之间的最短间隔（小时）。",
        icon: <Clock className="h-4 w-4" />,
        type: "float",
      },
      {
        key: "memory_maintenance_model",
        label: "维护模型",
        desc: "用于记忆维护的模型 ID，留空则使用当前激活模型。",
        icon: <Brain className="h-4 w-4" />,
        type: "string",
      },
      {
        key: "memory_expire_days",
        label: "记忆过期天数",
        desc: "会话启动时清理超过此天数的记忆。0 表示不过期。",
        icon: <Clock className="h-4 w-4" />,
        type: "int",
        min: 0,
        max: 3650,
      },
    ],
  },
  {
    title: "图片请求投影",
    icon: <Eye className="h-3.5 w-3.5" />,
    items: [
      {
        key: "image_pixel_budget",
        label: "请求像素预算",
        desc: "发给模型的请求版总像素上限。填正整数，或 low（512×512）。不改写历史，只影响当次请求。",
        icon: <Eye className="h-4 w-4" />,
        type: "string",
      },
      {
        key: "image_max_bytes",
        label: "请求编码上限",
        desc: "单张请求版图片的编码字节上限（默认 1MiB）。超出走质量阶梯，历史仍保留规范化附件。",
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 1024,
        max: 20971520,
      },
      {
        key: "image_files_api",
        label: "Files API 传输",
        desc: "auto 仅在 DeepSeek 等声明支持的端点上传 file_id；其余走同一请求版本的 inline。失败回退 inline。",
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
    title: "模型重试",
    icon: <RotateCcw className="h-3.5 w-3.5" />,
    items: [
      {
        key: "llm_retry_max_attempts",
        label: "最大重试次数",
        desc: "模型调用失败时的最大尝试次数（含首次）。遇限流或网络错误会自动重试。已打开的对话需新开后生效。",
        icon: <RotateCcw className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 10,
      },
      {
        key: "llm_retry_base_delay_seconds",
        label: "重试基准延迟",
        desc: "指数退避的起始等待时间（秒）。",
        icon: <Timer className="h-4 w-4" />,
        type: "float",
      },
      {
        key: "llm_retry_max_delay_seconds",
        label: "重试最大延迟",
        desc: "单次重试等待上限（秒）。若接口返回 Retry-After，则优先采用。",
        icon: <Timer className="h-4 w-4" />,
        type: "float",
      },
    ],
  },
  {
    title: "代码与校验",
    icon: <Shield className="h-3.5 w-3.5" />,
    items: [
      {
        key: "code_policy_enabled",
        label: "代码风险分级",
        desc: "对程序内执行的代码做绿 / 黄 / 红分级，决定自动运行还是先确认。关闭后仍在本机子进程运行，只保留基础文件围栏。写入历史在 .excelmanus/revisions。已打开的对话需新开后生效。",
        icon: <Shield className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "code_policy_green_auto_approve",
        label: "绿区自动执行",
        desc: "判定为低风险的代码自动执行。绿区会额外限制网络、起进程和写出工作区。",
        icon: <Shield className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "code_policy_yellow_auto_approve",
        label: "黄区自动执行",
        desc: "中风险代码自动执行。默认关闭；打开后仍不会自动批准写入文件系统。",
        icon: <Shield className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "tool_schema_validation_mode",
        label: "参数结构校验",
        desc: "关闭：不检查。影子：只记日志不拦截。强制：参数不合规则拒绝。仅对新开对话生效。",
        icon: <Shield className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "off", label: "关闭" },
          { value: "shadow", label: "影子" },
          { value: "enforce", label: "强制" },
        ],
      },
      {
        key: "tool_schema_validation_canary_percent",
        label: "强制校验比例",
        desc: "强制模式下实际拦截的请求百分比。未命中的请求按影子模式处理。",
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 0,
        max: 100,
      },
      {
        key: "tool_schema_strict_path",
        label: "严格路径校验",
        desc: "拒绝工具参数里的绝对路径和上级目录穿越。",
        icon: <Shield className="h-4 w-4" />,
        type: "bool",
      },
    ],
  },
  {
    title: "工具与 Hook",
    icon: <Zap className="h-3.5 w-3.5" />,
    items: [
      {
        key: "parallel_readonly_tools",
        label: "只读工具并发",
        desc: "同一轮回复中，相邻的读文件、列目录等只读工具可以并发。写入和多数外部工具始终串行。已打开的对话需新开后生效。",
        icon: <Zap className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "hooks_command_enabled",
        label: "技能 Hook 外部命令",
        desc: "允许技能包在会话开始、用户提交、工具前后、子代理起止时运行外部命令。默认关闭，需授权。已打开的对话需新开后生效。",
        icon: <Terminal className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "hooks_command_timeout_seconds",
        label: "Hook 命令超时",
        desc: "外部命令最长执行时间（秒）。超时则跳过，不影响主流程。",
        icon: <Timer className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 300,
      },
      {
        key: "hooks_output_max_chars",
        label: "Hook 输出上限",
        desc: "外部命令返回内容注入对话的最大字符数。",
        icon: <Shrink className="h-4 w-4" />,
        type: "int",
        min: 1000,
        max: 100000,
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
  {
    title: "技能发现",
    icon: <Sparkles className="h-3.5 w-3.5" />,
    items: [
      {
        key: "skills_discovery_enabled",
        label: "自动发现兼容目录",
        desc: "除内置和用户 / 项目目录外，还扫描 Claude、OpenClaw、Cursor 等常用技能文件夹。关闭后只加载内置以及 .excelmanus/skillpacks。",
        icon: <Sparkles className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "skills_discovery_include_agents",
        label: "加载 .agents/skills",
        desc: "扫描项目里的 .agents/skills 文件夹（Cursor 等工具常用位置）。与子代理无关。",
        icon: <Bot className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "skills_discovery_scan_workspace_ancestors",
        label: "扫描上级目录中的技能",
        desc: "从当前工作目录到项目根，逐层查找 .agents/skills。仅在当前目录位于项目工作区内时生效。",
        icon: <Sparkles className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "skills_discovery_scan_external_tool_dirs",
        label: "兼容 Claude 与 OpenClaw",
        desc: "从 ~/.claude/skills、~/.openclaw/skills 及项目内同名文件夹加载技能。未使用这些工具时可关闭。",
        icon: <Sparkles className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "skills_context_char_budget",
        label: "技能注入长度上限",
        desc: "用 /技能名 激活技能时，注入对话的正文总字符上限。0 表示不限制。不影响技能列表扫描。",
        icon: <Gauge className="h-4 w-4" />,
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
    title: "界面基础引导",
    description: "了解侧边栏、输入框、发送消息、模型切换等核心操作",
    icon: <BookOpen className="h-4 w-4" />,
  },
  {
    key: "advanced",
    category: "进阶",
    categoryColor: "text-amber-600 dark:text-amber-400",
    categoryBg: "bg-amber-50 dark:bg-amber-950/40",
    title: "进阶技巧探索",
    description: "掌握斜杠命令、对话模式切换、文件预览与技能规则",
    icon: <Sparkles className="h-4 w-4" />,
  },
  {
    key: "settings",
    category: "设置",
    categoryColor: "text-violet-600 dark:text-violet-400",
    categoryBg: "bg-violet-50 dark:bg-violet-950/40",
    title: "设置面板引导",
    description: "深入了解模型、规则、技能、MCP、记忆、系统等设置页面",
    icon: <SlidersHorizontal className="h-4 w-4" />,
  },
];

function CodeModeCard() {
  const presentAs = useUIStore((s) => s.presentAs);
  const setPresentAs = useUIStore((s) => s.setPresentAs);
  const sessionId = useSessionStore((s) => s.activeSessionId);
  const [saving, setSaving] = useState(false);
  const enabled = presentAs === "code";

  const handleChange = useCallback(
    async (checked: boolean) => {
      const mode = checked ? "code" : "native";
      setPresentAs(mode);
      if (!sessionId) return;
      setSaving(true);
      try {
        await togglePresentAs(sessionId, mode);
      } catch {
        setPresentAs(checked ? "native" : "code");
      } finally {
        setSaving(false);
      }
    },
    [sessionId, setPresentAs],
  );

  return (
    <div className="rounded-lg border border-border p-4" data-coach-id="coach-code-mode">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-start gap-2.5 min-w-0">
          <span className="mt-0.5 text-muted-foreground flex-shrink-0">
            <Code2 className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <div className="text-sm font-medium">代码模式</div>
            <div className="mt-1 text-[11px] sm:text-xs text-muted-foreground">
              只向模型暴露写代码入口，其余能力在程序内调用。观察 / 计划模式仍使用原生工具。
              也可用 <code className="font-mono">/code on</code> 或 <code className="font-mono">/code off</code>。
              此开关只影响当前对话，与下方代码风险分级无关。
            </div>
          </div>
        </div>
        <Switch
          checked={enabled}
          onCheckedChange={(checked: boolean) => void handleChange(checked)}
          disabled={saving}
          className="flex-shrink-0"
        />
      </div>
    </div>
  );
}

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
      <CodeModeCard />
      <Separator />
      {renderGroups(BASIC_GROUPS)}

      <button
        onClick={() => setShowAdvanced((prev) => !prev)}
        className="flex items-center gap-1.5 w-full text-left py-1.5 group"
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

      <OnboardingReplayCard />

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
