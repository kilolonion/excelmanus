// @vitest-environment jsdom
import React from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MentionHighlighter } from "@/components/chat/MentionHighlighter";
import { openWorkspaceFile } from "@/lib/open-workspace-file";

vi.mock("@/lib/open-workspace-file", () => ({ openWorkspaceFile: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("clicking an agent reference keeps sheet, range and observed version intact", () => {
  render(<MentionHighlighter text={"检查 @file:sales.xlsx['Sales ! O''Brien'!A2:A5,C2:C5]@sha256:aaaa"} />);
  fireEvent.click(screen.getByRole("button"));
  expect(openWorkspaceFile).toHaveBeenCalledWith("sales.xlsx", { sheet: "Sales ! O'Brien", range: "A2:A5,C2:C5", version: "sha256:aaaa" });
});

it("a range without a sheet is passed as a range, including keyboard activation", () => {
  render(<MentionHighlighter text="@file:sales.xlsx[D4]" />);
  fireEvent.keyDown(screen.getByRole("button"), { key: "Enter" });
  expect(openWorkspaceFile).toHaveBeenCalledWith("sales.xlsx", { range: "D4", version: undefined });
});
