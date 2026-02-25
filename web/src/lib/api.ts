import type { SessionDetail } from "@/lib/types";
import { useAuthStore } from "@/stores/auth-store";

const API_BASE_PATH = "/api/v1";

function getAuthHeaders(): Record<string, string> {
  const token = useAuthStore.getState().accessToken;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

/**
 * 解析后端直连地址（用于 SSE 流等必须绕过 Next.js 代理的场景）。
 *
 * 优先级：
 * 1. NEXT_PUBLIC_BACKEND_ORIGIN 环境变量（构建时内联）
 * 2. 空字符串 — 走同源 Nginx 代理（生产环境推荐，/api/ 已配置 proxy_buffering off）
 * 3. 回退：window.location.hostname:8000（仅适用于前后端同机开发）
 *
 * 生产环境如果 Nginx 已配置 /api/ 反向代理且关闭了 buffering，
 * 留空 NEXT_PUBLIC_BACKEND_ORIGIN 即可，所有请求走同源，无 CORS 问题。
 */
function resolveDirectBackendOrigin(): string {
  const configured = process.env.NEXT_PUBLIC_BACKEND_ORIGIN?.trim();
  if (configured) {
    return trimTrailingSlash(configured);
  }
  // 未配置时走同源（Nginx 反向代理 /api/ → 后端，buffering 已关闭）
  // 返回空字符串，让请求路径保持 /api/v1/... 同源访问
  return "";
}

/**
 * 解析 API 基础路径。
 *
 * - 默认：走 Next.js rewrite 代理（同源，避免 CORS）
 * - direct: true：直连后端（用于 SSE 流，因为 Next.js rewrite 会缓冲整个响应）
 *
 * 普通 REST 请求一律走代理，只有 SSE/abort 等实时性要求高的请求才用 direct。
 */
function resolveApiBase(opts?: { direct?: boolean }): string {
  // Most browser requests route through the Next.js rewrite proxy so that
  // requests stay same-origin (avoids CORS when accessed from LAN devices).
  //
  // However, SSE (Server-Sent Events) streams MUST bypass the proxy because
  // Next.js rewrites buffer the entire response before forwarding it to the
  // client, which completely breaks real-time streaming.  Callers that need
  // a live stream pass `direct: true` to connect straight to the backend.
  if (typeof window !== "undefined") {
    if (opts?.direct) {
      return `${resolveDirectBackendOrigin()}${API_BASE_PATH}`;
    }
    return API_BASE_PATH;
  }
  if (opts?.direct) {
    return `${resolveDirectBackendOrigin()}${API_BASE_PATH}`;
  }
  return API_BASE_PATH;
}

export function buildApiUrl(path: string, opts?: { direct?: boolean }): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${resolveApiBase(opts)}${normalizedPath}`;
}

async function handleAuthError(res: Response): Promise<never> {
  if (res.status === 401) {
    const { refreshToken, logout } = useAuthStore.getState();
    if (refreshToken) {
      const { refreshAccessToken } = await import("./auth-api");
      const ok = await refreshAccessToken();
      if (!ok) {
        if (typeof window !== "undefined") window.location.href = "/login";
      }
    } else {
      logout();
      if (typeof window !== "undefined") window.location.href = "/login";
    }
  }
  const data = await res.json().catch(() => ({}));
  throw new Error(data.error || data.detail || `API error: ${res.status}`);
}

export async function apiGet<T = unknown>(path: string): Promise<T> {
  const res = await fetch(buildApiUrl(path), {
    headers: { ...getAuthHeaders() },
  });
  if (!res.ok) return handleAuthError(res);
  return res.json();
}

export async function apiPost<T = unknown>(
  path: string,
  body: unknown
): Promise<T> {
  const res = await fetch(buildApiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(body),
  });
  if (!res.ok) return handleAuthError(res);
  return res.json();
}

export async function apiPut<T = unknown>(
  path: string,
  body: unknown
): Promise<T> {
  const res = await fetch(buildApiUrl(path), {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(body),
  });
  if (!res.ok) return handleAuthError(res);
  return res.json();
}

export async function apiPatch<T = unknown>(
  path: string,
  body: unknown
): Promise<T> {
  const res = await fetch(buildApiUrl(path), {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(body),
  });
  if (!res.ok) return handleAuthError(res);
  return res.json();
}

export async function apiDelete(path: string): Promise<void> {
  const res = await fetch(buildApiUrl(path), {
    method: "DELETE",
    headers: { ...getAuthHeaders() },
  });
  if (!res.ok) return handleAuthError(res);
}

export async function fetchSessions(opts?: {
  includeArchived?: boolean;
}): Promise<unknown[]> {
  const params = new URLSearchParams();
  if (opts?.includeArchived) {
    params.set("include_archived", "true");
  }
  const qs = params.toString();
  const res: { sessions?: unknown[] } = await apiGet(
    `/sessions${qs ? `?${qs}` : ""}`
  );
  return res.sessions ?? [];
}

export async function fetchSessionDetail(
  sessionId: string
): Promise<SessionDetail | null> {
  const res = await fetch(buildApiUrl(`/sessions/${encodeURIComponent(sessionId)}`), {
    headers: { ...getAuthHeaders() },
  });
  if (res.status === 404) {
    return null;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(
      body.error || body.detail || `API error: ${res.status}`
    );
  }
  const data = (await res.json()) as Record<string, unknown>;

  // 解析待处理审批
  let pendingApproval: import("@/lib/types").Approval | null = null;
  const pa = data.pending_approval as Record<string, unknown> | null;
  if (pa && pa.approval_id) {
    pendingApproval = {
      id: (pa.approval_id as string) || "",
      toolName: (pa.tool_name as string) || "",
      arguments: {},
      riskLevel: (pa.risk_level as "high" | "medium" | "low") || "high",
      argsSummary: (pa.args_summary as Record<string, string>) || {},
    };
  }

  // 解析待处理问题
  let pendingQuestion: import("@/lib/types").Question | null = null;
  const pq = data.pending_question as Record<string, unknown> | null;
  if (pq && pq.id) {
    pendingQuestion = {
      id: (pq.id as string) || "",
      header: (pq.header as string) || "",
      text: (pq.text as string) || "",
      options: (pq.options as { label: string; description: string }[]) || [],
      multiSelect: Boolean(pq.multi_select),
    };
  }

  return {
    id: (data.id as string) ?? sessionId,
    messageCount: (data.message_count as number) ?? 0,
    inFlight: (data.in_flight as boolean) ?? false,
    fullAccessEnabled: (data.full_access_enabled as boolean) ?? false,
    planModeEnabled: (data.plan_mode_enabled as boolean) ?? false,
    currentModel: (data.current_model as string | null) ?? null,
    currentModelName: (data.current_model_name as string | null) ?? null,
    messages: Array.isArray(data.messages) ? (data.messages as unknown[]) : [],
    pendingApproval,
    pendingQuestion,
    lastRoute: (() => {
      const lr = data.last_route as Record<string, unknown> | null;
      if (!lr || !lr.route_mode) return null;
      return {
        routeMode: (lr.route_mode as string) || "",
        skillsUsed: (lr.skills_used as string[]) || [],
        toolScope: (lr.tool_scope as string[]) || [],
      };
    })(),
  };
}

export async function deleteSession(sessionId: string): Promise<void> {
  await apiDelete(`/sessions/${encodeURIComponent(sessionId)}`);
}

export async function clearAllSessions(): Promise<{
  sessions_deleted: number;
  messages_deleted: number;
}> {
  const res = await fetch(buildApiUrl("/sessions"), {
    method: "DELETE",
    headers: { ...getAuthHeaders() },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `API error: ${res.status}`);
  }
  return res.json();
}

export async function archiveSession(
  sessionId: string,
  archive: boolean
): Promise<{ status: string; session_id: string; archived: boolean }> {
  return apiPatch(`/sessions/${encodeURIComponent(sessionId)}/archive`, {
    archive,
  });
}

export async function fetchSessionMessages(
  sessionId: string,
  limit = 50,
  offset = 0
): Promise<unknown[]> {
  const res: { messages?: unknown[] } = await apiGet(
    `/sessions/${sessionId}/messages?limit=${limit}&offset=${offset}`
  );
  return res.messages ?? [];
}

export interface PersistedExcelDiff {
  tool_call_id: string;
  file_path: string;
  sheet: string;
  affected_range: string;
  changes: { cell: string; old: string | number | boolean | null; new: string | number | boolean | null }[];
  timestamp: string;
}

export interface PersistedExcelPreview {
  tool_call_id: string;
  file_path: string;
  sheet: string;
  columns: string[];
  rows: (string | number | null)[][];
  total_rows: number;
  truncated: boolean;
}

export interface SessionExcelEventsResponse {
  diffs: PersistedExcelDiff[];
  previews: PersistedExcelPreview[];
  affected_files: string[];
}

export async function fetchSessionExcelEvents(
  sessionId: string
): Promise<SessionExcelEventsResponse> {
  try {
    return await apiGet<SessionExcelEventsResponse>(
      `/sessions/${sessionId}/excel-events`
    );
  } catch {
    return { diffs: [], previews: [], affected_files: [] };
  }
}

export interface ApprovalRecord {
  id: string;
  tool_name: string;
  created_at_utc: string;
  applied_at_utc: string;
  execution_status: string;
  undoable: boolean;
  result_preview: string;
  arguments?: Record<string, unknown>;
  changes?: { path: string; before_exists: boolean; after_exists: boolean }[];
}

export async function fetchApprovals(opts?: {
  limit?: number;
  undoableOnly?: boolean;
  sessionId?: string;
}): Promise<ApprovalRecord[]> {
  if (!opts?.sessionId) return [];
  const params = new URLSearchParams();
  if (opts?.limit) params.set("limit", String(opts.limit));
  if (opts?.undoableOnly) params.set("undoable_only", "true");
  params.set("session_id", opts.sessionId);
  const qs = params.toString();
  const res: { approvals?: ApprovalRecord[] } = await apiGet(
    `/approvals${qs ? `?${qs}` : ""}`
  );
  return res.approvals ?? [];
}

export async function undoApproval(approvalId: string, sessionId: string): Promise<{
  status: string;
  message: string;
  approval_id: string;
}> {
  const qs = new URLSearchParams({ session_id: sessionId }).toString();
  return apiPost(`/approvals/${approvalId}/undo?${qs}`, {});
}

export async function rollbackChat(opts: {
  sessionId: string;
  turnIndex: number;
  rollbackFiles?: boolean;
  newMessage?: string;
}): Promise<{
  status: string;
  removed_messages: number;
  file_rollback_results: string[];
  turn_index: number;
}> {
  const res = await fetch(buildApiUrl("/chat/rollback"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({
      session_id: opts.sessionId,
      turn_index: opts.turnIndex,
      rollback_files: opts.rollbackFiles ?? false,
      new_message: opts.newMessage ?? null,
    }),
  });
  if (!res.ok) return handleAuthError(res);
  return res.json();
}

// ── Excel 预览 API ────────────────────────────────────────

export interface ExcelSnapshot {
  file: string;
  sheet: string;
  sheets: string[];
  shape: { rows: number; columns: number };
  column_letters: string[];
  headers: string[];
  rows: (string | number | null)[][];
  total_rows: number;
  truncated: boolean;
}

/**
 * Normalize a file path for API calls and comparisons.
 *
 * Handles:
 * - Masked paths: ``<path>/foo.xlsx`` -> ``./foo.xlsx``
 * - Absolute paths: keep as-is for backend workspace validation
 * - Missing ``./`` prefix: ``uploads/foo.xlsx`` -> ``./uploads/foo.xlsx``
 * - Double slashes: ``./uploads//foo.xlsx`` -> ``./uploads/foo.xlsx``
 */
export function normalizeExcelPath(path: string): string {
  const raw = String(path ?? "").trim();
  if (!raw) return "";
  if (raw.startsWith("<path>/")) {
    const basename = raw.slice("<path>/".length).trim();
    return basename ? `./${basename}` : "";
  }
  let p = raw.replace(/\/\/+/g, "/");
  // Keep absolute paths intact so backend can validate them against workspace.
  if (p.startsWith("/")) return p;
  if (!p.startsWith("./")) p = `./${p}`;
  return p;
}

function extractSanitizedPathBasename(path: string): string | null {
  const raw = String(path ?? "").trim();
  if (!raw.startsWith("<path>/")) return null;
  const basename = raw.slice("<path>/".length).trim();
  return basename || null;
}

function buildExcelSnapshotUrl(
  path: string,
  opts?: { sheet?: string; maxRows?: number; sessionId?: string },
): string {
  const params = new URLSearchParams({ path: normalizeExcelPath(path) });
  if (opts?.sheet) params.set("sheet", opts.sheet);
  if (opts?.maxRows) params.set("max_rows", String(opts.maxRows));
  if (opts?.sessionId) params.set("session_id", opts.sessionId);
  return buildApiUrl(`/files/excel/snapshot?${params.toString()}`);
}

export function buildExcelFileUrl(path: string, sessionId?: string): string {
  const params = new URLSearchParams({ path: normalizeExcelPath(path) });
  if (sessionId) params.set("session_id", sessionId);
  return buildApiUrl(`/files/excel?${params.toString()}`);
}

export interface ExcelFileListItem {
  path: string;
  filename: string;
  modified_at: number;
}

export async function fetchExcelFiles(): Promise<ExcelFileListItem[]> {
  const url = buildApiUrl("/files/excel/list");
  const res = await fetch(url, { headers: { ...getAuthHeaders() } });
  if (!res.ok) return [];
  const data = await res.json();
  return data.files ?? [];
}

export async function fetchWorkspaceFiles(): Promise<ExcelFileListItem[]> {
  const url = buildApiUrl("/files/workspace/list");
  const res = await fetch(url, { headers: { ...getAuthHeaders() } });
  if (!res.ok) return [];
  const data = await res.json();
  return data.files ?? [];
}

export async function fetchExcelSnapshot(
  path: string,
  opts?: { sheet?: string; maxRows?: number; sessionId?: string }
): Promise<ExcelSnapshot> {
  const res = await fetch(buildExcelSnapshotUrl(path, opts), {
    headers: { ...getAuthHeaders() },
  });
  if (!res.ok) {
    // 兼容历史脱敏路径 "<path>/foo.xlsx"：尝试按 basename 回查 workspace 文件并重试
    const maskedBasename = extractSanitizedPathBasename(path);
    if (maskedBasename) {
      const files = await fetchWorkspaceFiles().catch(() => []);
      const matches = files.filter((f) => f.filename === maskedBasename);
      if (matches.length === 1) {
        const retryRes = await fetch(buildExcelSnapshotUrl(matches[0].path, opts), {
          headers: { ...getAuthHeaders() },
        });
        if (retryRes.ok) {
          return retryRes.json();
        }
      }
    }
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Snapshot error: ${res.status}`);
  }
  return res.json();
}

