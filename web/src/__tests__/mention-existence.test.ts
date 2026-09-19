import { describe, expect, it } from "vitest";
import {
  extractTypedFileMentions,
  findMissingFileMentions,
  shouldBlockMissingFileMentions,
} from "@/lib/mention-existence";

describe("extractTypedFileMentions", () => {
  it("returns an empty list for empty input", () => {
    expect(extractTypedFileMentions("")).toEqual([]);
    expect(extractTypedFileMentions("   ")).toEqual([]);
  });

  it("extracts a plain @file: path", () => {
    expect(extractTypedFileMentions("分析 @file:sales.xlsx")).toEqual(["sales.xlsx"]);
  });

  it("strips [range] and @sha256: and keeps only the path", () => {
    const hex = "a".repeat(64);
    const text =
      `@file:uploads/订单与产品.xlsx[Sheet1!A1:C10]@sha256:${hex}`;
    expect(extractTypedFileMentions(text)).toEqual(["uploads/订单与产品.xlsx"]);
  });

  it("does not swallow @sha256 into the path when range is absent", () => {
    expect(extractTypedFileMentions("@file:uploads/sales.xlsx@sha256:abcd")).toEqual([
      "uploads/sales.xlsx",
    ]);
  });

  it("extracts Chinese filenames", () => {
    expect(extractTypedFileMentions("读取 @file:广告与销售数据.csv")).toEqual([
      "广告与销售数据.csv",
    ]);
  });

  it("extracts multiple tokens in appearance order", () => {
    const text = "对比 @file:a.xlsx 和 @file:uploads/b.xlsx[Data!A1]";
    expect(extractTypedFileMentions(text)).toEqual(["a.xlsx", "uploads/b.xlsx"]);
  });

  it("does not treat a duplicate raw path twice", () => {
    expect(extractTypedFileMentions("@file:a.xlsx 再看 @file:a.xlsx")).toEqual([
      "a.xlsx",
    ]);
  });

  it("does not pick up bare @name mentions", () => {
    expect(extractTypedFileMentions("读取数据 @广告与销售数据.csv")).toEqual([]);
  });

  it("ignores @folder / @skill / @mcp", () => {
    const text = "@folder:outputs/ @skill:data_basic @mcp:mongodb 以及普通文字";
    expect(extractTypedFileMentions(text)).toEqual([]);
  });

  it("still extracts @file: when other mention kinds are present", () => {
    const text = "@skill:chart_basic 分析 @file:uploads/sales.xlsx @folder:src/";
    expect(extractTypedFileMentions(text)).toEqual(["uploads/sales.xlsx"]);
  });
});

describe("findMissingFileMentions", () => {
  it("returns empty for empty input even if known paths exist", () => {
    expect(findMissingFileMentions("", ["sales.xlsx"])).toEqual([]);
  });

  it("treats a listed path as present", () => {
    expect(
      findMissingFileMentions("@file:uploads/sales.xlsx", ["uploads/sales.xlsx"]),
    ).toEqual([]);
  });

  it("reports paths that are not in the workspace list", () => {
    expect(
      findMissingFileMentions("@file:missing.xlsx", ["uploads/sales.xlsx"]),
    ).toEqual(["missing.xlsx"]);
  });

  it("normalizes ./ prefix, leading slash, and backslash before compare", () => {
    const known = ["uploads/sales.xlsx"];
    expect(findMissingFileMentions("@file:./uploads/sales.xlsx", known)).toEqual([]);
    expect(findMissingFileMentions("@file:/uploads/sales.xlsx", known)).toEqual([]);
    expect(findMissingFileMentions("@file:uploads\\sales.xlsx", known)).toEqual([]);
  });

  it("normalizes known paths the same way", () => {
    expect(
      findMissingFileMentions("@file:uploads/sales.xlsx", ["./uploads/sales.xlsx"]),
    ).toEqual([]);
    expect(
      findMissingFileMentions("@file:uploads/sales.xlsx", ["\\uploads\\sales.xlsx"]),
    ).toEqual([]);
  });

  it("is case-sensitive", () => {
    expect(
      findMissingFileMentions("@file:Sales.xlsx", ["sales.xlsx"]),
    ).toEqual(["Sales.xlsx"]);
  });

  it("does not treat a similar substring as the same file", () => {
    expect(
      findMissingFileMentions("@file:uploads/sales.xlsx", ["sales.xlsx"]),
    ).toEqual(["uploads/sales.xlsx"]);
    expect(
      findMissingFileMentions("@file:sales.xlsx", ["uploads/sales.xlsx"]),
    ).toEqual(["sales.xlsx"]);
    expect(
      findMissingFileMentions("@file:my-sales.xlsx", ["sales.xlsx"]),
    ).toEqual(["my-sales.xlsx"]);
  });

  it("does not flag folder/skill/mcp or bare @name as missing files", () => {
    const text = "@folder:outputs/ @skill:data_basic @广告与销售数据.csv";
    expect(findMissingFileMentions(text, [])).toEqual([]);
  });
});

describe("shouldBlockMissingFileMentions", () => {
  it("does not block when knownPaths is empty even if missing mentions exist", () => {
    expect(shouldBlockMissingFileMentions([], ["missing.xlsx"])).toBe(false);
  });

  it("blocks when knownPaths is non-empty and missing mentions exist", () => {
    expect(
      shouldBlockMissingFileMentions(["uploads/sales.xlsx"], ["missing.xlsx"]),
    ).toBe(true);
  });

  it("does not block when knownPaths is non-empty and nothing is missing", () => {
    expect(shouldBlockMissingFileMentions(["uploads/sales.xlsx"], [])).toBe(false);
  });
});
