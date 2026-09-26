import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSessionStore } from "@/stores/session-store";
import type { Session } from "@/lib/types";

const row = (id: string, updatedAt: string): Session => ({
  id, title: `对话 ${id}`, messageCount: 2, inFlight: false, updatedAt,
});

describe("session polling updates", () => {
  beforeEach(() => useSessionStore.setState({ sessions: [row("a", "2026-09-19"), row("b", "2026-09-18")], activeSessionId: "a" }));

  it("does not notify subscribers or replace the list for an unchanged poll", () => {
    const before = useSessionStore.getState().sessions;
    const listener = vi.fn();
    const off = useSessionStore.subscribe(listener);
    useSessionStore.getState().mergeSessions(before.map((s) => ({ ...s })));
    off();
    expect(useSessionStore.getState().sessions).toBe(before);
    expect(listener).not.toHaveBeenCalled();
  });

  it("reorders changed sessions while retaining untouched row identities", () => {
    const [a, b] = useSessionStore.getState().sessions;
    useSessionStore.getState().mergeSessions([{ ...a }, { ...b, inFlight: true, updatedAt: "2026-09-20" }]);
    expect(useSessionStore.getState().sessions[0]).toMatchObject({ id: "b", inFlight: true });
    expect(useSessionStore.getState().sessions[1]).toBe(a);
  });

  it("skips no-op patches but delivers pending-interaction changes", () => {
    const before = useSessionStore.getState().sessions;
    useSessionStore.getState().patchSession("a", { inFlight: false });
    expect(useSessionStore.getState().sessions).toBe(before);
    useSessionStore.getState().patchSession("a", { pendingApproval: true });
    expect(useSessionStore.getState().sessions[0].pendingApproval).toBe(true);
    expect(useSessionStore.getState().sessions[1]).toBe(before[1]);
  });

  it("still prunes removed sessions and preserves an active optimistic session", () => {
    useSessionStore.getState().mergeSessions([row("c", "2026-09-20")]);
    expect(useSessionStore.getState().sessions.map((s) => s.id)).toEqual(["c", "a"]);
  });

  it("does not resurrect a session from a poll that overlapped its deletion", () => {
    const deleted = row("deleted-race", "2026-09-20");
    const kept = row("kept-race", "2026-09-19");
    useSessionStore.getState().setSessions([deleted, kept]);

    useSessionStore.getState().removeSession(deleted.id);
    useSessionStore.getState().mergeSessions([deleted, kept]);
    expect(useSessionStore.getState().sessions.map((s) => s.id)).toEqual([kept.id]);

    // A failed DELETE is still allowed to restore the row explicitly.
    useSessionStore.getState().addSession(deleted);
    expect(useSessionStore.getState().sessions.map((s) => s.id)).toContain(deleted.id);
  });
});
