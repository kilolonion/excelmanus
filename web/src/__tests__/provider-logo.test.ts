import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  hasProviderLogo,
  ProviderAvatar,
  providerFallbackInitial,
} from "@/components/settings/model/ProviderLogo";

describe("ProviderAvatar", () => {
  it("renders a stored brand logo when one is available", () => {
    const html = renderToStaticMarkup(createElement(ProviderAvatar, {
      id: "deepseek",
      label: "DeepSeek",
    }));

    expect(hasProviderLogo("deepseek")).toBe(true);
    expect(html).toContain("/providers/deepseek.svg");
  });

  it("renders the label initial instead of the generic default logo", () => {
    const html = renderToStaticMarkup(createElement(ProviderAvatar, {
      id: "edu",
      label: "bench-test-gateway",
    }));

    expect(hasProviderLogo("edu")).toBe(false);
    expect(providerFallbackInitial("bench-test-gateway")).toBe("B");
    expect(html).toContain(">B</span>");
    expect(html).not.toContain("default.svg");
  });

  it("supports Chinese initials and empty labels", () => {
    expect(providerFallbackInitial("acme/model-2")).toBe("A");
    expect(providerFallbackInitial("模型一号")).toBe("模");
    expect(providerFallbackInitial(" ")).toBe("?");
  });
});
