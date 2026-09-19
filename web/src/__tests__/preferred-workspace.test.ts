import { describe, expect, it } from "vitest";
import {
  resolvePreferredWorkspace,
} from "@/lib/preferred-workspace";
import type { Session, WorkspaceFolder } from "@/lib/types";

function session(partial: Partial<Session> & Pick<Session, "id">): Session {
  return {
    title: partial.title ?? partial.id,
    messageCount: partial.messageCount ?? 0,
    inFlight: false,
    ...partial,
  };
}

function workspace(partial: Partial<WorkspaceFolder> & Pick<WorkspaceFolder, "id" | "path">): WorkspaceFolder {
  return {
    title: partial.title ?? partial.id,
    ...partial,
  };
}

describe("resolvePreferredWorkspace", () => {
  const wsA = workspace({ id: "ws-a", path: "/data/a" });
  const wsB = workspace({ id: "ws-b", path: "/data/b" });

  it("uses the last opened or used chat workspace", () => {
    const preferred = resolvePreferredWorkspace({
      sessions: [
        session({ id: "s1", workspaceId: "ws-a", workspacePath: "/data/a", updatedAt: "2" }),
        session({ id: "s2", workspaceId: "ws-b", workspacePath: "/data/b", updatedAt: "1" }),
      ],
      workspaces: [wsA, wsB],
      lastWorkspaceId: "ws-b",
      lastWorkspacePath: "/data/b",
    });
    expect(preferred).toEqual({ workspaceId: "ws-b", workspacePath: "/data/b" });
  });

  it("uses the last opened session workspace when persist id is stale", () => {
    const preferred = resolvePreferredWorkspace({
      sessions: [
        session({ id: "s2", workspaceId: "ws-b", workspacePath: "/data/b", updatedAt: "1" }),
        session({ id: "s1", workspaceId: "ws-a", workspacePath: "/data/a", updatedAt: "3" }),
      ],
      workspaces: [wsA, wsB],
      lastOpenedSessionId: "s2",
    });
    expect(preferred).toEqual({ workspaceId: "ws-b", workspacePath: "/data/b" });
  });

  it("uses the most recently updated chat workspace next", () => {
    const preferred = resolvePreferredWorkspace({
      sessions: [
        session({ id: "old", workspaceId: "ws-a", workspacePath: "/data/a", updatedAt: "1" }),
        session({ id: "new", workspaceId: "ws-b", workspacePath: "/data/b", updatedAt: "9" }),
      ],
      workspaces: [wsA, wsB],
    });
    expect(preferred).toEqual({ workspaceId: "ws-b", workspacePath: "/data/b" });
  });

  it("falls back to the first workspace", () => {
    const preferred = resolvePreferredWorkspace({
      sessions: [session({ id: "gone", workspaceId: "missing", workspacePath: "/gone" })],
      workspaces: [wsA, wsB],
    });
    expect(preferred).toEqual({ workspaceId: "ws-a", workspacePath: "/data/a" });
  });
});
