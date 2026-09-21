import { describe, expect, it } from "vitest";
import { resolveWorkbookPanelPath } from "@/components/excel/WorkbookPanelButton";
import type { WorkbookRevisionItem } from "@/lib/api";
import {
  formatRelativeTime,
  groupRevisions,
  isCurrentRevision,
  pathsReferToSameFile,
  revisionReasonLabel,
} from "@/lib/revision-display";

function rev(partial: Partial<WorkbookRevisionItem> & Pick<WorkbookRevisionItem, "revision_id" | "sequence">): WorkbookRevisionItem {
  return {
    content_version: "",
    reason: "",
    transaction_id: "",
    label: "",
    parent_revision_id: null,
    ...partial,
  };
}

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

describe("groupRevisions", () => {
  it("pairs before/after of the same transaction into one edit", () => {
    const groups = groupRevisions([
      rev({
        revision_id: "after",
        sequence: 2,
        reason: "afterEdit",
        transaction_id: "tx-1",
        created_at: "2026-09-13T06:08:50Z",
      }),
      rev({
        revision_id: "before",
        sequence: 1,
        reason: "beforeEdit",
        transaction_id: "tx-1",
        created_at: "2026-09-13T06:08:50Z",
      }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].title).toBe("一次编辑");
    expect(groups[0].items.map((i) => i.revision_id)).toEqual(["after", "before"]);
  });

  it("keeps unlabeled singles as their reason", () => {
    const groups = groupRevisions([
      rev({ revision_id: "cp", sequence: 1, reason: "checkpoint" }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].title).toBe("检查点");
  });
});

describe("isCurrentRevision", () => {
  it("matches the live content version", () => {
    expect(
      isCurrentRevision(
        rev({ revision_id: "a", sequence: 1, content_version: "sha256:abc" }),
        "sha256:abc",
      ),
    ).toBe(true);
    expect(
      isCurrentRevision(
        rev({ revision_id: "a", sequence: 1, content_version: "sha256:abc" }),
        "sha256:def",
      ),
    ).toBe(false);
  });
});

describe("formatRelativeTime", () => {
  it("uses just-now / minutes / hours", () => {
    const now = Date.parse("2026-09-13T06:10:00Z");
    expect(formatRelativeTime("2026-09-13T06:09:40Z", now)).toBe("刚刚");
    expect(formatRelativeTime("2026-09-13T06:05:00Z", now)).toBe("5 分钟前");
    expect(formatRelativeTime("2026-09-13T03:10:00Z", now)).toBe("3 小时前");
  });
});

describe("pathsReferToSameFile", () => {
  it("treats slash variants as the same workbook", () => {
    expect(pathsReferToSameFile("./web/public/samples/a.xlsx", "web\\public\\samples\\a.xlsx")).toBe(true);
  });
});

describe("resolveWorkbookPanelPath", () => {
  it("prefers the currently open workbook", () => {
    expect(
      resolveWorkbookPanelPath("./a.xlsx", [{ path: "./b.xlsx", workspaceKey: "id:ws" }], "id:ws"),
    ).toBe("./a.xlsx");
  });

  it("falls back to the most recent workbook in the current workspace", () => {
    expect(
      resolveWorkbookPanelPath(null, [{ path: "./recent.xlsx", workspaceKey: "id:ws" }], "id:ws"),
    ).toBe("./recent.xlsx");
  });

  it("ignores recent files without the current workspaceKey", () => {
    expect(resolveWorkbookPanelPath(null, [{ path: "./legacy.xlsx" }], "id:ws")).toBeUndefined();
  });

  it("does not use an image as the active workbook", () => {
    expect(
      resolveWorkbookPanelPath("./receipt.jpg", [{ path: "./sales.xlsx", workspaceKey: "id:ws" }], "id:ws"),
    ).toBe("./sales.xlsx");
  });

  it("returns undefined when nothing is available", () => {
    expect(resolveWorkbookPanelPath(null, [])).toBeUndefined();
  });
});