export async function writeExcelCells(opts: {
  path: string;
  sheet?: string;
  changes: { cell: string; value: unknown }[];
  sessionId?: string;
}): Promise<{ status: string; cells_written: number }> {
  const url = buildApiUrl("/files/excel/write");
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({
      path: normalizeExcelPath(opts.path),
      sheet: opts.sheet ?? null,
      changes: opts.changes,
      session_id: opts.sessionId ?? null,
    }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Write error: ${res.status}`);
  }
  return res.json();
}

export async function downloadFile(path: string, filename?: string, sessionId?: string): Promise<void> {
  const params = new URLSearchParams({ path: normalizeExcelPath(path) });
  if (sessionId) params.set("session_id", sessionId);
  const url = buildApiUrl(`/files/download?${params.toString()}`);
  const res = await fetch(url, {
    headers: { ...getAuthHeaders() },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Download error: ${res.status}`);
  }
  const blob = await res.blob();
  const name = filename || path.split("/").pop() || "download";
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(objectUrl);
}

export async function uploadFile(file: File): Promise<{
  filename: string;
  path: string;
  size: number;
}> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(buildApiUrl("/upload"), {
    method: "POST",
    headers: { ...getAuthHeaders() },
    body: formData,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Upload error: ${res.status}`);
  }
  return res.json();
}

// ── Workspace Transaction API (formerly Backup Apply) ────

export interface BackupFile {
  original_path: string;
  backup_path: string;
  exists: boolean;
  modified_at: number | null;
}

export interface BackupListResponse {
  files: BackupFile[];
  backup_enabled: boolean;
}

export async function fetchBackupList(
  sessionId: string
): Promise<BackupListResponse> {
  const url = buildApiUrl(
    `/workspace/staged?session_id=${encodeURIComponent(sessionId)}`
  );
  const res = await fetch(url, { headers: { ...getAuthHeaders() } });
  if (!res.ok) {
    return { files: [], backup_enabled: false };
  }
  return res.json();
}

export async function applyBackup(opts: {
  sessionId: string;
  files?: string[];
}): Promise<{ status: string; applied: { original: string; backup: string }[]; count: number }> {
  const url = buildApiUrl("/workspace/commit");
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({
      session_id: opts.sessionId,
      files: opts.files ?? null,
    }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || data.detail || `Commit error: ${res.status}`);
  }
  return res.json();
}

export async function discardBackup(opts: {
  sessionId: string;
  files?: string[];
}): Promise<{ status: string; discarded: number | string }> {
  const url = buildApiUrl("/workspace/rollback");
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({
      session_id: opts.sessionId,
      files: opts.files ?? null,
    }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || data.detail || `Rollback error: ${res.status}`);
  }
  return res.json();
}
