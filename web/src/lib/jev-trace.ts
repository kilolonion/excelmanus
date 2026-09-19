export const JEV_PACKS = [
  { id: "exposure.turn", title: "回合入口", hint: "本轮可用工具与模式建议" },
  { id: "skill.pin", title: "技能置顶", hint: "只置顶可能相关的技能，不会自动调用" },
  { id: "approval.tool_call", title: "审批建议", hint: "高风险操作建议放行、询问或拒绝" },
  { id: "observation.shape", title: "结果收起", hint: "过长的工具结果如何收起" },
  { id: "loop.wrap", title: "是否继续", hint: "这一步之后建议继续还是停下" },
  { id: "ui.surface", title: "界面建议", hint: "是否打开侧栏或表格页" },
  { id: "observation.prune", title: "历史收起", hint: "收起已经用过的历史结果" },
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
    latencyMs: typeof data.latency_ms === "number" ? data.latency_ms : Number(data.latency_ms) || 0,
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
  const off = opts.traces.some((item) => item.pack === "exposure.turn" && (item.gate === "off" || item.reason === "disabled"));
  if (off) return "当前已关闭，本回合未评估";
  if (opts.streaming && opts.traces.length === 0) return "本回合尚未评估";
  if (opts.traces.length === 0) return "本回合尚未评估";
  return "";
}

export function cardTone(trace: JevTrace): "shadow" | "applied" | "unavailable" | "deny" | "ask" {
  if (trace.kind === "deny" || trace.action === "deny") return "deny";
  if (trace.kind === "ask" || trace.action === "ask") return "ask";
  if (trace.transport === "unavailable" || trace.gate === "off") return "unavailable";
  if (trace.applied) return "applied";
  return "shadow";
}

export function formatLatency(ms: number): string {
  if (!ms || ms <= 0) return "—";
  if (ms < 10) return `${ms.toFixed(1)} ms`;
  return `${Math.round(ms)} ms`;
}

export function formatConfidence(value: unknown): string | null {
  if (typeof value !== "number" || Number.isNaN(value)) return null;
  return `${Math.round(value * 100)}%`;
}

export const ANSWER_LABELS: Record<string, string> = {
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
};
