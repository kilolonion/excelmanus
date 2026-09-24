import { describe, expect, it } from "vitest";
import { nestToolCallsByParent } from "@/lib/tool-call-tree";

describe("nestToolCallsByParent", () => {
  it("nests SDK subcalls under the parent run_code card", () => {
    const nested = nestToolCallsByParent([
      { toolCallId: "run-1", name: "run_code" },
      { toolCallId: "child-1", name: "observe_spreadsheet", parentCallId: "run-1" },
      { toolCallId: "child-2", name: "observe_spreadsheet", parentCallId: "run-1" },
    ]);
    expect(nested).toHaveLength(1);
    expect(nested[0].item.toolCallId).toBe("run-1");
    expect(nested[0].children.map((c) => c.toolCallId)).toEqual(["child-1", "child-2"]);
  });

  it("keeps orphan calls as roots when the parent is missing", () => {
    const nested = nestToolCallsByParent([
      { toolCallId: "child-1", parentCallId: "missing-parent" },
      { toolCallId: "solo" },
    ]);
    expect(nested.map((n) => n.item.toolCallId)).toEqual(["child-1", "solo"]);
    expect(nested.every((n) => n.children.length === 0)).toBe(true);
  });
});
