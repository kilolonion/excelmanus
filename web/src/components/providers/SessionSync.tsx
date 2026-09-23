"use client";

import { useEffect, useRef, useState } from "react";
import { useSessionStore, waitForSessionHydration } from "@/stores/session-store";
import {
  refreshSessionMessagesFromBackend,
  useChatStore,
} from "@/stores/chat-store";
import { subscribeToSession } from "@/lib/chat-actions";
import { useUIStore } from "@/stores/ui-store";
import { useJevStore } from "@/stores/jev-store";
import { jevChatEnabledFromRuntime } from "@/lib/jev-settings";
import { fetchSessionDetail, fetchSessions, apiGet } from "@/lib/api";
import { parseChatMode, shouldHydrateChatMode } from "@/lib/chat-mode-hydrate";
import { buildDefaultSessionTitle } from "@/lib/session-title";
import { isPlaceholderModelId } from "@/lib/model-display";
import { normalizeThinkingEffortOptions } from "@/lib/thinking";
import { ensureLandingSession } from "@/lib/session-actions";
import type { Session } from "@/lib/types";
import { DEMO_SESSION_PREFIX } from "@/components/onboarding/demo-session";

/**
 * 将最后一个 assistant 消息中最后一个 running/success 状态的 tool_call 标记为 pending，
 * 用于刷新后恢复路由状态时避免工具调用卡片的应用状态。
 */
function _markLastToolCallPending(chat: ReturnType<typeof useChatStore.getState>) {
  const msgs = chat.messages;
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m.role !== "assistant") continue;
    for (let j = m.blocks.length - 1; j >= 0; j--) {
      const b = m.blocks[j];
      if (b.type === "tool_call" && (b.status === "running" || b.status === "success")) {
        chat.updateAssistantMessage(m.id, (message) => {
          const updatedBlocks = [...message.blocks];
          const block = updatedBlocks[j];
          if (block?.type === "tool_call") {
            updatedBlocks[j] = { ...block, status: "pending" as const };
          }
          return { ...message, blocks: updatedBlocks };
        });
        return;
      }
    }
    break; // 查找最后一条 assistant 消息
  }
}

