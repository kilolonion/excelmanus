type UniverSheetLike = {
  getName?: () => string;
  getSheetName?: () => string;
  activate?: () => void;
};

type UniverWorkbookLike<Sheet extends UniverSheetLike> = {
  getSheets?: () => Sheet[];
  getSheetByName?: (name: string) => Sheet | null;
  setActiveSheet?: (sheet: Sheet) => unknown;
};

type UniverApiLike<Sheet extends UniverSheetLike> = {
  getActiveWorkbook?: () => UniverWorkbookLike<Sheet> | null | undefined;
};

export function activateWorkbookSheet<Sheet extends UniverSheetLike>(
  api: UniverApiLike<Sheet> | null | undefined,
  sheetName?: string,
): boolean {
  if (!api || !sheetName) return false;
  try {
    const workbook = api.getActiveWorkbook?.();
    if (!workbook) return false;
    const target = workbook.getSheetByName?.(sheetName)
      ?? workbook.getSheets?.()?.find((sheet) => (sheet.getSheetName?.() || sheet.getName?.()) === sheetName);
    if (!target) return false;
    if (workbook.setActiveSheet) workbook.setActiveSheet(target);
    else if (target.activate) target.activate();
    else return false;
    return true;
  } catch {
    return false;
  }
}
