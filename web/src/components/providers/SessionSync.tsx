"use client";

import { useEffect } from "react";
import { useSessionStore } from "@/stores/session-store";
import {
  refreshSessionMessagesFromBackend,
  useChatStore,
} from "@/stores/chat-store";
import { subscribeToSession } from "@/lib/chat-actions";
import { useUIStore } from "@/stores/ui-store";
import { fetchSessionDetail, fetchSessions } from "@/lib/api";
import { buildDefaultSessionTitle } from "@/lib/session-title";
import type { Session, AssistantBlock } from "@/lib/types";

/**
 * 刷新后恢复路由状态 block：在最后一个 assistant 消息的 blocks 开头注入路由信息，
 * 仅当该消息尚未包含 route variant 的 status block 时执行。
 */
function _injectRouteBlock(
  chat: ReturnType<typeof useChatStore.getState>,
  route: { routeMode: string; skillsUsed: string[] },
) {
  const msgs = chat.messages;
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m.role !== "assistant") continue;
    // 已有 route status block 则跳过
    if (m.blocks.some((b) => b.type === "status" && b.variant === "route")) return;
    const routeBlock: AssistantBlock = {
      type: "status",
      label: `路由: ${route.routeMode}`,
      detail: route.skillsUsed.length > 0 ? `技能: ${route.skillsUsed.join(", ")}` : undefined,
      variant: "route",
    };
    const updatedBlocks = [routeBlock, ...m.blocks];
    const updatedMsgs = [...msgs];
    updatedMsgs[i] = { ...m, blocks: updatedBlocks };
    chat.setMessages(updatedMsgs);
    return;
  }
}

/**
 * 将最后一个 assistant 消息中最后一个 running/success 状态的 tool_call 标记为 pending，
 * 用于刷新后恢复审批弹窗时同步工具调用卡片的视觉状态。
 */
function _markLastToolCallPending(chat: ReturnType<typeof useChatStore.getState>) {
  const msgs = chat.messages;
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m.role !== "assistant") continue;
    for (let j = m.blocks.length - 1; j >= 0; j--) {
      const b = m.blocks[j];
      if (b.type === "tool_call" && (b.status === "running" || b.status === "success")) {
        const updatedBlocks = [...m.blocks];
        updatedBlocks[j] = { ...b, status: "pending" as const };
        const updatedMsgs = [...msgs];
        updatedMsgs[i] = { ...m, blocks: updatedBlocks };
        chat.setMessages(updatedMsgs);
        return;
      }
    }
    break; // only check the last assistant message
  }
}

