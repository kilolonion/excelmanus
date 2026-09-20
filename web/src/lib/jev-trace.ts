export const JEV_PACKS = [
  { id: "context.resolve", title: "上下文判断", hint: "推荐工作区、定位表格与选区，补充澄清和下一步建议" },
  { id: "exposure.turn", title: "回合入口", hint: "本轮可用工具与模式建议" },
  { id: "skill.pin", title: "技能置顶", hint: "只置顶可能相关的技能，不会自动调用" },
  { id: "approval.tool_call", title: "审批建议", hint: "高风险操作建议放行、询问或拒绝" },
  { id: "observation.shape", title: "结果收起", hint: "过长的工具结果如何收起" },
  { id: "loop.wrap", title: "是否继续", hint: "这一步之后建议继续还是停下" },
  { id: "ui.surface", title: "界面建议", hint: "是否打开侧栏或表格页" },
  { id: "observation.prune", title: "历史收起", hint: "收起已经用过的历史结果" },
  { id: "mutation.verify", title: "写入验证", hint: "观察写入是否覆盖用户要求" },
  { id: "recovery.next_step", title: "失败恢复", hint: "失败熔断后建议检查、询问或停止" },
] as const;

export type JevPackId = (typeof JEV_PACKS)[number]["id"];
export type JevGate = "off" | "shadow" | "enforce";
export type JevTransport = "gateway" | "typesafe" | "unavailable";

export interface JevTrace {
  id: string;
  pack: string;
  gate: JevGate;
  applied: boolean;
  transport: JevTransport;
  latencyMs: number;
  action: string;
  kind: string;
  reason: string;
  answers: Record<string, string | number | boolean>;
  impact: string;
  at: number;
}

const PACK_IDS = new Set<string>(JEV_PACKS.map((item) => item.id));

function asGate(value: unknown): JevGate {
  if (value === "shadow" || value === "enforce" || value === "off") return value;
  return "off";
}

function asTransport(value: unknown): JevTransport {
  if (value === "gateway" || value === "typesafe" || value === "unavailable") return value;
  return "unavailable";
}

function asAnswers(raw: unknown): Record<string, string | number | boolean> {
  if (!raw || typeof raw !== "object") return {};
  const out: Record<string, string | number | boolean> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (key === "user_text" || key === "state" || key === "api_key" || /secret|token|authorization/i.test(key)) {
      continue;
    }
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      out[key] = typeof value === "string" ? value.slice(0, 80) : value;
    }
  }
  return out;
}

export function parseJevTrace(data: Record<string, unknown>, id: string): JevTrace | null {
  const pack = String(data.pack || "");
  if (!pack || pack.length > 64) return null;
  return {
    id,
    pack,
    gate: asGate(data.gate),
    applied: Boolean(data.applied),
    transport: asTransport(data.transport),
    latencyMs: Number.isFinite(Number(data.latency_ms)) ? Math.max(0, Number(data.latency_ms)) : 0,
    action: String(data.action || "noop").slice(0, 80),
    kind: String(data.kind || "noop").slice(0, 40),
    reason: String(data.reason || "").slice(0, 200),
    answers: asAnswers(data.answers),
    impact: String(data.impact || "").slice(0, 200),
    at: Date.now(),
  };
}

export function packTitle(pack: string): string {
  return JEV_PACKS.find((item) => item.id === pack)?.title || pack;
}

export function packHint(pack: string): string {
  return JEV_PACKS.find((item) => item.id === pack)?.hint || "";
}

export function isKnownPack(pack: string): pack is JevPackId {
  return PACK_IDS.has(pack);
}

export function jevEmptyCopy(opts: {
  streaming: boolean;
  traces: readonly JevTrace[];
}): string {
  const off = opts.traces.length > 0 && opts.traces.every((item) => item.gate === "off" || item.reason === "disabled");
  if (off) return "当前已关闭，本回合未评估";
  if (opts.streaming && opts.traces.length === 0) return "本回合尚未评估";
  if (opts.traces.length === 0) return "本回合尚未评估";
  return "";
}

