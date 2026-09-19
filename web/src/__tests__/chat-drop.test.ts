import { describe, expect, it } from "vitest";
import {
  hasOsFileDrag,
  isWorkspaceFileDrag,
  parseWorkspaceDroppedFiles,
  shouldCancelComposerNativeDrop,
  workspaceFileMention,
} from "@/components/chat/chat-drop";

describe("workspace drag detection", () => {
  it("treats the custom MIME as a sidebar file drag", () => {
    expect(isWorkspaceFileDrag(["application/x-excel-file", "text/plain"], 0)).toBe(true);
  });

  it("falls back to the in-flight drag count when types omit the custom MIME", () => {
    expect(isWorkspaceFileDrag(["text/plain"], 2)).toBe(true);
    expect(isWorkspaceFileDrag(["text/plain"], 0)).toBe(false);
  });

  it("detects OS file drags so the textarea does not insert a path as text", () => {
    expect(hasOsFileDrag(["Files", "text/plain"])).toBe(true);
    expect(shouldCancelComposerNativeDrop(["Files"], 0)).toBe(true);
    expect(shouldCancelComposerNativeDrop(["text/plain"], 0)).toBe(false);
  });
});

describe("parseWorkspaceDroppedFiles", () => {
  it("accepts a single file object and an array", () => {
    expect(
      parseWorkspaceDroppedFiles(
        JSON.stringify({ path: "folder/sales.xlsx", filename: "sales.xlsx" }),
      ),
    ).toEqual([{ path: "folder/sales.xlsx", filename: "sales.xlsx" }]);
    expect(
      parseWorkspaceDroppedFiles(
        JSON.stringify([
          { path: "a.xlsx", filename: "a.xlsx" },
          { path: "b.csv", filename: "b.csv" },
        ]),
      ),
    ).toHaveLength(2);
  });

  it("ignores invalid payloads", () => {
    expect(parseWorkspaceDroppedFiles("")).toEqual([]);
    expect(parseWorkspaceDroppedFiles("not-json")).toEqual([]);
    expect(parseWorkspaceDroppedFiles(JSON.stringify({ filename: "a.xlsx" }))).toEqual([]);
  });
});

describe("workspaceFileMention", () => {
  it("uses the workspace path rather than the basename-only token", () => {
    expect(
      workspaceFileMention({ path: "./uploads/folder/sales.xlsx", filename: "sales.xlsx" }),
    ).toBe("@file:uploads/folder/sales.xlsx");
  });

  it("keeps workspace-list relative paths without inventing a leading slash", () => {
    expect(
      workspaceFileMention({ path: "web/public/samples/订单与产品.xlsx", filename: "订单与产品.xlsx" }),
    ).toBe("@file:web/public/samples/订单与产品.xlsx");
  });
});
