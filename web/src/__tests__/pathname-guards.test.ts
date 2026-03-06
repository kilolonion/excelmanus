import React from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

let mockPathname: string | null = null;

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({
    replace: vi.fn(),
  }),
}));

vi.mock("@/app/client-layout", () => ({
  ClientLayout: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}));

vi.mock("@/components/providers/AuthProvider", () => ({
  AuthProvider: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}));

vi.mock("@/stores/auth-config-store", () => ({
  useAuthConfigStore: () => ({
    authEnabled: true,
    checked: true,
    checkAuthEnabled: vi.fn().mockResolvedValue(true),
  }),
}));

vi.mock("@/components/ui/LoadingScreen", () => ({
  LoadingScreen: ({ message }: { message?: string }) => React.createElement("div", null, message ?? "loading"),
}));

vi.mock("@/components/VersionUpdateToast", () => ({
  VersionUpdateToast: () => null,
}));

vi.mock("@/components/GlobalRestartOverlay", () => ({
  GlobalRestartOverlay: () => null,
}));

vi.mock("@/stores/health-hub-store", () => ({
  ensureHealthHubPolling: vi.fn(),
  useHealthHubStore: (selector: (state: {
    newVersionAvailable: boolean;
    apiIncompatible: boolean;
    remoteVersion: string | null;
    dismissVersion: () => void;
    refreshNow: () => void;
  }) => unknown) => selector({
    newVersionAvailable: false,
    apiIncompatible: false,
    remoteVersion: null,
    dismissVersion: vi.fn(),
    refreshNow: vi.fn(),
  }),
}));

const { AppShell } = await import("@/app/app-shell");
const { pathnameStartsWith } = await import("@/lib/pathname");

describe("pathname guards", () => {
  it("AppShell does not throw when Next returns a null pathname", () => {
    mockPathname = null;

    expect(() =>
      renderToString(
        React.createElement(
          AppShell,
          null,
          React.createElement("div", null, "child"),
        ),
      ),
    ).not.toThrow();
  });

  it("pathnameStartsWith treats null pathnames as non-matches", () => {
    expect(pathnameStartsWith(null, ["/login", "/register"])).toBe(false);
  });
});
