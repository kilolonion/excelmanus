"use client";

import { useEffect, useState, useCallback } from "react";
import { useConnectionStore } from "@/stores/connection-store";
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
  Container,
  Hammer,
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
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";

import { apiGet, apiPut, fetchDockerSandboxStatus, setDockerSandbox, buildDockerSandboxImage, fetchSessionIsolationStatus } from "@/lib/api";
import type { DockerSandboxStatus, SessionIsolationStatus } from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";
import { useAuthStore } from "@/stores/auth-store";
import { useAuthConfigStore } from "@/stores/auth-config-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useUIStore } from "@/stores/ui-store";

interface RuntimeConfig {
  // 会话与多用户
  auth_enabled: boolean;
  session_ttl_seconds: number;
  max_sessions: number;
  max_consecutive_failures: number;
  // 执行与安全
  subagent_enabled: boolean;
  verifier_enabled: boolean;
  backup_enabled: boolean;
  checkpoint_enabled: boolean;
  external_safe_mode: boolean;
  max_iterations: number;
  guard_mode: string;
  friendly_error_messages: boolean;
  // AUX / VLM 开关
  aux_enabled: boolean;
  vlm_enabled: boolean;
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
  // 感知与视觉
  window_perception_enabled: boolean;
  window_perception_system_budget_tokens: number;
  window_perception_tool_append_tokens: number;
  window_perception_max_windows: number;
  window_perception_default_rows: number;
  window_perception_default_cols: number;
  window_perception_minimized_tokens: number;
  window_perception_background_after_idle: number;
  window_perception_suspend_after_idle: number;
  window_perception_terminate_after_idle: number;
  window_perception_advisor_mode: string;
  window_perception_advisor_timeout_ms: number;
  window_perception_advisor_trigger_window_count: number;
  window_perception_advisor_trigger_turn: number;
  window_perception_advisor_plan_ttl_turns: number;
  window_return_mode: string;
  window_full_max_rows: number;
  window_full_total_budget_tokens: number;
  window_data_buffer_max_rows: number;
  window_intent_enabled: boolean;
  window_intent_sticky_turns: number;
  window_intent_repeat_warn_threshold: number;
  window_intent_repeat_trip_threshold: number;
  window_rule_engine_version: string;
  vlm_enhance: boolean;
  main_model_vision: string;
  vlm_timeout_seconds: number;
  vlm_max_retries: number;
  vlm_max_tokens: number;
  vlm_image_max_long_edge: number;
  vlm_image_jpeg_quality: number;
  vlm_extraction_tier: string;
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
  playbook_inject_top_k: number;
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
        key: "verifier_enabled",
        label: "完成前验证器",
        desc: "任务完成前自动运行 Verifier 子代理校验结果",
        icon: <CheckCircle2 className="h-4 w-4" />,
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
      {
        key: "friendly_error_messages",
        label: "友好错误消息",
        desc: "将内部错误映射为更友好的用户可见消息",
        icon: <AlertCircle className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "aux_enabled",
        label: "辅助模型",
        desc: "启用辅助模型（子代理默认模型、窗口感知等）",
        icon: <Bot className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "vlm_enabled",
        label: "VLM 模型",
        desc: "启用视觉语言模型（独立 VLM 配置）",
        icon: <ScanEye className="h-4 w-4" />,
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
        desc: "用于记忆维护的模型 ID（留空使用辅助模型）",
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
        desc: "中风险代码（黄区）自动审批执行",
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
    title: "窗口感知细参",
    icon: <Eye className="h-3.5 w-3.5" />,
    items: [
      {
        key: "window_perception_system_budget_tokens",
        label: "系统提示预算",
        desc: "窗口感知层系统提示 token 预算",
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 500,
        max: 20000,
      },
      {
        key: "window_perception_tool_append_tokens",
        label: "工具追加 token",
        desc: "每个工具结果追加的 token 数",
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 100,
        max: 5000,
      },
      {
        key: "window_perception_max_windows",
        label: "最大窗口数",
        desc: "同时维护的最大窗口数量",
        icon: <Layers className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 20,
      },
      {
        key: "window_perception_default_rows",
        label: "默认行数",
        desc: "窗口默认显示行数",
        icon: <Layers className="h-4 w-4" />,
        type: "int",
        min: 5,
        max: 200,
      },
      {
        key: "window_perception_default_cols",
        label: "默认列数",
        desc: "窗口默认显示列数",
        icon: <Layers className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 100,
      },
      {
        key: "window_perception_minimized_tokens",
        label: "最小化 token",
        desc: "最小化窗口的 token 预算",
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 10,
        max: 500,
      },
      {
        key: "window_perception_background_after_idle",
        label: "后台化闲置轮次",
        desc: "窗口闲置多少轮后转为后台",
        icon: <Clock className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 20,
      },
      {
        key: "window_perception_suspend_after_idle",
        label: "挂起闲置轮次",
        desc: "窗口闲置多少轮后挂起",
        icon: <Clock className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 30,
      },
      {
        key: "window_perception_terminate_after_idle",
        label: "关闭闲置轮次",
        desc: "窗口闲置多少轮后关闭",
        icon: <Clock className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 50,
      },
      {
        key: "window_perception_advisor_mode",
        label: "顾问模式",
        desc: "窗口感知顾问的工作模式",
        icon: <Brain className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "rules", label: "规则 (rules)" },
          { value: "hybrid", label: "混合 (hybrid)" },
        ],
      },
      {
        key: "window_perception_advisor_timeout_ms",
        label: "顾问超时",
        desc: "窗口感知顾问超时（毫秒）",
        icon: <Timer className="h-4 w-4" />,
        type: "int",
        min: 100,
        max: 10000,
      },
      {
        key: "window_return_mode",
        label: "返回模式",
        desc: "工具返回数据的模式",
        icon: <Layers className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "unified", label: "统一 (unified)" },
          { value: "anchored", label: "锚定 (anchored)" },
          { value: "enriched", label: "增强 (enriched)" },
          { value: "adaptive", label: "自适应 (adaptive)" },
        ],
      },
      {
        key: "window_intent_enabled",
        label: "意图识别",
        desc: "启用窗口意图识别",
        icon: <Eye className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "window_rule_engine_version",
        label: "规则引擎版本",
        desc: "窗口规则引擎版本",
        icon: <Layers className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "v1", label: "v1" },
          { value: "v2", label: "v2" },
        ],
      },
    ],
  },
  {
    title: "VLM 参数",
    icon: <ScanEye className="h-3.5 w-3.5" />,
    items: [
      {
        key: "vlm_timeout_seconds",
        label: "VLM 超时",
        desc: "VLM 调用超时时间（秒）",
        icon: <Timer className="h-4 w-4" />,
        type: "int",
        min: 10,
        max: 600,
      },
      {
        key: "vlm_max_retries",
        label: "VLM 重试次数",
        desc: "VLM 调用失败时的重试次数",
        icon: <RotateCcw className="h-4 w-4" />,
        type: "int",
        min: 0,
        max: 5,
      },
      {
        key: "vlm_max_tokens",
        label: "VLM 最大输出",
        desc: "VLM 最大输出 token 数",
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 1024,
        max: 65536,
      },
      {
        key: "vlm_image_max_long_edge",
        label: "图片长边上限",
        desc: "图片长边像素上限",
        icon: <Eye className="h-4 w-4" />,
        type: "int",
        min: 512,
        max: 8192,
      },
      {
        key: "vlm_image_jpeg_quality",
        label: "JPEG 质量",
        desc: "JPEG 压缩质量 (1-100)",
        icon: <Eye className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 100,
      },
      {
        key: "vlm_extraction_tier",
        label: "提取策略分级",
        desc: "模型分级决定提取策略",
        icon: <Layers className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "auto", label: "自动 (auto)" },
          { value: "strong", label: "强 (strong)" },
          { value: "standard", label: "标准 (standard)" },
          { value: "weak", label: "弱 (weak)" },
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
      {
        key: "playbook_inject_top_k",
        label: "注入 Top-K",
        desc: "每轮注入的最大条目数",
        icon: <Layers className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 20,
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

function DockerSandboxSection() {
  const [status, setStatus] = useState<DockerSandboxStatus | null>(null);
  const [isolation, setIsolation] = useState<SessionIsolationStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState(false);
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [buildMsg, setBuildMsg] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ds, si] = await Promise.all([
        fetchDockerSandboxStatus(),
        fetchSessionIsolationStatus(),
      ]);
      setStatus(ds);
      setIsolation(si);
    } catch {
      setError("无法获取沙盒状态");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const handleToggle = async (enabled: boolean) => {
    setToggling(true);
    setError(null);
    setBuildMsg(null);
    try {
      const res = await setDockerSandbox(enabled);
      setStatus((prev) => prev ? { ...prev, docker_sandbox_enabled: res.docker_sandbox_enabled } : prev);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setToggling(false);
    }
  };

  const handleBuild = async (force: boolean) => {
    setBuilding(true);
    setError(null);
    setBuildMsg(null);
    try {
      const res = await buildDockerSandboxImage(force);
      setBuildMsg(res.message || "镜像构建完成");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "构建失败");
    } finally {
      setBuilding(false);
    }
  };

  if (loading && !status) {
    return (
      <div className="rounded-lg border border-border p-4">
        <div className="flex items-center gap-2 text-muted-foreground text-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          加载沙盒状态…
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2.5 p-4 pb-3">
        <span
          className="flex-shrink-0 w-7 h-7 rounded-md flex items-center justify-center"
          style={{ backgroundColor: "var(--em-primary-alpha-10)", color: "var(--em-primary)" }}
        >
          <Container className="h-3.5 w-3.5" />
        </span>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium">Docker 沙盒</div>
          <div className="text-[11px] sm:text-xs text-muted-foreground">
            在隔离容器中执行代码，防止文件系统被意外修改
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 sm:h-7 sm:w-7 flex-shrink-0"
          onClick={refresh}
          disabled={loading}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
        </Button>
      </div>

      {status && (
        <div className="px-4 pb-4 space-y-3">
          {/* Status indicators */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <div className="flex items-center gap-2 rounded-md border border-border/60 bg-muted/20 px-3 py-2">
              <StatusDot ok={status.docker_available} />
              <span className="text-xs">
                Docker Daemon {status.docker_available ? "可用" : "不可用"}
              </span>
            </div>
            <div className="flex items-center gap-2 rounded-md border border-border/60 bg-muted/20 px-3 py-2">
              <StatusDot ok={status.sandbox_image_ready} />
              <span className="text-xs">
                沙盒镜像 {status.sandbox_image_ready ? "就绪" : "未就绪"}
              </span>
            </div>
            <div className="flex items-center gap-2 rounded-md border border-border/60 bg-muted/20 px-3 py-2">
              <StatusDot ok={status.docker_sandbox_enabled} />
              <span className="text-xs">
                沙盒 {status.docker_sandbox_enabled ? "已启用" : "已关闭"}
              </span>
            </div>
          </div>

          {/* Toggle */}
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-start gap-2.5 flex-1 min-w-0">
              <span className="mt-0.5 text-muted-foreground flex-shrink-0">
                <Shield className="h-4 w-4" />
              </span>
              <div className="min-w-0">
                <div className="text-sm font-medium">启用 Docker 沙盒</div>
                <div className="text-[11px] sm:text-xs text-muted-foreground">
                  启用时，代码在隔离容器中执行；启用时若镜像未就绪会自动构建
                </div>
              </div>
            </div>
            <Switch
              checked={status.docker_sandbox_enabled}
              onCheckedChange={handleToggle}
              disabled={toggling || !status.docker_available}
              className="flex-shrink-0"
            />
          </div>

          {/* Build image button */}
          <div className="space-y-2 sm:space-y-0">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-start gap-2.5 flex-1 min-w-0">
                <span className="mt-0.5 text-muted-foreground flex-shrink-0">
                  <Hammer className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <div className="text-sm font-medium">沙盒镜像管理</div>
                  <div className="text-[11px] sm:text-xs text-muted-foreground">
                    构建或重建 Docker 沙盒镜像
                  </div>
                </div>
              </div>
              <div className="hidden sm:flex gap-1.5 flex-shrink-0">
                <Button
                  variant="outline"
                  size="sm"
                  className="text-xs gap-1.5 h-7"
                  disabled={building || !status.docker_available}
                  onClick={() => handleBuild(false)}
                >
                  {building ? <Loader2 className="h-3 w-3 animate-spin" /> : <Hammer className="h-3 w-3" />}
                  构建
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="text-xs gap-1.5 h-7"
                  disabled={building || !status.docker_available}
                  onClick={() => handleBuild(true)}
                >
                  {building ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                  强制重建
                </Button>
              </div>
            </div>
            {/* Mobile: full-width build buttons */}
            <div className="flex sm:hidden gap-2 pl-6.5">
              <Button
                variant="outline"
                size="sm"
                className="text-xs gap-1.5 h-9 flex-1"
                disabled={building || !status.docker_available}
                onClick={() => handleBuild(false)}
              >
                {building ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Hammer className="h-3.5 w-3.5" />}
                构建镜像
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="text-xs gap-1.5 h-9 flex-1"
                disabled={building || !status.docker_available}
                onClick={() => handleBuild(true)}
              >
                {building ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                强制重建
              </Button>
            </div>
          </div>

          {/* Session isolation (read-only info) */}
          {isolation && (
            <>
              <Separator />
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-start gap-2.5 flex-1 min-w-0">
                  <span className="mt-0.5 text-muted-foreground flex-shrink-0">
                    <Users className="h-4 w-4" />
                  </span>
                  <div className="min-w-0">
                    <div className="text-sm font-medium">会话用户隔离</div>
                    <div className="text-[11px] sm:text-xs text-muted-foreground">
                      启用多用户认证时自动激活，无需手动配置
                    </div>
                  </div>
                </div>
                <span className={`text-xs font-medium px-2 py-0.5 rounded-full flex-shrink-0 ${
                  isolation.session_isolation_enabled
                    ? "bg-green-500/10 text-green-600 dark:text-green-400"
                    : "bg-muted text-muted-foreground"
                }`}>
                  {isolation.session_isolation_enabled ? "已启用" : "未启用"}
                </span>
              </div>
            </>
          )}

          {/* Error / success messages */}
          {error && (
            <div className="flex items-start gap-2 rounded-md bg-red-500/5 border border-red-500/10 px-3 py-2">
              <AlertCircle className="h-3.5 w-3.5 text-red-500 flex-shrink-0 mt-0.5" />
              <span className="text-[11px] text-red-600 dark:text-red-400">{error}</span>
            </div>
          )}
          {buildMsg && !error && (
            <div className="flex items-start gap-2 rounded-md bg-green-500/5 border border-green-500/10 px-3 py-2">
              <CheckCircle2 className="h-3.5 w-3.5 text-green-500 flex-shrink-0 mt-0.5" />
              <span className="text-[11px] text-green-600 dark:text-green-400">{buildMsg}</span>
            </div>
          )}
        </div>
      )}
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
        <OnboardingReplayCard />
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
      <DockerSandboxSection />
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
