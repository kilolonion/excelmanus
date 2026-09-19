import { describe, expect, it } from "vitest";
import type { WordSnapshotResponse } from "@/lib/api";
import { toWordSnapshotViewModel } from "@/lib/word-snapshot";

function makeSnapshot(
  overrides: Partial<WordSnapshotResponse> = {},
): WordSnapshotResponse {
  return {
    file: "report.docx",
    total_paragraphs: 0,
    returned_paragraphs: 0,
    truncated: false,
    paragraphs: [],
    tables: [],
    total_tables: 0,
    sections: 1,
    properties: {},
    ...overrides,
  };
}

describe("toWordSnapshotViewModel", () => {
  it("maps an empty snapshot to zero stats and empty render models", () => {
    const model = toWordSnapshotViewModel(makeSnapshot());

    expect(model.stats).toEqual({
      paragraphCount: 0,
      tableCount: 0,
      truncated: false,
      returnedParagraphs: 0,
      totalParagraphs: 0,
      totalTables: 0,
    });
    expect(model.paragraphs).toEqual([]);
    expect(model.tables).toEqual([]);
  });

  it("maps paragraph runs and keeps table data as-is", () => {
    const tableData = [
      ["姓名", "金额"],
      ["张三", "10"],
    ];
    const snapshot = makeSnapshot({
      total_paragraphs: 3,
      returned_paragraphs: 2,
      truncated: true,
      total_tables: 1,
      paragraphs: [
        {
          text: "报告标题",
          style: "Title",
          heading_level: 0,
        },
        {
          text: "Hello World",
          style: "Normal",
          runs: [
            { text: "Hello ", bold: true, color: "FF0000", size_pt: 12 },
            { text: "World", italic: true, underline: true },
          ],
        },
      ],
      tables: [
        {
          index: 0,
          rows: 2,
          columns: 2,
          data: tableData,
        },
      ],
    });

    const model = toWordSnapshotViewModel(snapshot);

    expect(model.stats).toEqual({
      paragraphCount: 2,
      tableCount: 1,
      truncated: true,
      returnedParagraphs: 2,
      totalParagraphs: 3,
      totalTables: 1,
    });
    expect(model.paragraphs).toEqual([
      {
        text: "报告标题",
        headingLevel: 0,
        styleLabel: "Title",
        runs: [{ text: "报告标题" }],
      },
      {
        text: "Hello World",
        styleLabel: "Normal",
        runs: [
          { text: "Hello ", bold: true, color: "#FF0000", sizePt: 12 },
          { text: "World", italic: true, underline: true },
        ],
      },
    ]);
    expect(model.tables).toEqual([
      {
        index: 0,
        rows: 2,
        columns: 2,
        data: [
          ["姓名", "金额"],
          ["张三", "10"],
        ],
      },
    ]);
    expect(model.tables[0].data).toBe(tableData);
  });
});