export function cardTone(trace: JevTrace): "shadow" | "applied" | "unavailable" | "deny" | "ask" {
  if (traceUnavailable(trace) || trace.gate === "off" || trace.reason === "disabled") return "unavailable";
  if (trace.applied && trace.gate === "enforce") {
    if (trace.kind === "deny" || trace.action === "deny") return "deny";
    if (trace.kind === "ask" || trace.action === "ask") return "ask";
    return "applied";
  }
  return "shadow";
}

export function formatLatency(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  if (ms < 10) return `${ms.toFixed(1)} ms`;
  return `${Math.round(ms)} ms`;
}

export function formatConfidence(value: unknown): string | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) return null;
  return `${Math.round(value * 100)}%`;
}

export const ANSWER_LABELS: Record<string, string> = {
  workspace: "工作区建议",
  target: "表格目标",
  edit_intent: "修改意图",
  target_source: "判断依据",
  column: "列匹配",
  column_header: "命中表头",
  read: "读取建议",
  routed_workspace: "已绑定到",
  domain: "领域",
  needs_write: "需要写入",
  mode_hint: "模式建议",
  confidence: "置信",
  is_chitchat: "寒暄",
  shape: "收起方式",
  surface: "界面",
  next: "下一步",
  pin: "置顶技能",
  action: "动作",
  satisfied: "是否完成",
  scope_ok: "范围符合",
  retryable: "可重试",
  needs_user: "需要用户",
  still_relevant: "后续仍需使用",
  done_enough: "完成程度",
  destructive: "破坏性风险",
  needs_skill: "需要技能",
};

export function formatContextAnswer(key: string, value: string | number | boolean): string {
  const labels: Record<string, Record<string, string>> = {
    workspace: { current: "当前工作区", existing: "已有工作区", new_blank: "新建空白工作区", ask: "待确定", none: "无需工作区" },
    target: { resolved: "已有定位建议", ask: "待确定", none: "无需已有表格" },
    edit_intent: { specified: "本轮已明确", from_context: "承接最近对话", unclear: "需补充修改要求", no_edit: "无需修改" },
    target_source: { explicit_mention: "用户明确引用", active_view: "当前表格或选区", recent_tool: "最近操作", file_catalog: "文件目录", sheet_catalog: "工作表目录" },
    column: { matched: "已匹配候选列", ask: "待确定", none: "无需列定位" },
    read: { overview: "先读概览", selection: "读选区/范围", column_sample: "读列样本", formulas: "读公式", none: "无需读取" },
    next: { clarify: "补充必要信息", inspect_target: "先读取目标", inspect_candidate: "先读取建议范围", continue: "继续处理" },
    domain: { inspect_only: "只读分析", spreadsheet_write: "表格编辑", word_doc: "文档处理", file_code: "文件与代码", web_lookup: "联网查询", chitchat: "日常问答", mixed: "综合任务" },
    mode_hint: { keep: "保持当前模式", suggest_read: "建议只读模式", suggest_plan: "建议计划模式", suggest_write: "建议编辑模式" },
    shape: { keep: "保留结果", truncate: "精简长结果", spill: "保存完整结果", pointer: "保留取回入口" },
    surface: { stay: "保持当前界面", side_panel: "表格侧栏", sheet_full: "完整表格", compare: "差异对比", files_tab: "文件列表", none: "无需切换" },
    action: { ask: "建议询问", deny: "建议拒绝", auto: "建议放行", allow: "建议放行", noop: "无需调整" },
  };
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return formatConfidence(value) ?? String(value);
  return labels[key]?.[String(value)] || String(value);
}

export function traceUnavailable(trace: JevTrace): boolean {
  return trace.transport === "unavailable" || /^(unavailable|error|timeout|budget_exhausted|provider_cooldown)(:|$)/.test(trace.reason);
}

