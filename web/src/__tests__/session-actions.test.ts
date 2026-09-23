import { beforeEach, describe, expect, it, vi } from "vitest";

const createSession = vi.fn();
const fetchWorkspaces = vi.fn();

vi.mock("@/lib/api", () => ({
  createSession: (...args: unknown[]) => createSession(...args),
  fetchWorkspaces: (...args: unknown[]) => fetchWorkspaces(...args),
}));

vi.mock("@/stores/chat-store", () => ({
  useChatStore: {
    getState: () => ({
      switchSession: vi.fn(),
    }),
  },
}));

import { createOrReuseSession, ensureLandingSession } from "@/lib/session-actions";
import { useSessionStore } from "@/stores/session-store";

function resetStore() {
  useSessionStore.setState({
    sessions: [],
    activeSessionId: null,
    lastWorkspaceId: null,
    lastWorkspacePath: null,
  });
}

describe("ensureLandingSession", () => {
  beforeEach(() => {
    resetStore();
    createSession.mockReset();
    fetchWorkspaces.mockReset();
    createSession.mockResolvedValue({
      id: "blank-1",
      title: "新对话",
      message_count: 0,
      in_flight: false,
      updated_at: "2026-09-19T00:00:00Z",
      workspace_path: "/data/b",
      workspace_id: "ws-b",
      workspace_title: "b",
      blank: true,
    });
    fetchWorkspaces.mockResolvedValue([
      { id: "ws-a", path: "/data/a", title: "A" },
      { id: "ws-b", path: "/data/b", title: "B" },
    ]);
  });

  it("creates and activates a blank session in the last used workspace", async () => {
    useSessionStore.setState({
      lastWorkspaceId: "ws-b",
      lastWorkspacePath: "/data/b",
    });

    const created = await ensureLandingSession();

    expect(createSession).toHaveBeenCalledWith({
      workspaceId: "ws-b",
      workspacePath: "/data/b",
    });
    expect(created?.id).toBe("blank-1");
    expect(useSessionStore.getState().activeSessionId).toBe("blank-1");
    expect(useSessionStore.getState().lastWorkspaceId).toBe("ws-b");
  });

  it("falls back to the first workspace when nothing was used", async () => {
    await ensureLandingSession();
    expect(createSession).toHaveBeenCalledWith({
      workspaceId: "ws-a",
      workspacePath: "/data/a",
    });
  });

  it("keeps the restored session without creating an unused chat or fetching folders", async () => {
    useSessionStore.setState({
      sessions: [{
        id: "old",
        title: "旧会话",
        messageCount: 2,
        inFlight: false,
        workspaceId: "ws-b",
        workspacePath: "/data/b",
      }],
      activeSessionId: "old",
      lastWorkspaceId: "ws-b",
      lastWorkspacePath: "/data/b",
    });

    await ensureLandingSession();

    expect(createSession).not.toHaveBeenCalled();
    expect(fetchWorkspaces).not.toHaveBeenCalled();
    expect(useSessionStore.getState().activeSessionId).toBe("old");
    expect(useSessionStore.getState().sessions).toHaveLength(1);
  });
});

describe("createOrReuseSession", () => {
  beforeEach(() => {
    resetStore();
    createSession.mockReset();
    createSession.mockResolvedValue({
      id: "s-new",
      title: "新对话",
      message_count: 0,
      in_flight: false,
      updated_at: "2026-09-19T00:00:00Z",
      workspace_path: "/data/a",
      workspace_id: "ws-a",
      workspace_title: "A",
      blank: true,
    });
  });

  it("remembers the workspace when the new chat becomes active", async () => {
    await createOrReuseSession({ workspaceId: "ws-a", workspacePath: "/data/a" });
    const state = useSessionStore.getState();
    expect(state.activeSessionId).toBe("s-new");
    expect(state.lastWorkspaceId).toBe("ws-a");
    expect(state.lastWorkspacePath).toBe("/data/a");
  });

  it("passes through the explicit new-chat no-reuse flag", async () => {
    await createOrReuseSession({ workspaceId: "ws-a", reuseBlank: false });
    expect(createSession).toHaveBeenCalledWith({
      workspaceId: "ws-a",
      workspacePath: undefined,
      reuseBlank: false,
    });
  });

  it("does not steal selection if the user navigates during creation", async () => {
    let finish!: (value: unknown) => void;
    createSession.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const pending = createOrReuseSession();
    useSessionStore.getState().setActiveSession("chosen-later");
    finish({ id: "created-late", blank: true, message_count: 0, title: "New" });
    await pending;
    expect(useSessionStore.getState().activeSessionId).toBe("chosen-later");
  });
});