export function SessionSync() {
  const mergeSessions = useSessionStore((s) => s.mergeSessions);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const abortController = useChatStore((s) => s.abortController);
  const switchSession = useChatStore((s) => s.switchSession);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const setFullAccessEnabled = useUIStore((s) => s.setFullAccessEnabled);
  const setChatMode = useUIStore((s) => s.setChatMode);
  const setCurrentModel = useUIStore((s) => s.setCurrentModel);

  const setActiveSession = useSessionStore((s) => s.setActiveSession);

  useEffect(() => {
    let cancelled = false;

    const syncSessions = async () => {
      try {
        const raw = await fetchSessions({ includeArchived: true });
        if (cancelled) return;
        const mapped: Session[] = (raw as Record<string, unknown>[]).map((s) => ({
          id: s.id as string,
          title:
            (typeof s.title === "string" ? s.title.trim() : "")
            || buildDefaultSessionTitle(s.id as string),
          messageCount: (s.message_count as number) ?? 0,
          inFlight: (s.in_flight as boolean) ?? false,
          updatedAt: s.updated_at as string | undefined,
          status: s.status === "archived" ? "archived" : "active",
        }));
        mergeSessions(mapped);

        // If activeSessionId (restored from localStorage) doesn't match
        // any known backend session, clear it to avoid stale 404 polling
        // storms.  Skip if there's an active SSE stream (optimistic create
        // that hasn't reached the server yet).
        const currentActive = useSessionStore.getState().activeSessionId;
        if (currentActive && !mapped.some((s) => s.id === currentActive)) {
          const hasActiveStream = useChatStore.getState().abortController !== null;
          if (!hasActiveStream) {
            setActiveSession(null);
          }
        }
      } catch {
        // ignore
      }
    };

    void syncSessions();
    const timer = window.setInterval(() => {
      void syncSessions();
    }, 15000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [mergeSessions, setActiveSession]);

  useEffect(() => {
    // Never auto-switch while a local SSE stream is active; let the stream
    // settle first to avoid wiping optimistic in-flight messages.
    if (abortController) return;

    if (!activeSessionId) {
      if (currentSessionId !== null) {
        switchSession(null);
      }
      return;
    }
    if (currentSessionId !== activeSessionId) {
      switchSession(activeSessionId);
    }
  }, [activeSessionId, currentSessionId, abortController, switchSession]);

  useEffect(() => {
    if (!activeSessionId) {
      setFullAccessEnabled(false);
      // Don't reset chatMode here — it's user-driven state (ChatModeTabs).
      // Resetting it causes the mode to snap back to "write" a few seconds
      // after the user switches to "read" or "plan" while idle.
      // Don't reset currentModel here — TopModelSelector independently
      // manages the global active model name via /models API.  Clearing
      // it causes a visual flash where the toolbar briefly shows "模型"
      // before TopModelSelector re-fetches the value.
      // Only kill streaming state if there is no active SSE connection.
      // Otherwise the stream callback will keep writing to a "stopped" store.
      if (!useChatStore.getState().abortController) {
        setStreaming(false);
      }
      return;
    }

    let cancelled = false;
    const prevInFlightRef = { current: false };
    let initialLoadDone = false;
    let notFoundCount = 0;
    const NOT_FOUND_THRESHOLD = 2;
    const pollDetail = async () => {
      try {
        const detail = await fetchSessionDetail(activeSessionId);
        if (cancelled) {
          return;
        }

        if (!detail) {
          // Session not yet known to backend (created locally, first message
          // hasn't reached the server). Silently skip — do NOT remove the
          // session or it will break the optimistic-create flow.
          // 但如果连续多次 404，说明会话确实不存在（如后端重启），清理 activeSessionId。
          // 如果存在活跃的 SSE 流（abortController !== null），说明消息正在发送中，
          // 后端可能还未来得及注册该会话，不要重置。
          notFoundCount++;
          const hasActiveStream = useChatStore.getState().abortController !== null;
          if (notFoundCount >= NOT_FOUND_THRESHOLD && !hasActiveStream) {
            setActiveSession(null);
          }
          return;
        }

        // 收到有效响应，重置计数器
        notFoundCount = 0;

        setFullAccessEnabled(detail.fullAccessEnabled);
        // NOTE: 不要在轮询中用后端 chatMode 覆盖前端状态。
        // chatMode 的权威来源是前端用户操作（ChatModeTabs 点击），
        // 后端 _current_chat_mode 只在 engine.chat() 调用时更新，
        // 轮询覆盖会导致用户切换模式后几秒被重置回旧值。
        // 后端主动推送的模式变更（SSE mode_changed 事件）仍然生效。
        const modelName = detail.currentModelName || detail.currentModel;
        setCurrentModel(modelName ?? "");

        // IMPORTANT:
        // pollDetail is async and can race with optimistic local sendMessage().
        // Always re-read latest chat state before any destructive refresh to
        // avoid wiping freshly appended local user/assistant bubbles.
        const chat = useChatStore.getState();
        const hasLocalLiveStream = chat.abortController !== null;

        // 页面刷新后没有本地 stream 连接时，用后端 in_flight 状态接管。
        if (!hasLocalLiveStream) {
          // 仅在以下情况刷新消息（避免轮询期间替换消息数组导致编辑状态丢失）：
          // 1) 首次加载且本地消息为空（页面刷新恢复）
          // 2) inFlight 刚从 true -> false（后端处理完毕），做最终同步
          // 移除了 detail.inFlight 条件，避免在流式处理期间持续替换消息
          const wasInFlight = prevInFlightRef.current;
          const shouldRefresh =
            (!initialLoadDone && chat.messages.length === 0)
            || (wasInFlight && !detail.inFlight);
          prevInFlightRef.current = detail.inFlight;
          initialLoadDone = true;

          if (shouldRefresh) {
            const latestChat = useChatStore.getState();
            // 确保没有活跃的 SSE 连接且不在流式处理中
            if (latestChat.abortController === null && !latestChat.isStreaming) {
              // 首次加载需要 messages 为空才刷新；inFlight→false 始终刷新（权威最终同步）
              const isInitialEmpty = !wasInFlight && latestChat.messages.length === 0;
              const isTaskJustFinished = wasInFlight && !detail.inFlight;
              if (isInitialEmpty || isTaskJustFinished) {
                await refreshSessionMessagesFromBackend(activeSessionId);
              }
            }
          }

          // SSE 重连：检测到后端仍在处理且前端无活跃 SSE 连接时，
          // 自动调用 subscribeToSession 重新接入事件流。
          if (detail.inFlight) {
            const latest = useChatStore.getState();
            if (!latest.abortController && !latest.isStreaming) {
              // 先设置 streaming 避免下一轮 poll 重复触发
              latest.setStreaming(true);
              subscribeToSession(activeSessionId).catch(() => {
                // subscribe 失败时回退到轮询模式
                const s = useChatStore.getState();
                if (!s.abortController) s.setStreaming(false);
              });
            }
          } else if (detail.inFlight !== chat.isStreaming) {
            chat.setStreaming(detail.inFlight);
          }

          // 注意：refreshSessionMessagesFromBackend 已更新 store，需重新获取最新状态
          const freshChat = useChatStore.getState();

          // 恢复路由状态 block（刷新后丢失的 SSE route_end 产物）
          if (detail.lastRoute && detail.lastRoute.routeMode) {
            _injectRouteBlock(useChatStore.getState(), detail.lastRoute);
          }

          // 恢复待处理审批弹窗（刷新后丢失的瞬态状态）
          if (detail.pendingApproval && !freshChat.pendingApproval) {
            freshChat.setPendingApproval(detail.pendingApproval);
            // 同时将最后一个匹配的 tool_call block 标记为 pending
            _markLastToolCallPending(useChatStore.getState());
          } else if (!detail.pendingApproval && freshChat.pendingApproval) {
            freshChat.setPendingApproval(null);
          }

          // 恢复待处理问题弹窗
          if (detail.pendingQuestion && !freshChat.pendingQuestion) {
            freshChat.setPendingQuestion(detail.pendingQuestion);
          } else if (!detail.pendingQuestion && freshChat.pendingQuestion) {
            freshChat.setPendingQuestion(null);
          }
        }
      } catch {
        if (cancelled) {
          return;
        }
        // Network error or non-404 server error — reset UI toggles but
        // keep the session entry intact.  Don't reset currentModel —
        // it is managed independently by TopModelSelector.
        // Don't reset chatMode either — it's user-driven state.
        setFullAccessEnabled(false);
      }
    };

    // 自适应轮询：inFlight 时 2s 高频同步，空闲时 5s 低频检查
    const POLL_FAST = 2000;
    const POLL_IDLE = 5000;
    let currentInterval = POLL_FAST;
    let timer = window.setTimeout(function schedule() {
      void pollDetail().then(() => {
        if (cancelled) return;
        const isActive = prevInFlightRef.current;
        const nextInterval = isActive ? POLL_FAST : POLL_IDLE;
        if (nextInterval !== currentInterval) {
          currentInterval = nextInterval;
        }
        timer = window.setTimeout(schedule, currentInterval);
      });
    }, 0); // 首次立即触发

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [
    activeSessionId,
    setActiveSession,
    setStreaming,
    setCurrentModel,
    setFullAccessEnabled,
    setChatMode,
  ]);

  return null;
}
