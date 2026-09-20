/** Selection identity comes from the active worksheet; never invent a sheet name. */
export function readActiveRange(api: {
  getActiveWorkbook?: () => {
    getActiveSheet?: () => {
      getName?: () => string;
      getSheetName?: () => string;
      getSelection?: () => {
        getActiveRange?: () => {
          getRow?: () => number;
          getColumn?: () => number;
          getNumRows?: () => number;
          getNumColumns?: () => number;
          getHeight?: () => number;
          getWidth?: () => number;
        } | null;
      } | null;
    } | null;
  } | null;
} | null): { sheet?: string; range?: string } {
  try {
    const sheet = api?.getActiveWorkbook?.()?.getActiveSheet?.();
    const sheetName = sheet?.getName?.() || sheet?.getSheetName?.();
    if (!sheetName) return {};
    const selected = sheet?.getSelection?.()?.getActiveRange?.();
    const row = selected?.getRow?.();
    const col = selected?.getColumn?.();
    if (row == null || col == null) return { sheet: sheetName };
    const rows = selected?.getHeight?.() ?? selected?.getNumRows?.() ?? 1;
    const cols = selected?.getWidth?.() ?? selected?.getNumColumns?.() ?? 1;
    if (![row, col, rows, cols].every(Number.isInteger) || row < 0 || col < 0 || rows < 1 || cols < 1) {
      return { sheet: sheetName };
    }
    const letter = (index: number) => {
      let value = index + 1;
      let result = "";
      while (value > 0) {
        result = String.fromCharCode(65 + ((value - 1) % 26)) + result;
        value = Math.floor((value - 1) / 26);
      }
      return result;
    };
    const start = `${letter(col)}${row + 1}`;
    const end = `${letter(col + cols - 1)}${row + rows}`;
    return { sheet: sheetName, range: start === end ? start : `${start}:${end}` };
  } catch {
    return {};
  }
}
