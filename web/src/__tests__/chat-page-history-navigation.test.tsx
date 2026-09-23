// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { useSessionStore } from "@/stores/session-store";
import { useChatStore } from "@/stores/chat-store";
import ChatPage from "@/app/chat/[sessionId]/page";

vi.mock("next/navigation", () => ({ useParams: () => ({ sessionId: "route-a" }) }));
vi.mock("@/lib/chat-actions", () => ({ sendMessage: vi.fn(), stopGeneration: vi.fn(), rollbackAndResend: vi.fn(), retryAssistantMessage: vi.fn() }));
vi.mock("@/components/chat/MessageStream", () => ({ MessageStream: () => <div>loaded history</div> }));
vi.mock("@/components/chat/ChatInput", () => ({ ChatInput: ({ disabled }: { disabled: boolean }) => <textarea aria-label="composer" disabled={disabled} /> }));
vi.mock("@/components/workspace/WorkspaceViewHost", () => ({ WorkspaceViewHost: ({ children }: { children: ReactNode }) => children }));
vi.mock("@/components/modals/CommandResultDialog", () => ({ CommandResultDialog: () => null, useCommandResult: () => ({ state: {}, show: vi.fn(), close: vi.fn() }) }));
vi.mock("@/components/welcome/WelcomePage", () => ({ WelcomePage: () => null }));

afterEach(cleanup);

it("renders the selected history and unlocks input after a sidebar switch from a deep link", () => {
  useSessionStore.setState({ activeSessionId: "route-a", sessions: [] });
  useChatStore.setState({ loadedSessionId: "route-a", messageOrder: ["a"], isLoadingMessages: false, messageLoadError: null });
  render(<ChatPage />);
  act(() => {
    useSessionStore.getState().setActiveSession("sidebar-b");
    useChatStore.setState({ loadedSessionId: "sidebar-b", messageOrder: ["b"] });
  });
  expect(screen.getByText("loaded history")).toBeTruthy();
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).disabled).toBe(false);
});
