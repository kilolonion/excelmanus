import { Bot, Gauge, History, Layers, MessageSquare, Server, Shrink, Timer, Zap } from "lucide-react";
import type { RuntimeSettingGroup, RuntimeSettingItem } from "@/lib/runtime-settings-form";
import { DISPATCH_LABELS } from "@/stores/dispatch-store";

const number = (key: string, label: string, desc: string, min: number, unit: string, extra: Partial<RuntimeSettingItem> = {}): RuntimeSettingItem => ({ key, label, desc, min, unit, type: "int", ...extra });
const toggle = (key: string, label: string, desc: string, extra: Partial<RuntimeSettingItem> = {}): RuntimeSettingItem => ({ key, label, desc, type: "bool", ...extra });
const whenSubagentOff = (settings: Record<string, unknown>) => !settings.subagent_enabled;
const whenCompactionOff = (settings: Record<string, unknown>) => !settings.compaction_enabled;

export const RUNTIME_CATEGORIES = [
  { key: "conversation", label: "对话偏好", icon: <MessageSquare className="h-4 w-4" />, description: "消息与助手行为" },
  { key: "execution", coachId: "coach-settings-advanced-toggle", label: "任务限制", icon: <Gauge className="h-4 w-4" />, description: "时长、用量与并行任务" },
  { key: "context", coachId: "coach-settings-runtime-context", label: "对话容量", icon: <Layers className="h-4 w-4" />, description: "上下文与自动摘要" },
  { key: "service", label: "服务维护", icon: <Server className="h-4 w-4" />, description: "记录、资源与日志" },
];

