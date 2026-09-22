interface SelectionRange {
  getRow?: () => number;
  getColumn?: () => number;
  getNumRows?: () => number;
  getNumColumns?: () => number;
  getHeight?: () => number;
  getWidth?: () => number;
  getRange?: () => { rangeType?: number };
}

function columnLetter(index: number): string {
  let value = index + 1;
  let result = "";
  while (value > 0) {
    result = String.fromCharCode(65 + ((value - 1) % 26)) + result;
    value = Math.floor((value - 1) / 26);
  }
  return result;
}

function rangeAddress(selected: SelectionRange): string | undefined {
  const row = selected.getRow?.();
  const col = selected.getColumn?.();
  const rows = selected.getHeight?.() ?? selected.getNumRows?.() ?? 1;
  const cols = selected.getWidth?.() ?? selected.getNumColumns?.() ?? 1;
  const type = selected.getRange?.().rangeType;
  const validRows = row != null && Number.isInteger(row) && row >= 0 && Number.isInteger(rows) && rows > 0;
  const validCols = col != null && Number.isInteger(col) && col >= 0 && Number.isInteger(cols) && cols > 0;
  // Univer RANGE_TYPE: ROW = 1, COLUMN = 2. Keep whole-axis intent in the reference.
  if (type === 1 && validRows) return `${row + 1}:${row + rows}`;
  if (type === 2 && validCols) return `${columnLetter(col)}:${columnLetter(col + cols - 1)}`;
  if (!validRows || !validCols) return undefined;
  const start = `${columnLetter(col)}${row + 1}`;
  const end = `${columnLetter(col + cols - 1)}${row + rows}`;
  return start === end ? start : `${start}:${end}`;
}

export function isSingleCellSelection(range: string): boolean {
  return /^\$?[A-Za-z]+\$?[1-9][0-9]*$/.test(range);
}

export function formatSelectionConfirmLabel(fileName: string, sheet: string, range: string, cellValue?: string): string {
  const count = range.split(",").length;
  const label = `引用 ${fileName} · ${sheet}!${range}${count > 1 ? `（${count} 个区域）` : ""}`;
  if (!isSingleCellSelection(range) || !cellValue) return label;
  const shown = cellValue.length > 40 ? `${cellValue.slice(0, 40)}…` : cellValue;
  return `${label}（值：${shown}）`;
}

/** Read every selected area without filling the gaps; sheet identity comes from the worksheet. */
export function readActiveRange(api: {
  getActiveWorkbook?: () => {
    getActiveSheet?: () => {
      getName?: () => string;
      getSheetName?: () => string;
      getSelection?: () => {
        getActiveRange?: () => SelectionRange | null;
        getActiveRangeList?: () => SelectionRange[];
      } | null;
    } | null;
  } | null;
} | null): { sheet?: string; range?: string } {
  try {
    const sheet = api?.getActiveWorkbook?.()?.getActiveSheet?.();
    const sheetName = sheet?.getName?.() || sheet?.getSheetName?.();
    if (!sheetName) return {};
    const selection = sheet?.getSelection?.();
    const active = selection?.getActiveRange?.();
    const selected = selection?.getActiveRangeList?.() ?? (active ? [active] : []);
    const addresses = selected.map(rangeAddress);
    if (!addresses.length || addresses.some((address) => !address)) return { sheet: sheetName };
    return { sheet: sheetName, range: [...new Set(addresses)].join(",") };
  } catch {
    return {};
  }
}
