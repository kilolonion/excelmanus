import { describe, expect, it } from "vitest";
import { revisionReasonLabel } from "@/components/chat/CheckpointTimeline";

describe("revisionReasonLabel", () => {
  it("prefers an explicit label", () => {
    expect(revisionReasonLabel("beforeEdit", "发版前")).toBe("发版前");
  });

  it("maps automatic history reasons", () => {
    expect(revisionReasonLabel("beforeEdit", "")).toBe("编辑前");
    expect(revisionReasonLabel("afterEdit", "")).toBe("编辑后");
    expect(revisionReasonLabel("beforeRestore", "")).toBe("恢复前");
    expect(revisionReasonLabel("checkpoint", "")).toBe("检查点");
  });
});
