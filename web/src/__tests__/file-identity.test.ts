import { describe, expect, it } from "vitest";
import {
  collectHistoryAffectedFiles,
  displayFileName,
  mergeAffectedFiles,
  toPublicFileIdentity,
} from "@/lib/file-identity";

describe("toPublicFileIdentity", () => {
  it("normalizes ./ vs missing prefix", () => {
    expect(toPublicFileIdentity("sales.xlsx")).toBe("./sales.xlsx");
    expect(toPublicFileIdentity("./sales.xlsx")).toBe("./sales.xlsx");
  });

  it("extracts uploads/ from absolute paths", () => {
    expect(toPublicFileIdentity("/tmp/ws/uploads/abcd1234_sales.xlsx")).toBe(
      "./uploads/abcd1234_sales.xlsx",
    );
  });

  it("drops reserved and timestamped backup leftovers", () => {
    expect(toPublicFileIdentity("outputs/backups/foo_20260911T091344_f525.xlsx")).toBeNull();
    expect(toPublicFileIdentity("./outputs/backups/report.xlsx")).toBeNull();
    expect(toPublicFileIdentity(".excelmanus/revisions/x.xlsx")).toBeNull();
    expect(toPublicFileIdentity("foo_20260911T091344_abcd.xlsx")).toBeNull();
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
  });
});

describe("collectHistoryAffectedFiles", () => {
  const writeTools = new Set(["edit_spreadsheet", "run_code"]);

  it("uses write-tool file_path only and ignores reserved leftovers", () => {
    expect(
      collectHistoryAffectedFiles(
        "edit_spreadsheet",
        { file_path: "sales.xlsx" },
        writeTools,
      ),
    ).toEqual(["./sales.xlsx"]);
    expect(
      collectHistoryAffectedFiles(
        "edit_spreadsheet",
        { file_path: "outputs/backups/sales_20260911T091344_f525.xlsx" },
        writeTools,
      ),
    ).toEqual([]);
  });
});
