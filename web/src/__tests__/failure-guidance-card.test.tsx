// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FailureGuidanceCard } from "@/components/chat/FailureGuidanceCard";
import { takePendingModelSubTab } from "@/components/settings/model/model-subtab";

const { openSettings } = vi.hoisted(() => ({ openSettings: vi.fn() }));
vi.mock("@/stores/ui-store", () => ({ useUIStore: (select: (state: unknown) => unknown) => select({ openSettings }) }));
vi.mock("@/components/chat/RetryModelPicker", () => ({ RetryModelPicker: () => null }));

afterEach(() => { cleanup(); vi.clearAllMocks(); takePendingModelSubTab(); });

it("opens subscription settings from the primary login recovery action", () => {
  render(<FailureGuidanceCard category="model" code="model_oauth_expired"
    title="订阅登录已失效" message="请重新登录对应账号，再继续对话。"
    stage="calling_llm" retryable={false} diagnosticId="auth-1" actions={[]}
    provider="chatgpt" model="test-model" />);
  expect(screen.getAllByRole("button")[0].textContent).toBe("重新登录订阅账号");
  fireEvent.click(screen.getByRole("button", { name: "重新登录订阅账号" }));
  expect(openSettings).toHaveBeenCalledWith("model");
  expect(takePendingModelSubTab()).toBe("subscription");
});
