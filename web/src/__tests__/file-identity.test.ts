import { describe, expect, it } from "vitest";
import {
  collectHistoryAffectedFiles,
  decodeUriEscapedPath,
  displayFileName,
  displayFilePath,
  mergeAffectedFiles,
  toPublicFileIdentity,
} from "@/lib/file-identity";

describe("toPublicFileIdentity", () => {
  it("normalizes ./ vs missing prefix", () => {
    expect(toPublicFileIdentity("sales.xlsx")).toBe("./sales.xlsx");
    expect(toPublicFileIdentity("./sales.xlsx")).toBe("./sales.xlsx");
  });

  it("does not infer a workspace from an absolute path", () => {
    expect(toPublicFileIdentity("/tmp/ws/uploads/abcd1234_sales.xlsx")).toBeNull();
    expect(toPublicFileIdentity("/tmp/ws/uploads/abcd1234_sales.xlsx", "/tmp/ws")).toBe(
      "./uploads/abcd1234_sales.xlsx",
    );
  });

  it("drops reserved and timestamped backup leftovers", () => {
    expect(toPublicFileIdentity("outputs/backups/foo_20260911T091344_f525.xlsx")).toBeNull();
    expect(toPublicFileIdentity("./outputs/backups/report.xlsx")).toBeNull();
    expect(toPublicFileIdentity(".excelmanus/revisions/x.xlsx")).toBeNull();
    expect(toPublicFileIdentity("foo_20260911T091344_abcd.xlsx")).toBeNull();
  });

  it("drops internal runtime scripts and staging paths", () => {
    expect(toPublicFileIdentity("_rc_12345678.py")).toBeNull();
    expect(toPublicFileIdentity("./_sw_abcdef12.py")).toBeNull();
    expect(toPublicFileIdentity("scripts/temp/_rc_12345678.py")).toBeNull();
    expect(toPublicFileIdentity("scripts/temp/staging.xlsx")).toBeNull();
  });
});

describe("mergeAffectedFiles", () => {
  it("dedupes by identity not raw string", () => {
    expect(mergeAffectedFiles(["./sales.xlsx"], ["sales.xlsx", "./sales.xlsx"])).toEqual([
      "./sales.xlsx",
    ]);
  });

  it("does not keep timestamped backup names when original is present", () => {
    expect(
      mergeAffectedFiles(
        ["./sales.xlsx"],
        ["outputs/backups/sales_20260911T091344_f525.xlsx"],
      ),
    ).toEqual(["./sales.xlsx"]);
  });
});

describe("displayFileName", () => {
  it("strips uploads hex prefix", () => {
    expect(displayFileName("./uploads/abcd1234_sales.xlsx")).toBe("sales.xlsx");
    expect(displayFileName("uploads/abcd1234_sales.xlsx")).toBe("sales.xlsx");
  });

  it("strips the hex prefix at any depth under uploads/", () => {
    expect(displayFileName("uploads/sub/abcd1234_sales.xlsx")).toBe("sales.xlsx");
  });

  it("strips the timestamped backup suffix", () => {
    expect(displayFileName("outputs/backups/sales_20260911T091344_f525.xlsx")).toBe("sales.xlsx");
    expect(displayFileName("sales_20260911T091344_f525.xlsx")).toBe("sales.xlsx");
  });

  it("strips both prefix and suffix on backup copies of uploads", () => {
    expect(displayFileName("outputs/backups/abcd1234_sales_20260911T091344_f525.xlsx")).toBe("sales.xlsx");
  });

  it("keeps ordinary names untouched", () => {
    expect(displayFileName("销售明细.xlsx")).toBe("销售明细.xlsx");
    expect(displayFileName("deadbeef.xlsx")).toBe("deadbeef.xlsx");
    expect(displayFileName("data/ab12_report.xlsx")).toBe("ab12_report.xlsx");
  });
});

describe("displayFilePath", () => {
  it("keeps the directory and cleans only the leaf", () => {
    expect(displayFilePath("./uploads/abcd1234_sales.xlsx")).toBe("./uploads/sales.xlsx");
    expect(displayFilePath("outputs/backups/x_20260911T091344_abcd.csv")).toBe("outputs/backups/x.csv");
  });
});

describe("decodeUriEscapedPath", () => {
  it("decodes normalizeUri-encoded link hrefs back to workspace paths", () => {
    expect(
      decodeUriEscapedPath(
        "./outputs/%E6%94%B6%E6%AC%BE%E6%94%B6%E6%8D%AE_%E5%B8%83%E5%B1%80%E8%BF%98%E5%8E%9F%E7%89%88.xlsx",
      ),
    ).toBe("./outputs/收款收据_布局还原版.xlsx");
  });

  it("keeps plain and malformed inputs untouched", () => {
    expect(decodeUriEscapedPath("./outputs/sales.xlsx")).toBe("./outputs/sales.xlsx");
    expect(decodeUriEscapedPath("./outputs/100%zz.xlsx")).toBe("./outputs/100%zz.xlsx");
  });
});

describe("collectHistoryAffectedFiles", () => {
  const writeTools = new Set(["apply_spreadsheet_changes", "run_code"]);

  it("uses write-tool file_path only and ignores reserved leftovers", () => {
    expect(
      collectHistoryAffectedFiles(
        "apply_spreadsheet_changes",
        { file_path: "sales.xlsx" },
        writeTools,
      ),
    ).toEqual(["./sales.xlsx"]);
    expect(
      collectHistoryAffectedFiles(
        "apply_spreadsheet_changes",
        { file_path: "outputs/backups/sales_20260911T091344_f525.xlsx" },
        writeTools,
      ),
    ).toEqual([]);
  });
});
