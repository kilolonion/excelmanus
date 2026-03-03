/**
 * Demo session management for onboarding tour.
 * Creates a temporary session with mock streaming to showcase the UI.
 */
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useExcelStore } from "@/stores/excel-store";

// ── Constants ──

const DEMO_USER_TEXT = "帮我分析这个表格的销售数据趋势";

/** Well-known prefix so SessionSync can recognise and skip demo sessions. */
export const DEMO_SESSION_PREFIX = "__onboarding_demo__";

// ── Module state ──

let _originalSessionId: string | null | undefined; // undefined = not yet saved
let _demoSessionId: string | null = null;
let _mockStreamingTimer: ReturnType<typeof setInterval> | null = null;
let _demoToolCallIds: string[] = [];
let _demoPhaseTimers: ReturnType<typeof setTimeout>[] = [];

// ── Public API ──

/** Create a temporary demo session, saving the original one for later restore. */
export function ensureDemoSession() {
  if (_demoSessionId) return;

  const sessionStore = useSessionStore.getState();

  if (_originalSessionId === undefined) {
    _originalSessionId = sessionStore.activeSessionId;
  }

  const id = `${DEMO_SESSION_PREFIX}${crypto.randomUUID()}`;
  _demoSessionId = id;

  sessionStore.addSession({
    id,
    title: "引导演示",
    messageCount: 0,
    inFlight: false,
  });
  sessionStore.setActiveSession(id);
  useChatStore.setState({
    currentSessionId: id,
    messages: [],
    isLoadingMessages: false,
  });
}

/** Pre-fill input with demo text if empty. */
export function prefillDemoInput() {
  const textarea = document.querySelector(
    '[data-coach-id="coach-chat-input"] textarea'
  ) as HTMLTextAreaElement | null;
  if (textarea && !textarea.value.trim()) {
    const nativeSetter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value"
    )?.set;
    if (nativeSetter) {
      nativeSetter.call(textarea, DEMO_USER_TEXT);
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }
}

/** Clear the textarea value programmatically. */
export function clearDemoInput() {
  const textarea = document.querySelector(
    '[data-coach-id="coach-chat-input"] textarea'
  ) as HTMLTextAreaElement | null;
  if (textarea) {
    const nativeSetter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value"
    )?.set;
    if (nativeSetter) {
      nativeSetter.call(textarea, "");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }
}

/** Inject mock streaming data to simulate an AI response. */
export function injectMockStreaming() {
  const store = useChatStore.getState();
  const excelStore = useExcelStore.getState();

  const textarea = document.querySelector(
    '[data-coach-id="coach-chat-input"] textarea'
  ) as HTMLTextAreaElement | null;
  const userText = textarea?.value?.trim() || DEMO_USER_TEXT;

  clearDemoInput();

  const userMsgId = crypto.randomUUID();
  const assistantMsgId = crypto.randomUUID();
  const ts = Date.now();
  const readToolCallId = `demo-read-${ts}`;
  const writeToolCallId = `demo-write-${ts}`;
  _demoToolCallIds = [readToolCallId, writeToolCallId];

  const abortController = new AbortController();
  store.setAbortController(abortController);
  store.setStreaming(true);

  const schedule = (fn: () => void, ms: number) => {
    const t = setTimeout(() => {
      if (!abortController.signal.aborted) fn();
    }, ms);
    _demoPhaseTimers.push(t);
    return t;
  };

  // Phase 0: User message + assistant shell
  store.addUserMessage(userMsgId, userText);
  store.addAssistantMessage(assistantMsgId);

  // Phase 1: Thinking block
  store.appendBlock(assistantMsgId, {
    type: "thinking" as const,
    content:
      "用户想分析销售数据趋势。我需要先读取表格了解数据结构，" +
      "分析各月销售额变化和环比增长率，然后将计算结果写回表格的新列中。",
    duration: 1.2,
    startedAt: ts,
  });

  // Phase 2: Route status + read_excel tool call
  schedule(() => {
    store.appendBlock(assistantMsgId, {
      type: "status" as const,
      label: "智能路由",
      detail: "read_excel,run_code",
      variant: "route",
    });

    store.appendBlock(assistantMsgId, {
      type: "tool_call" as const,
      toolCallId: readToolCallId,
      name: "read_excel",
      args: { file_path: "销售数据.xlsx", sheet: "Sheet1", range: "A1:D7" },
      status: "success" as const,
      result: "成功读取 6 行 × 4 列数据",
    });

    excelStore.addPreview({
      toolCallId: readToolCallId,
      filePath: "销售数据.xlsx",
      sheet: "Sheet1",
      columns: ["A:月份", "B:销售额", "C:成本", "D:利润"],
      rows: [
        ["1月", 128500, 76200, 52300],
        ["2月", 135800, 79400, 56400],
        ["3月", 142300, 82100, 60200],
        ["4月", 156700, 88500, 68200],
        ["5月", 168200, 92300, 75900],
        ["6月", 207600, 105800, 101800],
      ],
      totalRows: 6,
      truncated: false,
    });
  }, 300);

  // Phase 3: Streaming analysis text
  const analysisText =
    "根据读取的数据，我来为你分析销售趋势：\n\n" +
    "📊 **数据概览**\n" +
    "- 数据范围：2024年1月 ~ 6月\n" +
    "- 总销售额：¥939,100\n" +
    "- 总利润：¥414,800\n" +
    "- 平均利润率：44.2%\n\n" +
    "📈 **趋势分析**\n" +
    "销售额呈持续上升趋势，6月环比增长 **23.4%**，为半年内最高增速。" +
    "利润率从1月的40.7%提升至6月的49.0%，成本控制效果显著。\n\n" +
    "我来把环比增长率写入 E 列：";

  schedule(() => {
    store.appendBlock(assistantMsgId, { type: "text" as const, content: "" });

    let charIndex = 0;
    _mockStreamingTimer = setInterval(() => {
      if (charIndex >= analysisText.length || abortController.signal.aborted) {
        if (_mockStreamingTimer) {
          clearInterval(_mockStreamingTimer);
          _mockStreamingTimer = null;
        }
        if (!abortController.signal.aborted) {
          _injectWritePhase(assistantMsgId, writeToolCallId);
        }
        return;
      }
      const chunk = analysisText.slice(charIndex, charIndex + 3);
      charIndex += 3;
      useChatStore.getState().updateLastBlock(assistantMsgId, (b) => {
        if (b.type === "text") return { ...b, content: b.content + chunk };
        return b;
      });
    }, 35);
  }, 900);
}