export const RUNTIME_SETTING_GROUPS: RuntimeSettingGroup[] = [
  {
    category: "conversation", title: "消息与助手", description: "决定任务执行中如何处理新消息，以及助手可使用的能力。", icon: <MessageSquare className="h-4 w-4" />,
    items: [
      { key: "message_dispatch_default", label: "任务进行中发送消息", desc: "引导：让当前任务采用补充要求；中断：停止当前任务后处理；排队：完成当前任务后处理。", type: "select", effect: "immediate", options: Object.entries(DISPATCH_LABELS).map(([value, label]) => ({ value, label })) },
      toggle("subagent_enabled", "允许拆分子任务", "复杂任务可交给多个助手协作，可能增加模型用量。关闭后不再接受新的委派；已有对话保持原设置。"),
      toggle("agent_self_management_enabled", "允许助手调整当前对话", "助手可调整当前对话的思考深度、上下文和工具开关。不会修改密钥、全局默认或审批权限。", { effect: "immediate" }),
    ],
  },
  {
    category: "execution", title: "每条消息的执行上限", description: "限制一次回复及其工具操作的总用量。0 表示不限制；已在执行的消息继续使用原上限。", icon: <Gauge className="h-4 w-4" />,
    items: [
      number("turn_timeout_seconds", "最长执行时间", "包含等待模型、重试、工具操作和同步子任务的时间。", 0, "秒", { effect: "next-turn" }),
      number("max_iterations", "最多执行步数", "模型回复与工具调用都计入步数。达到上限后停止，可发送新消息继续。", 0, "步", { effect: "next-turn" }),
      number("turn_token_budget", "模型用量上限", "累计每次模型请求的输入和输出用量（token）。请求结束后检查，最后一次调用可能超过上限。", 0, "token", { effect: "next-turn" }),
      number("turn_cost_budget_usd", "费用上限", "按服务商返回的费用或下方单价估算。此限制不是账单硬限额；未返回费用且单价为 0 时无法准确计费。", 0, "美元", { type: "float", effect: "next-turn", step: "any" }),
    ],
  },
  {
    category: "execution", title: "费用估算单价", description: "仅在服务商没有返回费用时使用。请按实际定价填写；更换模型后也应检查。", icon: <Gauge className="h-4 w-4" />, defaultOpen: false,
    items: [
      number("input_cost_per_1k_usd", "输入单价", "每 1,000 个输入 token 的美元价格。0 表示不估算该部分费用。", 0, "美元 / 千 token", { type: "float", effect: "next-turn" }),
      number("output_cost_per_1k_usd", "输出单价", "每 1,000 个输出 token 的美元价格。0 表示不估算该部分费用。", 0, "美元 / 千 token", { type: "float", effect: "next-turn" }),
    ],
  },
  {
    category: "execution", title: "失败与重试", description: "在暂时断网或接口限流时自动重试；反复失败则停止，避免无效消耗。", icon: <Timer className="h-4 w-4" />, defaultOpen: false,
    items: [
      number("llm_retry_max_attempts", "模型请求最多尝试次数", "包含第一次请求。设为 1 表示不重试。", 1, "次"),
      number("llm_retry_base_delay_seconds", "首次重试等待", "后续等待时间逐次增加。服务商指定的等待时间优先。", 0, "秒", { type: "float", exclusiveMin: 0, validate: (value, settings) => Number(value) > Number(settings.llm_retry_max_delay_seconds) ? "不能超过最长重试等待" : undefined }),
      number("llm_retry_max_delay_seconds", "最长重试等待", "自动计算的单次等待上限。", 0, "秒", { type: "float", exclusiveMin: 0, validate: (value, settings) => Number(value) < Number(settings.llm_retry_base_delay_seconds) ? "不能小于首次重试等待" : undefined }),
      number("max_consecutive_failures", "工具连续失败停止次数", "工具连续失败达到此数时停止本轮执行；成功一次便重新计数。", 1, "次"),
    ],
  },
  {
    category: "execution", title: "子任务与并行处理", description: "并行可以缩短等待，也会增加资源占用。通常保持现有值即可。", icon: <Bot className="h-4 w-4" />, defaultOpen: false,
    items: [
      number("subagent_timeout_seconds", "子任务最长执行时间", "从子任务开始执行时计时；不包含后台排队时间。", 1, "秒", { disabledWhen: whenSubagentOff, disabledDesc: "请先在对话偏好中允许拆分子任务。" }),
      number("subagent_max_iterations", "子任务最多执行步数", "默认的模型回复与工具调用上限；0 表示不限制。子代理档案可覆盖此值。", 0, "步", { effect: "next-turn", disabledWhen: whenSubagentOff }),
      number("parallel_subagent_max", "同时执行的子任务数", "限制并行委派数量和后台并发；后台超出后排队，冲突的文件写入仍受调度限制。", 1, "个", { disabledWhen: whenSubagentOff }),
      toggle("parallel_readonly_tools", "同时执行独立的读取操作", "允许互不依赖的只读工具并行运行。文件写入仍按顺序执行。"),
      number("parallel_tool_max", "同时读取的工具数", "超出数量的读取操作等待空位后执行。", 1, "个", { max: 32, disabledWhen: (settings) => !settings.parallel_readonly_tools, disabledDesc: "请先开启同时执行独立的读取操作。" }),
    ],
  },
  {
    category: "context", title: "对话与上下文", description: "上下文是模型一次能读到的对话和文件内容。窗口越大，可容纳的内容越多，也可能增加费用。", icon: <Layers className="h-4 w-4" />,
    items: [
      number("max_context_tokens_override", "默认对话容量", "推荐自动匹配当前模型。手动值应在模型支持的范围内；单个模型档案中设置的容量优先。", 1, "token", { effect: "immediate", automatic: { label: "跟随模型自动匹配", effectiveKey: "max_context_tokens" } }),
      toggle("compaction_enabled", "自动摘要较早的对话", "容量不足时概括较早的内容，保留近期消息以继续任务。摘要会调用模型并产生用量。", { effect: "immediate", coachId: "coach-settings-runtime-compaction" }),
    ],
  },
  {
    category: "context", title: "摘要与内容长度", description: "仅在遇到遗忘近期内容或工具结果过长时调整。", icon: <Shrink className="h-4 w-4" />, defaultOpen: false,
    items: [
      number("compaction_threshold_ratio", "触发摘要的占用比例", "例如 0.85 表示占用达到容量的 85% 后开始摘要。", 0, "比例", { type: "float", max: 1, exclusiveMin: 0, exclusiveMax: 1, step: 0.01, effect: "immediate", disabledWhen: whenCompactionOff }),
      number("compaction_keep_recent_turns", "完整保留的最近对话", "这些最近的对话不参与常规摘要，保留原始内容。", 1, "轮", { disabledWhen: whenCompactionOff }),
      number("compaction_max_summary_tokens", "摘要最长用量", "越长越容易保留细节，但会占用更多对话容量。", 1, "token", { disabledWhen: whenCompactionOff }),
      number("tool_result_hard_cap_chars", "工具结果最长文本", "限制单次返回给模型的文本长度；0 关闭此层限制，各工具自身仍可能截断。", 0, "字符"),
    ],
  },
  {
    category: "service", title: "记录与服务资源", description: "这些修改会重启后端服务。请先完成进行中的任务。", icon: <History className="h-4 w-4" />,
    items: [
      toggle("chat_history_enabled", "保存聊天记录", "将对话保存在此服务的数据库中，下次打开可以恢复。关闭后不再保存或恢复；不会删除已有记录。", { effect: "restart" }),
      number("session_ttl_seconds", "空闲对话内存保留时间", "到期回收未在执行的对话内存，已保存的聊天记录仍保留。", 1, "秒", { effect: "restart" }),
      number("max_sessions", "内存中的对话数量上限", "达到上限时无法继续新建内存会话，不是历史聊天记录的数量上限。", 1, "个", { effect: "restart" }),
    ],
  },
  {
    category: "service", title: "诊断日志", description: "排查故障时调整。详细日志会产生更多数据。", icon: <Zap className="h-4 w-4" />, defaultOpen: false,
    items: [
      { key: "log_level", label: "日志详细程度", desc: "推荐使用标准日志；遇到问题时临时启用调试。", type: "select", effect: "immediate", options: [{ value: "INFO", label: "标准（推荐）" }, { value: "DEBUG", label: "详细调试" }, { value: "WARNING", label: "仅警告和错误" }, { value: "ERROR", label: "仅错误" }, { value: "CRITICAL", label: "仅严重错误" }] },
    ],
  },
];
