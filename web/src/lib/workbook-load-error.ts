/**
 * Failure taxonomy for a workbook read (`GET /api/v1/workbooks/observe`).
 *
 * Every failure used to collapse into「文件已删除或不存在」. That hides the real
 * cause — the backend answers 404 only when the resolved path is genuinely not a
 * readable file, while scope problems are 400/409 and transport faults are 5xx
 * or network errors — and it left the grid on the loading shell forever because
 * nothing retried.
 *
 * Classifying the answer lets the pane show the true reason, retry faults that
 * can heal by themselves, and retire a target the backend has confirmed is gone.
 */

export type WorkbookLoadFailureKind =
  /** Backend resolved the path but it is not a readable file. */
  | "missing"
  /** Session and file workspace disagree, or the scope could not be resolved. */
  | "scope"
  /** The read token is older than the file; a refresh re-reads the live version. */
  | "stale"
  /** Network/service fault that may succeed on retry. */
  | "transient"
  | "unknown";

export interface WorkbookLoadFailure {
  kind: WorkbookLoadFailureKind;
  /** User-facing copy for this kind. */
  message: string;
  /**
   * True only when the backend explicitly answered "this path is not a file
   * here" (HTTP 404, or a PATH_INVALID/NOT_FOUND code). Message-only matches are
   * not enough to close a user's tab: a reworded server or a proxied error must
   * not be treated as a deletion.
   */
  confirmedMissing: boolean;
}

/** Retry delays in ms; the length also caps the number of automatic attempts. */
const RETRY_DELAYS = [600, 1800] as const;

function errorStatus(err: unknown): number | undefined {
  const status = (err as { status?: unknown } | null)?.status;
  return typeof status === "number" ? status : undefined;
}

function errorCode(err: unknown): string {
  const code = (err as { code?: unknown } | null)?.code;
  return typeof code === "string" ? code.toUpperCase() : "";
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err ?? "");
}

const MISSING_TEXT = /(?:文件不存在|文件未找到|文件已删除|not found|\b404\b)/i;
const SCOPE_TEXT = /(?:无法确定工作区|工作区不一致|工作区|FILE_SCOPE)/i;
const TRANSIENT_TEXT = /(?:ECONNRESET|socket hang up|fetch failed|Failed to fetch|Failed to proxy|network|timeout|timed out|503|502|504|服务暂时)/i;

function missingCodes(code: string): boolean {
  return code === "PATH_INVALID" || code === "NOT_FOUND" || code === "FILE_NOT_FOUND";
}

function scopeCodes(code: string): boolean {
  return code === "FILE_SCOPE_REQUIRED" || code === "FILE_SCOPE_MISMATCH";
}

export function classifyWorkbookLoadError(err: unknown): WorkbookLoadFailureKind {
  const status = errorStatus(err);
  const code = errorCode(err);
  const text = errorText(err);
  if (status === 404 || missingCodes(code)) return "missing";
  if (code === "STALE_VIEW" || /\bSTALE_VIEW\b/.test(text)) return "stale";
  if (scopeCodes(code) || status === 409) return "scope";
  if (status === 400 || code === "INVALID_ARGS") {
    // A 400 is either a rejected path or a rejected argument. Only the missing
    // wording (an SnapshotError carries PATH_INVALID) may be read as absent.
    return MISSING_TEXT.test(text) ? "missing" : scopeCodes(code) ? "scope" : "unknown";
  }
  if (typeof status === "number" && status >= 500) return "transient";
  if (TRANSIENT_TEXT.test(text)) return "transient";
  if (SCOPE_TEXT.test(text)) return "scope";
  if (MISSING_TEXT.test(text)) return "missing";
  return "unknown";
}

const MESSAGES: Record<WorkbookLoadFailureKind, string> = {
  missing: "文件已删除或不存在。请从文件列表或「打开表格」重新选择文件。",
  scope: "当前对话与文件所属工作区不一致。请切换到该文件的对话，或从「打开表格」重新选择文件。",
  stale: "文件已被更新，正在重新读取最新版本…",
  transient: "读取表格失败（服务暂时不可用），正在自动重试…",
  unknown: "",
};

export function workbookLoadFailure(err: unknown): WorkbookLoadFailure {
  const kind = classifyWorkbookLoadError(err);
  const status = errorStatus(err);
  const code = errorCode(err);
  return {
    kind,
    message: MESSAGES[kind] || errorText(err) || "加载失败",
    confirmedMissing: kind === "missing" && (status === 404 || missingCodes(code)),
  };
}

/**
 * ``attempt`` is the number of automatic retries already spent for this file.
 * A confirmed miss gets exactly one retry so a write in flight cannot close the
 * pane; transient faults get the full budget.
 */
export function shouldRetryWorkbookLoad(kind: WorkbookLoadFailureKind, attempt: number): boolean {
  if (attempt >= RETRY_DELAYS.length) return false;
  if (kind === "transient" || kind === "stale") return true;
  if (kind === "missing") return attempt === 0;
  return false;
}

export function workbookLoadRetryDelay(attempt: number): number {
  return RETRY_DELAYS[Math.min(attempt, RETRY_DELAYS.length - 1)];
}
