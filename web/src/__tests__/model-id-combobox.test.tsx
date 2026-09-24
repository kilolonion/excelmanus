// @vitest-environment jsdom

import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ModelIdCombobox } from "@/components/settings/model/ModelIdCombobox";

const models = [
  { id: "gpt-6-astra", owned_by: "gateway" },
  { id: "gpt-6-sol", owned_by: "gateway" },
  { id: "claude-sonnet-4", owned_by: "anthropic" },
];

function Harness({ initial = "gpt-6-sol", disabled = false, empty = false } = {}) {
  const [value, setValue] = useState(initial);
  const [open, setOpen] = useState(false);
  return <>
    <ModelIdCombobox value={value} models={empty ? [] : models} usedModelIds={new Set(["gpt-6-sol"])}
      open={open} onOpenChange={setOpen} onChange={setValue} onSelect={(model) => setValue(model.id)} disabled={disabled} />
    <button type="button">下一个字段</button>
  </>;
}

afterEach(cleanup);
const input = () => screen.getByRole("combobox", { name: "Model ID" }) as HTMLInputElement;
const active = () => document.getElementById(input().getAttribute("aria-activedescendant") || "");

describe("Model ID combobox interactions", () => {
  it("opens on the first click, keeps repeated input clicks open, and closes after selection", () => {
    render(<Harness />);
    fireEvent.click(input());
    expect(screen.getAllByRole("option")).toHaveLength(3);
    fireEvent.click(input());
    expect(screen.getByRole("listbox")).toBeTruthy();
    fireEvent.click(screen.getByRole("option", { name: /claude-sonnet-4/ }));
    expect(input().value).toBe("claude-sonnet-4");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(document.activeElement).toBe(input());
    fireEvent.click(input());
    expect(screen.getAllByRole("option")).toHaveLength(3);
  });

  it("filters immediately including exact IDs, preserves custom input, and resets browsing on reopen", () => {
    render(<Harness />);
    fireEvent.change(input(), { target: { value: "ASTRA" } });
    expect(screen.getAllByRole("option")).toHaveLength(1);
    fireEvent.change(input(), { target: { value: "gpt-6-astra" } });
    expect(screen.getAllByRole("option")).toHaveLength(1);
    fireEvent.change(input(), { target: { value: "my-custom-model" } });
    expect(screen.getByText(/无匹配模型/)).toBeTruthy();
    fireEvent.keyDown(input(), { key: "Enter" });
    expect(input().value).toBe("my-custom-model");
    expect(screen.queryByRole("listbox")).toBeNull();
    fireEvent.click(input());
    expect(screen.getAllByRole("option")).toHaveLength(3);
  });

  it("navigates from the selected model with wraparound and confirms by keyboard", () => {
    render(<Harness />);
    fireEvent.keyDown(input(), { key: "ArrowDown" });
    expect(active()?.textContent).toContain("gpt-6-sol");
    fireEvent.keyDown(input(), { key: "ArrowDown" });
    expect(active()?.textContent).toContain("claude-sonnet-4");
    fireEvent.keyDown(input(), { key: "ArrowDown" });
    expect(active()?.textContent).toContain("gpt-6-astra");
    expect(input().value).toBe("gpt-6-sol");
    fireEvent.keyDown(input(), { key: "Enter" });
    expect(input().value).toBe("gpt-6-astra");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("does not select stale options after filtering and respects IME composition", () => {
    render(<Harness />);
    fireEvent.click(input());
    fireEvent.change(input(), { target: { value: "claude" } });
    expect(active()).toBeNull();
    fireEvent.keyDown(input(), { key: "ArrowDown", isComposing: true });
    expect(active()).toBeNull();
    fireEvent.keyDown(input(), { key: "Enter", isComposing: true });
    expect(screen.getByRole("listbox")).toBeTruthy();
    fireEvent.keyDown(input(), { key: "ArrowDown" });
    expect(active()?.textContent).toContain("claude-sonnet-4");
    fireEvent.keyDown(input(), { key: "Enter" });
    expect(input().value).toBe("claude-sonnet-4");
  });

  it.each(["Escape", "Tab"])("dismisses with %s without changing the model", (key) => {
    render(<Harness />);
    fireEvent.click(input());
    fireEvent.keyDown(input(), { key: "ArrowDown" });
    fireEvent.keyDown(input(), { key });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(input().value).toBe("gpt-6-sol");
  });

  it("toggles with the arrow and dismisses when focus moves outside", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "展开模型列表" }));
    expect(screen.getByRole("listbox")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "收起模型列表" }));
    expect(screen.queryByRole("listbox")).toBeNull();
    fireEvent.click(input());
    fireEvent.focusIn(screen.getByRole("button", { name: "下一个字段" }));
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("allows manual entry before detection and disables both controls while saving", () => {
    const { unmount } = render(<Harness empty />);
    fireEvent.change(input(), { target: { value: "custom" } });
    expect(input().value).toBe("custom");
    expect(screen.queryByRole("listbox")).toBeNull();
    unmount();
    render(<Harness disabled />);
    expect(input().disabled).toBe(true);
    expect((screen.getByRole("button", { name: "展开模型列表" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
