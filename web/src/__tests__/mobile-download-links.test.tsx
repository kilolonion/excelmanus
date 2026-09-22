// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ProjectLinks } from "@/components/settings/ProjectLinks";
import { MobilePairingDialog } from "@/components/sidebar/MobilePairingDialog";
import { ANDROID_DOWNLOAD_PAGE_URL } from "@/lib/product-links";

vi.mock("@/lib/api", () => ({ getManageToken: () => "", setManageToken: vi.fn() }));
vi.mock("@/lib/mobile-pairing", () => ({
  mobilePairing: vi.fn().mockResolvedValue({
    enabled: false,
    devices: [],
    pending: [],
    networks: [],
    port: 8787,
  }),
}));

afterEach(cleanup);

describe("Android client download links", () => {
  it("adds the Android download entry to settings", () => {
    render(<ProjectLinks />);

    const link = screen.getByRole("link", { name: /Android 手机端/ });
    expect(link.getAttribute("href")).toBe(ANDROID_DOWNLOAD_PAGE_URL);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(screen.getByText("GitHub Releases · APK")).toBeTruthy();
  });

  it("shows the same quick download in the pairing dialog", () => {
    render(<MobilePairingDialog open onOpenChange={() => {}} />);

    const link = screen.getByRole("link", { name: /还没安装？下载 Android 手机端/ });
    expect(link.getAttribute("href")).toBe(ANDROID_DOWNLOAD_PAGE_URL);
    expect(link.getAttribute("target")).toBe("_blank");
  });
});
