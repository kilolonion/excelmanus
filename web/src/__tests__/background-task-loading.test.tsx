// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  fetchSubagentRuns: vi.fn().mockResolvedValue([]),
  fetchSessionTaskList: vi.fn().mockResolvedValue(null),
  controlSubagentRun: vi.fn(),
}));
vi.mock("@/lib/idb-cache", () => ({
  loadCachedMessages: vi.fn(), saveCachedMessages: vi.fn().mockResolvedValue(undefined),
  deleteCachedMessages: vi.fn(), clearAllCachedMessages: vi.fn(),
}));
import { useBackgroundTasks } from "@/components/chat/use-background-tasks";
import { fetchSessionTaskList, fetchSubagentRuns } from "@/lib/api";
import { useSessionStore } from "@/stores/session-store";
import { useChatStore } from "@/stores/chat-store";

beforeEach(() => {
  vi.clearAllMocks();
  useSessionStore.setState({ activeSessionId: "s", sessions: [{ id: "s", title: "S", blank: true, messageCount: 0, inFlight: false }] });
  useChatStore.setState({ loadedSessionId: "s", messages: [], isLoadingMessages: false, isStreaming: false });
});
afterEach(cleanup);

it("does not request task endpoints for a blank chat, even when opened", () => {
  const { result } = renderHook(() => useBackgroundTasks("s", true));
  expect(result.current.loading).toBe(false);
  expect(fetchSubagentRuns).not.toHaveBeenCalled();
  expect(fetchSessionTaskList).not.toHaveBeenCalled();
});

it("loads historical tasks only when opened and the message page is ready", async () => {
  useSessionStore.getState().patchSession("s", { blank: false, messageCount: 10 });
  useChatStore.setState({ isLoadingMessages: true });
  const hook = renderHook(({ open }) => useBackgroundTasks("s", open), { initialProps: { open: false } });
  hook.rerender({ open: true });
  expect(fetchSubagentRuns).not.toHaveBeenCalled();
  act(() => useChatStore.setState({ isLoadingMessages: false }));
  await waitFor(() => expect(fetchSubagentRuns).toHaveBeenCalledOnce());
  expect(fetchSessionTaskList).toHaveBeenCalledOnce();
});

it("cancels requests when the task panel is closed or unmounted", async () => {
  useSessionStore.getState().patchSession("s", { blank: false, messageCount: 10 });
  const hook = renderHook(() => useBackgroundTasks("s", true));
  await waitFor(() => expect(fetchSubagentRuns).toHaveBeenCalledOnce());
  const signal = vi.mocked(fetchSubagentRuns).mock.calls[0][1]?.signal;
  hook.unmount();
  expect(signal?.aborted).toBe(true);
});
