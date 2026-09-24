import { create } from "zustand";
import type { DispatchReceipt } from "@/lib/types";

export const DISPATCH_LABELS = { steer: "引导当前任务", interrupt: "中断并发送", queue: "排队发送" } as const;
export const DISPATCH_DESCRIPTIONS = {
  steer: "补充当前任务的要求，在下一步生效。",
  interrupt: "停止当前任务，待正在执行的操作收尾后处理新消息。",
  queue: "等待当前任务完成，再处理新消息。",
} as const;
export const DISPATCH_STATUS = {
  queued: "等待处理", interrupt_pending: "正在停止当前任务", applying: "正在生效",
  applied: "已生效", completed: "已处理", failed: "处理失败", cancelled: "已取消", interrupted: "已中断",
} as const;

interface DispatchState {
  sessions: Record<string, Record<string, DispatchReceipt>>;
  upsert: (sessionId: string, receipt: DispatchReceipt) => void;
}

export const useDispatchStore = create<DispatchState>((set) => ({
  sessions: {},
  upsert: (sessionId, receipt) => set((state) => {
    if (!sessionId || !receipt.client_message_id || !receipt.dispatch_id) return state;
    const bucket = state.sessions[sessionId] ?? {};
    const previous = bucket[receipt.client_message_id];
    // HTTP acknowledgement can arrive after a newer SSE state.
    if (previous && previous.revision >= receipt.revision) return state;
    return { sessions: { ...state.sessions, [sessionId]: { ...bucket, [receipt.client_message_id]: receipt } } };
  }),
}));