/** Phase 4: Inject run_code tool call with diff data + token stats. */
function _injectWritePhase(assistantMsgId: string, writeToolCallId: string) {
  const store = useChatStore.getState();
  if (store.abortController?.signal.aborted) return;
  const excelStore = useExcelStore.getState();

  store.appendBlock(assistantMsgId, {
    type: "tool_call" as const,
    toolCallId: writeToolCallId,
    name: "run_code",
    args: {
      code: "import openpyxl\nwb = openpyxl.load_workbook('销售数据.xlsx')\nws = wb['Sheet1']\nws['E1'] = '环比增长'\nws['E2'] = '—'\nratios = ['+5.7%', '+4.8%', '+10.1%', '+7.3%', '+23.4%']\nfor i, r in enumerate(ratios, 3):\n    ws[f'E{i}'] = r\nwb.save('销售数据.xlsx')\nprint('已写入 E1:E7（7 个单元格）')",
    },
    status: "success" as const,
    result: "已写入 E1:E7（7 个单元格）",
  });

  excelStore.addDiff({
    toolCallId: writeToolCallId,
    filePath: "销售数据.xlsx",
    sheet: "Sheet1",
    affectedRange: "E1:E7",
    changes: [
      { cell: "E1", old: null, new: "环比增长" },
      { cell: "E2", old: null, new: "—" },
      { cell: "E3", old: null, new: "+5.7%" },
      { cell: "E4", old: null, new: "+4.8%" },
      { cell: "E5", old: null, new: "+10.1%" },
      { cell: "E6", old: null, new: "+7.3%" },
      { cell: "E7", old: null, new: "+23.4%" },
    ],
    timestamp: Date.now(),
  });

  store.appendBlock(assistantMsgId, {
    type: "text" as const,
    content:
      "\n\n✅ 已将各月环比增长率写入 E 列。6月增幅最大（+23.4%），建议重点关注 Q2 的增长驱动因素。",
  });

  store.appendBlock(assistantMsgId, {
    type: "token_stats" as const,
    promptTokens: 1247,
    completionTokens: 386,
    totalTokens: 1633,
    iterations: 2,
  });
}

/** Clean up mock streaming state. */
export function cleanupMockStreaming() {
  for (const t of _demoPhaseTimers) clearTimeout(t);
  _demoPhaseTimers = [];

  if (_mockStreamingTimer) {
    clearInterval(_mockStreamingTimer);
    _mockStreamingTimer = null;
  }
  const store = useChatStore.getState();
  if (store.isStreaming) {
    store.abortController?.abort();
    store.setAbortController(null);
    store.setStreaming(false);
  }
}

/** Clean up Excel preview demo state. */
export function cleanupExcelPreviewDemo() {
  useExcelStore.getState().closePanel();
  useExcelStore.getState().clearDemoFile();
}

/** Remove the temporary demo session and restore the user's original session. */
export function cleanupDemoSession() {
  cleanupMockStreaming();

  // Remove demo preview/diff data from excel-store
  if (_demoToolCallIds.length > 0) {
    const es = useExcelStore.getState();
    const demoIds = new Set(_demoToolCallIds);
    useExcelStore.setState({
      previews: Object.fromEntries(
        Object.entries(es.previews).filter(([k]) => !demoIds.has(k)),
      ),
      diffs: es.diffs.filter((d) => !demoIds.has(d.toolCallId)),
    });
    _demoToolCallIds = [];
  }

  const demoId = _demoSessionId;
  _demoSessionId = null;

  if (demoId) {
    useChatStore.setState({
      messages: [],
      isStreaming: false,
      abortController: null,
      pipelineStatus: null,
      currentSessionId: null,
    });
    useSessionStore.getState().removeSession(demoId);
    useChatStore.getState().removeSessionCache(demoId);
  }

  const originalId = _originalSessionId;
  _originalSessionId = undefined;

  if (originalId) {
    useSessionStore.getState().setActiveSession(originalId);
  } else {
    useSessionStore.getState().setActiveSession(null);
  }
}
