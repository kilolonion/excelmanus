import { currentWorkbookSendContext, workbookSheetContext } from "./workbook-context";

/** Send identities only. Cell values and workbook contents never enter this hint. */
export function buildJevSheetContext(sessionId?: string | null) {
  const context = currentWorkbookSendContext(sessionId);
  return context ? workbookSheetContext(context) : undefined;
}