export function SessionSync() {
  const mergeSessions = useSessionStore((s) => s.mergeSessions);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const abortController = useChatStore((s) => s.abortController);
  const switchSession = useChatStore((s) => s.switchSession);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const setFullAccessEnabled = useUIStore((s) => s.setFullAccessEnabled);
  const setAutoApproveEnabled = useUIStore((s) => s.setAutoApproveEnabled);
  const setVisionCapable = useUIStore((s) => s.setVisionCapable);
  const setCurrentModel = useUIStore((s) => s.setCurrentModel);
  const setThinkingEffort = useUIStore((s) => s.setThinkingEffort);
  const setThinkingEffortOptions = useUIStore((s) => s.setThinkingEffortOptions);

  const setActiveSession = useSessionStore((s) => s.setActiveSession);
  const hydratedChatModeSessionRef = useRef<string | null>(null);
  const landingEnsuredRef = useRef(false);
  const [sessionsReady, setSessionsReady] = useState(false);

  // 启动时拉取 thinking config 同步到 store
  useEffect(() => {
    apiGet<{ effort: string; allowed_efforts?: string[] }>("/thinking")
      .then((data) => {
        if (data.effort) setThinkingEffort(data.effort);
        setThinkingEffortOptions(normalizeThinkingEffortOptions(data.allowed_efforts));
      })
      .catch(() => {});
  }, [setThinkingEffort, setThinkingEffortOptions]);

  useEffect(() => {
    apiGet<{
      jev_enabled?: string;
      jev_active_provider?: string;
      ai_gateway?: { configured?: boolean };
      typesafe?: { configured?: boolean };
      jev_providers?: { id?: string; protocol?: string; configured?: boolean }[];
    }>("/config/runtime")
      .then((data) => {
        useJevStore.getState().setChatEnabled(jevChatEnabledFromRuntime(data));
      })
      .catch(() => {
        useJevStore.getState().setChatEnabled(false);
      });
  }, []);

  useEffect(() => {
    let cancelled = false;
    let syncing = false;
    const controller = new AbortController();

    const syncSessions = async () => {
      if (cancelled || syncing || document.hidden) return;
      syncing = true;
      try {
        await waitForSessionHydration();
        if (cancelled) return;
        const raw = await fetchSessions({ signal: controller.signal });
        if (cancelled) return;
        const mapped: Session[] = (raw as Record<string, unknown>[]).map((s) => ({
          id: s.id as string,
          title:
            (typeof s.title === "string" ? s.title.trim() : "")
            || buildDefaultSessionTitle(s.id as string),
          messageCount: (s.message_count as number) ?? 0,
          inFlight: (s.in_flight as boolean) ?? false,
          updatedAt: s.updated_at as string | undefined,
          workspacePath: typeof s.workspace_path === "string" ? s.workspace_path : undefined,
          workspaceId: (s.workspace_id as string | null | undefined) ?? undefined,
          workspaceTitle: typeof s.workspace_title === "string" ? s.workspace_title : undefined,
          blank: Boolean(s.blank),
          pendingApproval: Boolean(s.pending_approval),
          pendingQuestion: Boolean(s.pending_question),
        }));
        mergeSessions(mapped);

        // 若 activeSessionId（从 localStorage 恢复）与后端已知会话都不匹配，则清空以避免刚启动时 404 错误。
        // 若有活跃 SSE 流（本地创建尚未到达服务端）则跳过。
        // Demo sessions are local-only — never prune them via backend sync.
        const currentActive = useSessionStore.getState().activeSessionId;
        if (currentActive && !currentActive.startsWith(DEMO_SESSION_PREFIX) && !mapped.some((s) => s.id === currentActive)) {
          {
            const hasActiveStream = useChatStore.getState().abortController !== null;
            if (!hasActiveStream) {
              // 若本地会话列表中没有找到匹配的会话，则清空 activeSessionId。
              // 若有活跃 SSE 流，则跳过。
              // 保护本地新建但尚未发送首条消息的会话：检查 session-store 中是否存在
              // 存在该会话且 messageCount === 0 且创建不超过 60 秒。
              const localSession = useSessionStore.getState().sessions.find((s) => s.id === currentActive);
              const GRACE_MS = 60_000;
              const isLocalUnsent = localSession
                && localSession.messageCount === 0
                && localSession.createdAt
                && (Date.now() - localSession.createdAt) < GRACE_MS;
              if (!isLocalUnsent) {
                setActiveSession(null);
              }
            }
          }
        }

        setSessionsReady(true);
        const afterActive = useSessionStore.getState().activeSessionId;
        if (afterActive?.startsWith(DEMO_SESSION_PREFIX)) {
          return;
        }
        if (!landingEnsuredRef.current || !afterActive) {
          try {
            await ensureLandingSession();
            if (!cancelled) landingEnsuredRef.current = true;
          } catch {
            // 下次轮询再补建空白新对话
          }
        }
      } catch {
        // 蹇界暐
      } finally {
        syncing = false;
      }
    };

    void syncSessions();
    const onVisible = () => { if (!document.hidden) void syncSessions(); };
    document.addEventListener("visibilitychange", onVisible);
    const timer = window.setInterval(() => {
      void syncSessions();
    }, 15_000);

    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [mergeSessions, setActiveSession]);

  useEffect(() => {
    if (!sessionsReady) return;
    if (activeSessionId?.startsWith(DEMO_SESSION_PREFIX)) return;
    if (activeSessionId) return;
    void ensureLandingSession().catch(() => {});
  }, [sessionsReady, activeSessionId]);

  useEffect(() => {
    // 本地 SSE 流活跃时不自动切换会话；等流结束再切换，避免清空乐观进行中的消息。
    if (abortController) return;

    // Demo sessions are fully managed by CoachMarks — skip async switchSession
    // which would wipe mock messages by trying to load from IDB/backend.
    if (activeSessionId?.startsWith(DEMO_SESSION_PREFIX)) return;

    // 单向：URL/session-store → 加载 chat 消息。switchSession 不回写 session id。
    switchSession(activeSessionId);
  }, [activeSessionId, abortController, switchSession]);

  useEffect(() => {
    if (!activeSessionId) {
      setFullAccessEnabled(false);
      setAutoApproveEnabled(false);
      // 不重置 chatMode：它是用户点选（ChatModeTabs）。轮询覆盖会把 read/plan 弹回 write。
      // 后端主动切换（/plan、批准退出）走 SSE mode_changed（mode_name=chat_mode, value）。
      // 不重置 currentModel：TopModelSelector 通过 /models API 独立管理全局模型名。
      // 仅在没有活跃 SSE 连接时清除流式状态，否则流回调会继续写入已「停止」的 store。
      if (!useChatStore.getState().abortController) {
        setStreaming(false);
      }
      return;
    }

    // Demo sessions are local-only — skip backend polling entirely.
    if (activeSessionId.startsWith(DEMO_SESSION_PREFIX)) return;

    let cancelled = false;
    const controller = new AbortController();
    const prevInFlightRef = { current: false };
    let snapshotValidated = false;
    let previousHistoryRevision = "";
    let polling = false;
    let notFoundCount = 0;
    const NOT_FOUND_THRESHOLD = 2;
    if (hydratedChatModeSessionRef.current !== activeSessionId) {
      useUIStore.getState().releaseChatModeOwnership();
    }
    const hydrateChatModeOnce = (chatMode: unknown) => {
      const ui = useUIStore.getState();
      if (
        !shouldHydrateChatMode({
          sessionId: activeSessionId,
          hydratedSessionId: hydratedChatModeSessionRef.current,
          owned: ui.chatModeOwned,
        })
      ) {
        return;
      }
      const mode = parseChatMode(chatMode);
      if (!mode) return;
      ui.hydrateChatMode(mode);
      hydratedChatModeSessionRef.current = activeSessionId;
    };
    const pollDetail = async () => {
      if (cancelled || polling || document.hidden) return;
      polling = true;
      try {
        const modelProfileVersion = useUIStore.getState().modelProfileVersion;
        // 状态轮询不需要消息正文；历史由 chat-store 按页加载。
        const detail = await fetchSessionDetail(activeSessionId, {
          includeMessages: false,
          signal: controller.signal,
        });
        if (cancelled) {
          return;
        }

        if (!detail) {
          // 浼氳瘽灏氭湭琚悗绔煡鏅擄紙鏈湴鍒涘缓锛岄鏉℃秷鎭湭鍒版湇鍔＄锛夈€傞潤榛樿烦杩囷紝涓嶈绉婚櫎浼氳瘽锛屽惁鍒欎細鐮村潖涔愯鍒涘缓娴佺▼銆?
          // 浣嗗鏋滆繛缁娆?404锛岃鏄庝細璇濈‘瀹炰笉瀛樺湪锛堝鍚庣閲嶅惎锛夛紝娓呯悊 activeSessionId銆?
          // 濡傛灉瀛樺湪娲昏穬鐨?SSE 娴侊紙abortController !== null锛夛紝璇存槑娑堟伅姝ｅ湪鍙戦€佷腑锛?
          // 鍚庣鍙兘杩樻湭鏉ュ緱鍙婃敞鍐岃浼氳瘽锛屼笉瑕侀噸缃€?
          notFoundCount++;
          const hasActiveStream = useChatStore.getState().abortController !== null;
          if (notFoundCount >= NOT_FOUND_THRESHOLD && !hasActiveStream) {
            // 淇濇姢鏈湴鏂板缓浣嗗皻鏈彂閫侀鏉℃秷鎭殑浼氳瘽锛岄伩鍏嶈疆璇㈣娓呫€?
            const localSession = useSessionStore.getState().sessions.find((s) => s.id === activeSessionId);
            const GRACE_MS = 60_000;
            const isLocalUnsent = localSession
              && localSession.messageCount === 0
              && localSession.createdAt
              && (Date.now() - localSession.createdAt) < GRACE_MS;
            if (!isLocalUnsent) {
              setActiveSession(null);
            }
          }
          return;
        }

        // 鏀跺埌鏈夋晥鍝嶅簲锛岄噸缃鏁板櫒
        notFoundCount = 0;
        consecutiveErrors = 0;

        setFullAccessEnabled(detail.fullAccessEnabled);
        setAutoApproveEnabled(detail.autoApproveEnabled);
        // 占位会话（engine 尚未创建）会返回 vision_capable=null；不要把未知当成不支持。
        const modelSnapshotIsCurrent = modelProfileVersion === useUIStore.getState().modelProfileVersion;
        if (modelSnapshotIsCurrent && detail.currentModel != null && typeof detail.visionCapable === "boolean") {
          setVisionCapable(detail.visionCapable);
        }
        // 每个 session 只 hydrate 一次 chat_mode。轮询不得覆盖用户点选；/plan 走 SSE。
        hydrateChatModeOnce(detail.chatMode);
        const modelName = detail.currentModelName || detail.currentModel;
        if (modelSnapshotIsCurrent && modelName && !isPlaceholderModelId(modelName)) setCurrentModel(modelName);

        // 閲嶈锛歱ollDetail 涓哄紓姝ワ紝鍙兘涓庝箰瑙傛湰鍦?sendMessage() 绔炴€併€?
        // 鍦ㄤ换浣曚細瑕嗙洊娑堟伅鐨勫埛鏂板墠锛屽姟蹇呴噸鏂拌鍙栨渶鏂?chat 鐘舵€侊紝閬垮厤鎿﹂櫎鍒氳拷鍔犵殑鏈湴 user/assistant 姘旀场銆?
        const chat = useChatStore.getState();
        const detailStreamId = detail.activeStreamId ?? null;
        const detailLatestSeq = Math.max(0, detail.latestSeq ?? 0);
        const historyRevision = detail.historyRevision ?? "";
        const historyRevisionChanged = Boolean(
          historyRevision
          && previousHistoryRevision
          && historyRevision !== previousHistoryRevision,
        );
        if (!chat.abortController) {
          if (detail.inFlight && detailStreamId) {
            chat.setStreamState(detailStreamId, Math.max(chat.latestSeq, detailLatestSeq));
          } else if (!detail.inFlight) {
            chat.setStreamState(null, 0);
            chat.clearResumeFailed();
          }
        }
        const hasLocalLiveStream = chat.abortController !== null;
        prevInFlightRef.current = detail.inFlight;

        // 椤甸潰鍒锋柊鍚庢病鏈夋湰鍦?stream 杩炴帴鏃讹紝鐢ㄥ悗绔?in_flight 鐘舵€佹帴绠°€?
        if (!hasLocalLiveStream) {
          // 仅在快照校验失败时回源，避免常态全量刷新。
          if (detail.inFlight) {
            snapshotValidated = false;
          } else if (!snapshotValidated || historyRevisionChanged) {
            snapshotValidated = true;
            const latestChat = useChatStore.getState();
            if (latestChat.abortController === null && !latestChat.isStreaming
              && !latestChat.isLoadingMessages && latestChat.loadedSessionId === activeSessionId) {
              const remoteCount = Math.max(0, detail.messageCount ?? 0);
              // loadedMessageTotal tracks the server total even when only the
              // newest page is present locally. Comparing against visible rows
              // would otherwise re-fetch the tail on every poll.
              const localCount = latestChat.loadedMessageTotal ?? latestChat.messageOrder.length;
              if (remoteCount !== localCount || historyRevisionChanged) {
                await refreshSessionMessagesFromBackend(activeSessionId);
              }
            } else {
              snapshotValidated = false;
            }
          }

          // SSE 閲嶈繛锛氭娴嬪埌鍚庣浠嶅湪澶勭悊涓斿墠绔棤娲昏穬 SSE 杩炴帴鏃讹紝
          // 鑷姩璋冪敤 subscribeToSession 閲嶆柊鎺ュ叆浜嬩欢娴併€?
          if (detail.inFlight) {
            const latest = useChatStore.getState();
            if (!latest.abortController && !latest.isStreaming) {
              // 鍏堣缃?streaming 閬垮厤涓嬩竴杞?poll 閲嶅瑙﹀彂
              latest.setStreaming(true);
              subscribeToSession(activeSessionId).catch(() => {
                // 璁㈤槄澶辫触鏃跺洖閫€鍒拌疆璇㈡ā寮?
                const s = useChatStore.getState();
                if (!s.abortController) s.setStreaming(false);
              });
            }
          } else if (detail.inFlight !== chat.isStreaming) {
            chat.setStreaming(detail.inFlight);
          }
          if (!detail.inFlight && historyRevision) previousHistoryRevision = historyRevision;

          // 娉ㄦ剰锛歳efreshSessionMessagesFromBackend 宸叉洿鏂?store锛岄渶閲嶆柊鑾峰彇鏈€鏂扮姸鎬?
          const freshChat = useChatStore.getState();

          // 鎭㈠璺敱鐘舵€?block锛堝埛鏂板悗涓㈠け鐨?SSE route_end 浜х墿锛?
          // 鎭㈠寰呭鐞嗗鎵瑰脊绐楋紙鍒锋柊鍚庝涪澶辩殑鐬€佺姸鎬侊級
          // 娉ㄦ剰锛氱敤鎴风偣鍑诲厑璁?鎷掔粷鍚庝細璁板綍 _lastDismissedApprovalId锛?
          // 闃叉 SessionSync 杞鍦ㄥ悗绔皻鏈鐞嗗畬瀹℃壒鏃舵妸寮圭獥閲嶆柊鎷夊洖鏉?
          if (detail.pendingApproval && !freshChat.pendingApproval) {
            const dismissed = freshChat._lastDismissedApprovalId;
            const incomingId = (detail.pendingApproval as { id?: string })?.id
              ?? (detail.pendingApproval as { approval_id?: string })?.approval_id;
            // 后端只应返回仍可提交的审批；本地再拦一次已关闭单据。
            if (incomingId && dismissed !== incomingId) {
              freshChat.setPendingApproval(detail.pendingApproval);
              _markLastToolCallPending(useChatStore.getState());
            }
          } else if (!detail.pendingApproval && freshChat.pendingApproval) {
            freshChat.dismissApproval(freshChat.pendingApproval.id);
          }

          // 鎭㈠寰呭鐞嗛棶棰樺脊绐?
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
        // 缃戠粶閿欒锛堝鏂綉/瓒呮椂锛夛細涓嶉噸缃?UI 寮€鍏筹紝閬垮厤鐬椂缃戠粶娉㈠姩瀵艰嚧鐢ㄦ埛涓㈠け Full Access 鐘舵€併€?
        // fullAccessEnabled 绛?UI 鐘舵€佷細鍦ㄤ笅涓€娆℃垚鍔熻疆璇㈡椂鑷劧鎭㈠銆?
        consecutiveErrors++;
      } finally {
        polling = false;
      }
    };

    // 鑷€傚簲杞锛歩nFlight 鏃?2s 楂橀鍚屾锛岀┖闂叉椂 5s 浣庨妫€鏌?
    // 杩炵画澶辫触鏃舵寚鏁伴€€閬匡紙鏈€澶?30s锛夛紝鎴愬姛鏃堕噸缃?
    // 鎬ц兘浼樺寲锛氶娆″欢杩?800ms 鍐嶈Е鍙戯紝閬垮厤涓?switchSession 鐨勬秷鎭姞杞界珵鎬?
    const POLL_FAST = 2000;
    const POLL_IDLE = 5000;
    const POLL_MAX_BACKOFF = 30_000;
    const POLL_INITIAL_DELAY = 800;
    let currentInterval = POLL_FAST;
    let consecutiveErrors = 0;
    // chat_mode 首次 hydrate 不等轮询延迟，避免 F5 后 tab 先闪回 write。
    // One initial request also hydrates chat mode; avoid a second detail fetch
    // just for that field. Message validation waits for the session loader.
    void pollDetail();
    const onVisible = () => { if (!document.hidden) void pollDetail(); };
    document.addEventListener("visibilitychange", onVisible);
    let timer = window.setTimeout(function schedule() {
      void pollDetail().then(() => {
        if (cancelled) return;
        const isActive = prevInFlightRef.current;
        let nextInterval = isActive ? POLL_FAST : POLL_IDLE;
        // 杩炵画澶辫触鏃舵寚鏁伴€€閬?
        if (consecutiveErrors > 0) {
          nextInterval = Math.min(nextInterval * Math.pow(2, consecutiveErrors), POLL_MAX_BACKOFF);
        }
        currentInterval = nextInterval;
        timer = window.setTimeout(schedule, currentInterval);
      });
    }, POLL_INITIAL_DELAY); // 寤惰繜棣栨瑙﹀彂锛岄伩鍏嶄笌 switchSession 绔炴€?

    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [
    activeSessionId,
    setActiveSession,
    setStreaming,
    setCurrentModel,
    setFullAccessEnabled,
    setAutoApproveEnabled,
    setVisionCapable,
  ]);

  return null;
}