export function traceStatus(trace: JevTrace): { label: string; description: string } {
  if (trace.gate === "off" || trace.reason === "disabled") {
    return { label: "已跳过", description: "这项功能已关闭，本次没有参与评估。" };
  }
  if (traceUnavailable(trace)) {
    const why = trace.reason === "budget_exhausted" ? "本轮评估额度已用完" : trace.reason === "provider_cooldown" ? "评估服务暂时冷却" : "本次评估未能完成";
    return { label: "已回退", description: `${why}，任务继续按原流程处理。` };
  }
  if (trace.gate === "shadow") return { label: "仅观察", description: "已记录建议，本次没有改变任务行为。" };
  if (!trace.applied) return { label: "未采纳", description: "已评估，本次未应用建议；任务按原流程处理。" };
  if (trace.pack === "context.resolve") {
    const routed = trace.answers.routed_workspace;
    if (typeof routed === "string" && routed) {
      return { label: "已路由工作区", description: `会话已绑定到工作区「${routed}」，可在会话列表中调整归属。` };
    }
    return { label: "已提供建议", description: "已向主模型补充上下文建议；未切换工作区或修改文件。" };
  }
  if (trace.kind === "deny" || trace.action === "deny") return { label: "已拦截", description: "已拒绝这次高风险操作。" };
  if (trace.kind === "ask" || trace.action === "ask") return { label: "交由审批", description: "这次操作仍需通过原有审批流程。" };
  const descriptions: Record<string, string> = {
    "exposure.turn": trace.impact.includes("已按") ? "已根据任务调整本轮可用工具。" : "已记录工具建议，本轮未缩减可用工具。",
    "skill.pin": "已调整相关技能的展示顺序，不会自动调用技能。",
    "observation.shape": "已应用结果整理策略，需要时可取回完整结果。",
    "observation.prune": trace.action === "prune" ? "已收起旧结果，保留取回入口。" : "已保留历史结果。",
    "loop.wrap": "已记录后续步骤建议，由主流程决定是否继续。",
    "ui.surface": "已提交界面建议，是否切换由当前界面状态决定。",
  };
  return { label: "已应用", description: descriptions[trace.pack] || "已应用本次评估建议。" };
}

export function traceActionLabel(trace: JevTrace): string {
  if (trace.gate === "off" || trace.reason === "disabled") return "未参与评估";
  if (traceUnavailable(trace)) return "继续原流程";
  if (trace.pack === "context.resolve") return formatContextAnswer("next", trace.action);
  const labels: Record<string, string> = {
    minimal: "无需工作区工具", full: "保留完整工具", inspect: "建议读取工具", edit: "建议编辑工具", file_code: "建议文件与代码工具", web: "建议联网工具",
    keep: "保持现状", none: "无需调整", noop: "无需调整", auto: "建议放行", allow: "建议放行", ask: "建议询问", deny: "建议拒绝",
    truncate: "精简长结果", spill: "保存完整结果", pointer: "保留取回入口", prune: "收起旧结果",
    continue: "建议继续", stop: "建议停止", wrap: "建议收尾", finish: "建议完成", clarify: "补充必要信息", inspect_target: "先读取目标", inspect_candidate: "先读取建议范围",
    retry: "建议重试", ask_user: "建议询问用户", inspect_more: "建议继续检查", verify: "建议验证结果", replan: "建议调整计划",
    stay: "保持当前界面", side_panel: "打开表格侧栏", sheet_full: "打开完整表格", compare: "查看差异", files_tab: "打开文件列表",
  };
  if (trace.pack === "skill.pin" && !["none", "noop"].includes(trace.action)) return `建议置顶 · ${trace.action}`;
  return labels[trace.action] || "查看评估建议";
}

export function traceNeedsAttention(trace: JevTrace): boolean {
  return traceUnavailable(trace) && trace.gate !== "off" && trace.reason !== "disabled" || ["deny", "ask"].includes(cardTone(trace));
}

export function summarizeTraces(traces: readonly JevTrace[]) {
  const evaluated = traces.filter((trace) => trace.gate !== "off" && trace.reason !== "disabled" && !traceUnavailable(trace));
  const measured = traces.filter((trace) => Number.isFinite(trace.latencyMs) && trace.latencyMs > 0);
  return {
    evaluated: evaluated.length,
    applied: evaluated.filter((trace) => trace.applied && trace.gate === "enforce").length,
    attention: traces.filter(traceNeedsAttention).length,
    meanMs: measured.length ? measured.reduce((sum, trace) => sum + trace.latencyMs, 0) / measured.length : 0,
  };
}
