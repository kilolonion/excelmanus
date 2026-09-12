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
  RefreshCw,
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
  max_iterations: number;
  friendly_error_messages: boolean;
  // 上下文与记忆
  max_context_tokens: number;
  memory_enabled: boolean;
  memory_auto_extract_interval: number;
  memory_auto_load_lines: number;
  memory_expire_days: number;
  chat_history_enabled: boolean;
  // 记忆维护
  memory_maintenance_enabled: boolean;
  memory_maintenance_min_entries: number;
  memory_maintenance_new_threshold: number;
  memory_maintenance_interval_hours: number;
  memory_maintenance_model: string;
  // 摘要与压缩
  summarization_enabled: boolean;
  summarization_threshold_ratio: number;
  summarization_keep_recent_turns: number;
  compaction_enabled: boolean;
  compaction_threshold_ratio: number;
  compaction_keep_recent_turns: number;
  compaction_max_summary_tokens: number;
  prompt_cache_key_enabled: boolean;
  // 推理配置
  thinking_effort: string;
  thinking_budget: number;
  // 子代理
  subagent_max_iterations: number;
  subagent_timeout_seconds: number;
  subagent_max_consecutive_failures: number;
  parallel_subagent_max: number;
  // LLM 重试
  llm_retry_max_attempts: number;
  llm_retry_base_delay_seconds: number;
  llm_retry_max_delay_seconds: number;
  // 视觉
  main_model_vision: string;
  image_keep_rounds: number;
  image_max_active: number;
  image_token_budget: number;
  // 系统消息与工具
  system_message_mode: string;
  tool_result_hard_cap_chars: number;
  large_excel_threshold_bytes: number;
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
  // Embedding / 语义检索
  embedding_enabled: boolean;
  embedding_model: string;
  embedding_dimensions: number;
  embedding_timeout_seconds: number;
  memory_semantic_top_k: number;
  memory_semantic_threshold: number;
  memory_semantic_fallback_recent: number;
  // Playbook
  playbook_enabled: boolean;
  playbook_max_bullets: number;
  registry_semantic_top_k: number;
  registry_semantic_threshold: number;
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
    title: "会话",
    icon: <Users className="h-3.5 w-3.5" />,
    items: [
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
        key: "friendly_error_messages",
        label: "友好错误消息",
        desc: "将内部错误映射为更友好的用户可见消息",
        icon: <AlertCircle className="h-4 w-4" />,
        type: "bool",
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
        desc: "最大上下文 token 数。保存后立即同步到已打开的对话并锁定；未手动保存时按当前激活模型推断。",
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
        desc: "超阈值时用激活模型压缩早期对话",
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
      {
        key: "memory_auto_load_lines",
        label: "记忆自动加载行数",
        desc: "会话开始时自动加载的记忆条目数",
        icon: <Brain className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 1000,
      },
      {
        key: "memory_expire_days",
        label: "记忆过期天数",
        desc: "记忆过期天数（0 = 不过期）",
        icon: <Clock className="h-4 w-4" />,
        type: "int",
        min: 0,
        max: 3650,
      },
    ],
  },
  {
    title: "感知与视觉",
    icon: <Eye className="h-3.5 w-3.5" />,
    items: [
      {
        key: "main_model_vision",
        label: "视觉能力",
        desc: "激活模型视觉能力：auto 自动检测 / true 强制开启 / false 关闭。图片只交给当前模型阅读，随后用 edit_spreadsheet(workbook_spec) 建表。",
        icon: <ScanEye className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "auto", label: "自动 (auto)" },
          { value: "true", label: "开启 (true)" },
          { value: "false", label: "关闭 (false)" },
        ],
      },
      {
        key: "image_keep_rounds",
        label: "图片保持轮次",
        desc: "图片保持完整 base64 的最小轮次",
        icon: <Eye className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 20,
      },
      {
        key: "image_max_active",
        label: "活跃图片上限",
        desc: "同时保持高清的最大图片数",
        icon: <Eye className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 10,
      },
      {
        key: "image_token_budget",
        label: "图片 token 预算",
        desc: "图片总 token 预算上限",
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 1000,
        max: 50000,
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
          { value: "max", label: "最深 (max)" },
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
      {
        key: "subagent_max_consecutive_failures",
        label: "子代理最大连续失败",
        desc: "子代理连续工具调用失败达到此次数后停止",
        icon: <AlertCircle className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 50,
      },
    ],
  },
  {
    title: "LLM 重试",
    icon: <RotateCcw className="h-3.5 w-3.5" />,
    items: [
      {
        key: "llm_retry_max_attempts",
        label: "最大重试次数",
        desc: "LLM 调用失败时的最大尝试次数（含首次）",
        icon: <RotateCcw className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 10,
      },
      {
        key: "llm_retry_base_delay_seconds",
        label: "重试基准延迟",
        desc: "指数退避基准延迟（秒）",
        icon: <Timer className="h-4 w-4" />,
        type: "float",
      },
      {
        key: "llm_retry_max_delay_seconds",
        label: "重试最大延迟",
        desc: "单次重试最大延迟上限（秒）",
        icon: <Timer className="h-4 w-4" />,
        type: "float",
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
        key: "memory_maintenance_enabled",
        label: "记忆自动维护",
        desc: "启用 LLM 驱动的记忆清理、合并与改进",
        icon: <Sparkles className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "memory_maintenance_min_entries",
        label: "维护最少条目数",
        desc: "记忆条目少于此数时不触发维护",
        icon: <Layers className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 200,
      },
      {
        key: "memory_maintenance_new_threshold",
        label: "维护新增阈值",
        desc: "新增条目达到此数后触发维护",
        icon: <Layers className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 50,
      },
      {
        key: "memory_maintenance_interval_hours",
        label: "维护最小间隔",
        desc: "两次维护之间的最小间隔（小时）",
        icon: <Clock className="h-4 w-4" />,
        type: "float",
      },
      {
        key: "memory_maintenance_model",
        label: "维护模型",
        desc: "用于记忆维护的模型 ID（留空使用激活模型）",
        icon: <Brain className="h-4 w-4" />,
        type: "string",
      },
      {
        key: "prompt_cache_key_enabled",
        label: "提示词缓存",
        desc: "向 API 发送缓存键提升 prompt 缓存命中率",
        icon: <Zap className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "summarization_threshold_ratio",
        label: "摘要触发比例",
        desc: "Token 使用率超过此比例触发对话摘要 (0-1)",
        icon: <Gauge className="h-4 w-4" />,
        type: "float",
      },
      {
        key: "summarization_keep_recent_turns",
        label: "摘要保留轮次",
        desc: "摘要时保留的最近对话轮次数",
        icon: <History className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 20,
      },
      {
        key: "compaction_keep_recent_turns",
        label: "压缩保留轮次",
        desc: "压缩时保留的最近对话轮次数",
        icon: <History className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 20,
      },
      {
        key: "compaction_max_summary_tokens",
        label: "压缩摘要上限",
        desc: "压缩摘要最大 token 数",
        icon: <Shrink className="h-4 w-4" />,
        type: "int",
        min: 100,
        max: 10000,
      },
    ],
  },
  {
    title: "安全与策略",
    icon: <Shield className="h-3.5 w-3.5" />,
    items: [
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
      {
        key: "code_policy_green_auto_approve",
        label: "绿区自动审批",
        desc: "安全代码（绿区）自动审批执行",
        icon: <Shield className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "code_policy_yellow_auto_approve",
        label: "黄区自动审批",
        desc: "中风险代码（黄区）自动审批。默认关闭；打开后仍不会自动批准文件系统写入",
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
      {
        key: "system_message_mode",
        label: "系统消息模式",
        desc: "system message 处理方式：auto / merge / replace",
        icon: <MessageSquare className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "auto", label: "自动 (auto)" },
          { value: "merge", label: "合并 (merge)" },
          { value: "replace", label: "替换 (replace)" },
        ],
      },
      {
        key: "tool_result_hard_cap_chars",
        label: "工具结果截断",
        desc: "工具返回结果的字符数上限（0 = 不限制）",
        icon: <Shrink className="h-4 w-4" />,
        type: "int",
        min: 0,
        max: 100000,
      },
      {
        key: "large_excel_threshold_bytes",
        label: "大表格阈值",
        desc: "Excel 文件超过此字节数视为大文件",
        icon: <FileText className="h-4 w-4" />,
        type: "int",
        min: 1048576,
        max: 104857600,
      },
      {
        key: "hooks_command_timeout_seconds",
        label: "Hook 命令超时",
        desc: "Hook 命令执行超时时间（秒）",
        icon: <Timer className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 300,
      },
      {
        key: "hooks_output_max_chars",
        label: "Hook 输出上限",
        desc: "Hook 命令输出的最大字符数",
        icon: <Shrink className="h-4 w-4" />,
        type: "int",
        min: 1000,
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
        label: "技能发现",
        desc: "启用自动技能包发现",
        icon: <Sparkles className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "skills_discovery_scan_workspace_ancestors",
        label: "扫描祖先目录",
        desc: "技能发现时扫描工作区祖先目录",
        icon: <Sparkles className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "skills_discovery_include_agents",
        label: "包含代理",
        desc: "技能发现时包含代理包",
        icon: <Bot className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "skills_discovery_scan_external_tool_dirs",
        label: "扫描外部工具目录",
        desc: "技能发现时扫描外部工具目录",
        icon: <Sparkles className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "skills_context_char_budget",
        label: "技能字符预算",
        desc: "技能正文字符预算（0 = 不限制）",
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 0,
        max: 100000,
      },
    ],
  },
  {
    title: "Embedding / 语义检索",
    icon: <Brain className="h-3.5 w-3.5" />,
    items: [
      {
        key: "embedding_enabled",
        label: "语义检索",
        desc: "启用 embedding 语义检索功能",
        icon: <Brain className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "embedding_model",
        label: "Embedding 模型",
        desc: "语义检索使用的 embedding 模型",
        icon: <Brain className="h-4 w-4" />,
        type: "string",
      },
      {
        key: "embedding_dimensions",
        label: "Embedding 维度",
        desc: "Embedding 向量维度",
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 64,
        max: 8192,
      },
      {
        key: "memory_semantic_top_k",
        label: "语义检索 Top-K",
        desc: "语义检索返回的最大条目数",
        icon: <Layers className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 50,
      },
      {
        key: "memory_semantic_threshold",
        label: "语义相似度阈值",
        desc: "低于此阈值的结果将被过滤 (0-1)",
        icon: <Gauge className="h-4 w-4" />,
        type: "float",
      },
    ],
  },
  {
    title: "Playbook",
    icon: <BookOpen className="h-3.5 w-3.5" />,
    items: [
      {
        key: "playbook_enabled",
        label: "Playbook",
        desc: "启用自进化战术手册",
        icon: <BookOpen className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "playbook_max_bullets",
        label: "最大条目数",
        desc: "Playbook 条目上限",
        icon: <Layers className="h-4 w-4" />,
        type: "int",
        min: 10,
        max: 5000,
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

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full flex-shrink-0 ${ok ? "bg-green-500" : "bg-red-400"}`}
    />
  );
}

function LocalSandboxNote() {
  return (
    <div className="rounded-lg border border-border p-4">
      <div className="text-sm font-medium">本机代码围栏</div>
      <div className="mt-1 text-[11px] sm:text-xs text-muted-foreground">
        本机受限子进程：禁网络、禁起进程、禁出工作区。
        文件历史在 .excelmanus/revisions，不写 outputs/backups。
      </div>
    </div>
  );
}

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
              只向模型暴露 run_code，其余能力在程序内通过 SDK 调用。观察/计划模式仍使用原生工具。
              也可用 <code className="font-mono">/code on</code> 或 <code className="font-mono">/code off</code>。
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
                <p className="text-[11px] text-muted-foreground leading-relaxed line-clamp-1">
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
      <OnboardingReplayCard />
      <LocalSandboxNote />
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
