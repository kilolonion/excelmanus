// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useOAuthLogin, usePollingLogin } from "@/components/settings/model/useSubscriptionLogin";
import { SubscriptionAccountCard } from "@/components/settings/model/SubscriptionAccountCard";
import { requestModelSubTab, subscribeModelSubTab, takePendingModelSubTab } from "@/components/settings/model/model-subtab";

const oauthData = {
  authorize_url: "https://auth.openai.com/authorize?state=current",
  state: "current",
  redirect_uri: "http://localhost:1455/auth/callback",
  mode: "popup",
};
const callback = "http://localhost:1455/auth/callback?state=current&code=valid";
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}
function oauthOptions() {
  return {
    name: "test-oauth", messageType: "codex-oauth-callback",
    start: vi.fn().mockResolvedValue(oauthData), validateUrl: (url: string) => url,
    exchange: vi.fn().mockResolvedValue({}), onConnected: vi.fn().mockResolvedValue(undefined), onError: vi.fn(),
  };
}

beforeEach(() => { vi.spyOn(window, "open").mockReturnValue(null); });
afterEach(() => {
  cleanup(); vi.useRealTimers(); vi.restoreAllMocks();
  delete window.excelManusAndroid;
  takePendingModelSubTab();
});

describe("subscription OAuth recovery", () => {
  it("keeps a blocked popup recoverable and retains the attempt after bad pasted addresses", async () => {
    const options = oauthOptions();
    const { result } = renderHook(() => useOAuthLogin(options));
    await act(() => result.current.start());
    expect(result.current.authorizeUrl).toBe(oauthData.authorize_url);
    expect(result.current.manual).toBe(true);
    for (const invalid of ["not a url", callback.replace("current", "stale"), callback.replace("localhost:1455", "example.com"), callback.replace("&code=valid", "")]) {
      act(() => result.current.setPasteUrl(invalid));
      await act(() => result.current.submit());
      expect(result.current.busy).toBe(true);
      expect(options.exchange).not.toHaveBeenCalled();
      expect(options.onError.mock.lastCall?.[0]).not.toBe("");
    }
    act(() => result.current.setPasteUrl(callback));
    await act(() => result.current.submit());
    expect(options.exchange).toHaveBeenCalledExactlyOnceWith("valid", "current");
    expect(options.onConnected).toHaveBeenCalledOnce();
    expect(result.current.busy).toBe(false);
    expect(result.current.pasteUrl).toBe("");
  });

  it("ignores wrong origins and stale errors, and exchanges duplicate callbacks only once", async () => {
    const pending = deferred<unknown>();
    const options = oauthOptions(); options.exchange.mockReturnValue(pending.promise);
    const { result } = renderHook(() => useOAuthLogin(options));
    await act(() => result.current.start());
    const send = (origin: string, data: object) => window.dispatchEvent(new MessageEvent("message", { origin, data: { type: options.messageType, ...data } }));
    act(() => {
      send("https://example.com", { state: "current", code: "bad" });
      send("http://localhost:1455", { state: "old", error: "access_denied" });
    });
    expect(result.current.busy).toBe(true);
    expect(options.exchange).not.toHaveBeenCalled();
    act(() => {
      send("http://localhost:1455", { state: "current", code: "valid" });
      send("http://localhost:1455", { state: "current", code: "valid" });
    });
    expect(result.current.phase).toBe("exchanging");
    expect(options.exchange).toHaveBeenCalledOnce();
    await act(async () => { pending.resolve({}); });
    expect(result.current.busy).toBe(false);
  });

  it("does not revive a cancelled start and allows immediate retry", async () => {
    const pending = deferred<typeof oauthData>();
    const options = oauthOptions(); options.start.mockReturnValueOnce(pending.promise);
    const { result } = renderHook(() => useOAuthLogin(options));
    let first!: Promise<void>;
    act(() => { first = result.current.start(); result.current.cancel(); });
    await act(() => result.current.start());
    await act(async () => { pending.resolve({ ...oauthData, state: "old" }); await first; });
    act(() => result.current.setPasteUrl(callback));
    await act(() => result.current.submit());
    expect(options.exchange).toHaveBeenCalledExactlyOnceWith("valid", "current");
  });

  it("rejects double starts even when no popup was opened", async () => {
    const pending = deferred<typeof oauthData>();
    const options = oauthOptions(); options.start.mockReturnValue(pending.promise);
    const { result } = renderHook(() => useOAuthLogin(options));
    act(() => { void result.current.start(); void result.current.start(); });
    expect(options.start).toHaveBeenCalledOnce();
    await act(async () => { pending.resolve(oauthData); });
  });

  it("retains manual recovery after popup closure and cleans up timers on unmount", async () => {
    vi.useFakeTimers();
    const popup = { closed: false, close: vi.fn(), location: { href: "about:blank" } };
    vi.mocked(window.open).mockReturnValue(popup as unknown as Window);
    const { result, unmount } = renderHook(() => useOAuthLogin(oauthOptions()));
    await act(() => result.current.start());
    popup.closed = true;
    await act(() => vi.advanceTimersByTimeAsync(500));
    expect(result.current.manual).toBe(true);
    expect(result.current.notice).toContain("已关闭");
    expect(result.current.busy).toBe(true);
    unmount(); expect(vi.getTimerCount()).toBe(0);
  });

  it("opens the full authorization URL directly in native clients", async () => {
    window.excelManusAndroid = { version: 1, saveBlob: vi.fn() };
    const { result } = renderHook(() => useOAuthLogin(oauthOptions()));
    await act(() => result.current.start());
    expect(window.open).toHaveBeenCalledExactlyOnceWith(oauthData.authorize_url, "test-oauth", expect.any(String));
  });
});

