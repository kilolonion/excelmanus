type UniverSheetLike = {
  getName?: () => string;
  activate?: () => void;
};

type UniverWorkbookLike = {
  getSheets?: () => UniverSheetLike[];
};

type UniverApiLike = {
  getActiveWorkbook?: () => UniverWorkbookLike | null | undefined;
};

export function activateWorkbookSheet(
  api: UniverApiLike | null | undefined,
  sheetName?: string,
): boolean {
  if (!api || !sheetName) return false;
  try {
    const workbook = api.getActiveWorkbook?.();
    if (!workbook) return false;
    const target = workbook.getSheets?.()?.find((sheet) => sheet.getName?.() === sheetName);
    if (!target) return false;
    target.activate?.();
    return true;
  } catch {
    return false;
  }
}
