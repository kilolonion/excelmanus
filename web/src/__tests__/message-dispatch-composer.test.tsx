// @vitest-environment jsdom
import React from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(),
  apiGet: vi.fn().mockResolvedValue({ message_dispatch_default: "interrupt", files: [], models: [], skills: [] }),
  answerQuestion: vi.fn().mockResolvedValue({ resume_required: false }),
}));

import { ChatInput } from "@/components/chat/ChatInput";
import { useChatStore } from "@/stores/chat-store";
import { useSessionStore } from "@/stores/session-store";
import { useUIStore } from "@/stores/ui-store";
import { answerQuestion } from "@/lib/api";

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0));
  vi.stubGlobal("cancelAnimationFrame", clearTimeout);
  useSessionStore.setState({ activeSessionId: "composer", sessions: [{ id: "composer", title: "fixture", messageCount: 0, inFlight: true }] });
  useChatStore.getState().setMessages([]);
  useChatStore.setState({ isStreaming: true, abortController: new AbortController(), loadedSessionId: "composer",
    pendingQuestion: { id: "q1", header: "选择", text: "使用哪个区域？", options: [], multiSelect: false } });
  useUIStore.setState({ configReady: true, configError: null, messageDispatchDefault: "interrupt" });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("a question reply stays an answer even when the global default is interrupt", async () => {
  const send = vi.fn().mockResolvedValue(true);
  render(<ChatInput onSend={send} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "A1:C8" } });
  fireEvent.click(screen.getByRole("button", { name: "发送回答" }));
  await waitFor(() => expect(answerQuestion).toHaveBeenCalledWith("composer", "q1", "A1:C8"));
  expect(send).not.toHaveBeenCalled();
});

it("uses the setting for a message sent while a task is running", async () => {
  const send = vi.fn().mockResolvedValue(true);
  useChatStore.getState().setPendingQuestion(null);
  render(<ChatInput onSend={send} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "改做另一项任务" } });
  expect(screen.queryByRole("button", { name: /发送方式：/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
  await waitFor(() => expect(send).toHaveBeenCalledWith("改做另一项任务", undefined, "composer", "interrupt"));
  expect(answerQuestion).not.toHaveBeenCalled();
  expect(useUIStore.getState().messageDispatchDefault).toBe("interrupt");
});