describe("subscription polling", () => {
  const session = { state: "current", url: "https://workbuddy.ai/login", expires_in: 30, interval: 3 };
  it("does not poll after cancellation while the start request is still pending", async () => {
    vi.useFakeTimers();
    const pending = deferred<typeof session>();
    const options = { start: () => pending.promise, poll: vi.fn(), onConnected: vi.fn(), onError: vi.fn() };
    const { result } = renderHook(() => usePollingLogin(options));
    let starting!: Promise<void>;
    act(() => { starting = result.current.start(); result.current.cancel(); });
    await act(async () => { pending.resolve(session); await starting; await vi.advanceTimersByTimeAsync(60_000); });
    expect(options.poll).not.toHaveBeenCalled();
    expect(result.current.busy).toBe(false);
    expect(result.current.session).toBeNull();
  });

  it("serializes polls and ignores a success arriving after cancellation", async () => {
    vi.useFakeTimers();
    const pending = deferred<{ status: string }>();
    const options = { start: async () => session, poll: vi.fn().mockReturnValue(pending.promise), onConnected: vi.fn(), onError: vi.fn() };
    const { result } = renderHook(() => usePollingLogin(options));
    await act(() => result.current.start());
    await act(() => vi.advanceTimersByTimeAsync(9_000));
    expect(options.poll).toHaveBeenCalledOnce();
    act(() => result.current.cancel());
    await act(async () => { pending.resolve({ status: "connected" }); });
    expect(options.onConnected).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("clears the old deadline before retrying and reports expiry on the current attempt", async () => {
    vi.useFakeTimers();
    const options = { start: async () => session, poll: vi.fn().mockResolvedValue({ status: "pending" }), onConnected: vi.fn(), onError: vi.fn() };
    const { result } = renderHook(() => usePollingLogin(options));
    await act(() => result.current.start());
    await act(() => vi.advanceTimersByTimeAsync(20_000));
    act(() => result.current.cancel());
    await act(() => result.current.start());
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    expect(result.current.busy).toBe(true);
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    expect(result.current.busy).toBe(false);
    expect(options.onError.mock.lastCall?.[0]).toContain("超时");
  });
});

describe("subscription account navigation", () => {
  it("collapses disconnected accounts and exposes recovery for unknown status", () => {
    const retry = vi.fn();
    render(<SubscriptionAccountCard provider="openai-codex" title="GPT Codex" description="ChatGPT 订阅" loading={false} busy={false} modelCount={0} error="" statusError onRetry={retry}><button>登录</button></SubscriptionAccountCard>);
    expect(screen.queryByRole("button", { name: "登录" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /GPT Codex/ }));
    fireEvent.click(screen.getByRole("button", { name: "重新检查" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("shows expired authorization as actionable and routes connected accounts to model configuration", () => {
    const props = { provider: "openai-codex", title: "GPT Codex", description: "ChatGPT 订阅", loading: false, busy: false, modelCount: 2, error: "", statusError: false, onRetry: vi.fn() };
    const { rerender } = render(<SubscriptionAccountCard {...props} status="expired">重新登录</SubscriptionAccountCard>);
    fireEvent.click(screen.getByRole("button", { name: /需重新登录/ }));
    expect(screen.getByRole("status").textContent).toContain("授权已过期");
    rerender(<SubscriptionAccountCard {...props} status="connected">模型列表</SubscriptionAccountCard>);
    const listener = vi.fn(); const unsubscribe = subscribeModelSubTab(listener);
    fireEvent.click(screen.getByRole("button", { name: "配置模型" }));
    expect(listener).toHaveBeenCalledWith("roles");
    unsubscribe();
    expect(takePendingModelSubTab()).toBeNull();
    requestModelSubTab("subscription");
    expect(takePendingModelSubTab()).toBe("subscription");
  });
});
