import type { WordSnapshotResponse } from "@/lib/api";

export interface WordSnapshotRunView {
  text: string;
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
  color?: string;
  sizePt?: number;
}

export interface WordSnapshotParagraphView {
  text: string;
  headingLevel?: number;
  styleLabel: string;
  runs: WordSnapshotRunView[];
}

export interface WordSnapshotTableView {
  index: number;
  rows: number;
  columns: number;
  data: string[][];
}

export interface WordSnapshotStats {
  paragraphCount: number;
  tableCount: number;
  truncated: boolean;
  returnedParagraphs: number;
  totalParagraphs: number;
  totalTables: number;
}

export interface WordSnapshotViewModel {
  stats: WordSnapshotStats;
  paragraphs: WordSnapshotParagraphView[];
  tables: WordSnapshotTableView[];
}

function normalizeRunColor(color?: string): string | undefined {
  const trimmed = color?.trim();
  if (!trimmed) return undefined;
  return trimmed.startsWith("#") ? trimmed : `#${trimmed}`;
}

function toRunView(run: {
  text: string;
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
  color?: string;
  size_pt?: number;
}): WordSnapshotRunView {
  const view: WordSnapshotRunView = { text: run.text };
  if (run.bold) view.bold = true;
  if (run.italic) view.italic = true;
  if (run.underline) view.underline = true;
  const color = normalizeRunColor(run.color);
  if (color) view.color = color;
  if (run.size_pt != null) view.sizePt = run.size_pt;
  return view;
}

function toParagraphView(
  paragraph: WordSnapshotResponse["paragraphs"][number],
): WordSnapshotParagraphView {
  const runs =
    paragraph.runs && paragraph.runs.length > 0
      ? paragraph.runs.map(toRunView)
      : paragraph.text
        ? [{ text: paragraph.text }]
        : [];

  const view: WordSnapshotParagraphView = {
    text: paragraph.text,
    styleLabel: paragraph.style || "Normal",
    runs,
  };
  if (paragraph.heading_level !== undefined) {
    view.headingLevel = paragraph.heading_level;
  }
  return view;
}

function toTableView(
  table: WordSnapshotResponse["tables"][number],
): WordSnapshotTableView {
  return {
    index: table.index,
    rows: table.rows,
    columns: table.columns,
    data: table.data,
  };
}

export function toWordSnapshotViewModel(
  snapshot: WordSnapshotResponse,
): WordSnapshotViewModel {
  const paragraphs = (snapshot.paragraphs ?? []).map(toParagraphView);
  const tables = (snapshot.tables ?? []).map(toTableView);

  return {
    stats: {
      paragraphCount: paragraphs.length,
      tableCount: tables.length,
      truncated: Boolean(snapshot.truncated),
      returnedParagraphs: snapshot.returned_paragraphs ?? paragraphs.length,
      totalParagraphs: snapshot.total_paragraphs ?? paragraphs.length,
      totalTables: snapshot.total_tables ?? tables.length,
    },
    paragraphs,
    tables,
  };
}
