import type { SessionDetail, SubagentRun, WorkspaceFolder } from "@/lib/types";
import { resolveDirectBackendOrigin } from "@/lib/backend-origin";
import { formatApiErrorMessage } from "@/lib/api-error";

const API_BASE_PATH = "/api/v1";
const MANAGE_TOKEN_STORAGE_KEY = "excelmanus_manage_token";

/** 普通 REST 请求的默认超时（毫秒）。上传/下载等大体积操作使用更长的超时。 */
const _DEFAULT_TIMEOUT_MS = 30_000;
const _UPLOAD_TIMEOUT_MS = 120_000;

/**
 * 创建一个带超时的 AbortSignal。如果调用方已提供 signal，则合并两者（任一触发即中止）。
 */
function _withTimeout(timeoutMs: number, existingSignal?: AbortSignal | null): AbortSignal {
  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  if (!existingSignal) return timeoutSignal;
  // AbortSignal.any 合并多个 signal（任一触发即中止）
  if (typeof AbortSignal.any === "function") {
    return AbortSignal.any([existingSignal, timeoutSignal]);
  }
  // Fallback for older browsers: prefer caller's signal, timeout won't apply
  return existingSignal;
}

export function getManageToken(): string {
  if (typeof window === "undefined") return "";
  try {
    return sessionStorage.getItem(MANAGE_TOKEN_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

export function setManageToken(token: string): void {
  if (typeof window === "undefined") return;
  try {
    if (token) sessionStorage.setItem(MANAGE_TOKEN_STORAGE_KEY, token);
    else sessionStorage.removeItem(MANAGE_TOKEN_STORAGE_KEY);
  } catch {
    /* ignore quota / private mode */
  }
}

export function getAuthHeaders(): Record<string, string> {
  const token = getManageToken();
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

/**
 * 解析 API 基础路径。
 *
 * - 配置了运行时后端地址时：直连后端。桌面版后端使用启动时分配的
 *   随机端口，构建时固化的 Next.js rewrite 无法转发到这个端口。
 * - 未配置运行时地址时：默认走 Next.js rewrite 代理（同源，避免 CORS）
 * - direct: true：在没有运行时地址时也尝试直连（用于 SSE 流）
 *
 * 没有运行时后端地址时，普通 REST 请求走代理；SSE/abort 等实时性要求高的
 * 请求传入 direct=true。
 */
function resolveApiBase(opts?: { direct?: boolean }): string {
  // 没有运行时后端地址时，大多数浏览器请求通过 Next.js rewrite 代理，保持同源。
  // SSE（Server-Sent Events）流仍需传入 direct=true，避免 Next.js rewrite 缓冲响应。
  if (typeof window !== "undefined") {
    const directOrigin = resolveDirectBackendOrigin();
    if (directOrigin) return `${directOrigin}${API_BASE_PATH}`;
    if (opts?.direct) return `${directOrigin}${API_BASE_PATH}`;
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

/**
 * 判断请求 URL 是否为跨域（与当前页面不同 origin）。
 * 跨域请求需要携带 credentials: "include" 以匹配后端 allow_credentials=True。
 */
function _isCrossOrigin(url: RequestInfo | URL): boolean {
  if (typeof window === "undefined") return false;
  try {
    const target = typeof url === "string" ? url : url instanceof URL ? url.href : (url as Request).url;
    if (!target || target.startsWith("/")) return false; // 相对路径 = 同源
    const u = new URL(target, window.location.origin);
    return u.origin !== window.location.origin;
  } catch {
    return false;
  }
}

/**
 * 为跨域 URL 自动追加 credentials: "include" 到 RequestInit。
 * 同源 URL 不做任何修改，保持浏览器默认行为（same-origin）。
 */
function _withCredentials(url: string, init: RequestInit): RequestInit {
  if (_isCrossOrigin(url)) {
    return { ...init, credentials: init.credentials ?? "include" };
  }
  return init;
}

/** 判断是否为可重试的暂态错误（网络异常或 502/503/504）。 */
function _isTransientError(err: unknown, res?: Response | null): boolean {
  if (err instanceof TypeError) return true; // "Failed to fetch" — 网络不可达
  if (res && (res.status === 502 || res.status === 503 || res.status === 504)) return true;
  return false;
}

/**
 * 直连后端的 fetch 包装。
 * - 跨域时携带 credentials；Authorization 由调用方通过 getAuthHeaders 注入。
 * - 遇到暂态网络错误或 502/503/504 时自动重试一次（间隔 1 秒）。
 */
export async function directFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const doFetch = async () => {
    const headers = new Headers(init?.headers);
    const signal = init?.signal ?? _withTimeout(_DEFAULT_TIMEOUT_MS);
    const credentials: RequestCredentials | undefined = _isCrossOrigin(input) ? "include" : undefined;
    return fetch(input, { ...init, headers, signal, credentials: init?.credentials ?? credentials });
  };

  let res: Response;
  try {
    res = await doFetch();
  } catch (err) {
    // 暂态网络错误 → 延迟 1s 重试一次
    if (_isTransientError(err)) {
      await new Promise((r) => setTimeout(r, 1000));
      res = await doFetch(); // 重试失败则抛出，由调用方处理
    } else {
      throw err;
    }
  }

  // 502/503/504 暂态服务端错误 → 重试一次
  if (_isTransientError(null, res)) {
    await new Promise((r) => setTimeout(r, 1000));
    res = await doFetch();
  }

  return res;
}

async function handleAuthError(res: Response): Promise<never> {
  const data = await res.json().catch(() => ({}));
  throw new Error(formatApiErrorMessage(data, res.status));
}

export async function apiGet<T = unknown>(
  path: string,
  opts?: { direct?: boolean; signal?: AbortSignal; timeoutMs?: number },
): Promise<T> {
  const url = buildApiUrl(path, opts);
  const res = await fetch(url, _withCredentials(url, {
    headers: { ...getAuthHeaders() },
    signal: _withTimeout(opts?.timeoutMs ?? _DEFAULT_TIMEOUT_MS, opts?.signal),
  }));
  if (!res.ok) return handleAuthError(res);
  return res.json();
}

export async function apiPost<T = unknown>(
  path: string,
  body: unknown,
  opts?: { direct?: boolean; timeoutMs?: number },
): Promise<T> {
  const url = buildApiUrl(path, opts);
  const res = await fetch(url, _withCredentials(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(body),
    signal: _withTimeout(opts?.timeoutMs ?? _DEFAULT_TIMEOUT_MS),
  }));
  if (!res.ok) return handleAuthError(res);
  return res.json();
}

export async function apiPut<T = unknown>(
  path: string,
  body: unknown,
  opts?: { direct?: boolean },
): Promise<T> {
  const url = buildApiUrl(path, opts);
  const res = await fetch(url, _withCredentials(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(body),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  }));
  if (!res.ok) return handleAuthError(res);
  return res.json();
}

export async function apiPatch<T = unknown>(
  path: string,
  body: unknown,
  opts?: { direct?: boolean },
): Promise<T> {
  const url = buildApiUrl(path, opts);
  const res = await fetch(url, _withCredentials(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(body),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  }));
  if (!res.ok) return handleAuthError(res);
  return res.json();
}

export async function apiDelete<T = void>(path: string, opts?: { direct?: boolean }): Promise<T> {
  const url = buildApiUrl(path, opts);
  const res = await fetch(url, _withCredentials(url, {
    method: "DELETE",
    headers: { ...getAuthHeaders() },
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  }));
  if (!res.ok) return handleAuthError(res);
  return await res.json().catch(() => undefined) as T;
}

export async function fetchSessions(): Promise<unknown[]> {
  const res: { sessions?: unknown[] } = await apiGet("/sessions");
  return res.sessions ?? [];
}

export async function fetchSubagentRuns(sessionId: string): Promise<SubagentRun[]> {
  const data = await apiGet<{ runs: SubagentRun[] }>(
    `/sessions/${encodeURIComponent(sessionId)}/subagents`,
  );
  return data.runs;
}

export type SubagentControlAction = "send" | "pause" | "cancel" | "resume";

export async function cancelToolCall(sessionId: string, executionId: string): Promise<{ status: string }> {
  const data = await apiPost<{ call: { status: string } }>(
    `/sessions/${encodeURIComponent(sessionId)}/tool-calls/${encodeURIComponent(executionId)}/cancel`,
    {},
  );
  return data.call;
}

export async function controlSubagentRun(
  sessionId: string,
  runId: string,
  action: SubagentControlAction,
  message = "",
): Promise<SubagentRun> {
  const data = await apiPost<{ run: SubagentRun }>(
    `/sessions/${encodeURIComponent(sessionId)}/subagents/${encodeURIComponent(runId)}`,
    { action, message },
    // 暂停/取消需要等待已开始的本地工具收尾；请求本身不自动重放。
    { timeoutMs: _UPLOAD_TIMEOUT_MS },
  );
  return data.run;
}

export async function createSession(opts?: {
  workspaceId?: string | null;
  workspacePath?: string | null;
  title?: string;
}): Promise<{
  id: string;
  title: string;
  message_count: number;
  in_flight: boolean;
  updated_at: string;
  workspace_path: string;
  workspace_id: string | null;
  workspace_title: string;
  blank: boolean;
}> {
  return apiPost("/sessions", {
    workspace_id: opts?.workspaceId || undefined,
    workspace_path: opts?.workspacePath || undefined,
    title: opts?.title || undefined,
  });
}

export async function fetchWorkspaces(): Promise<WorkspaceFolder[]> {
  const res: { workspaces?: WorkspaceFolder[] } = await apiGet("/workspaces");
  return res.workspaces ?? [];
}

export async function createWorkspaceFolder(path: string, title?: string): Promise<{
  workspace: WorkspaceFolder;
  created: boolean;
}> {
  return apiPost("/workspaces", { path, title: title || undefined });
}

export async function updateWorkspaceFolder(
  workspaceId: string,
  opts: { title?: string; path?: string },
): Promise<{ workspace: WorkspaceFolder }> {
  return apiPatch(`/workspaces/${encodeURIComponent(workspaceId)}`, {
    title: opts.title || undefined,
    path: opts.path || undefined,
  });
}

export async function deleteWorkspaceFolder(workspaceId: string): Promise<void> {
  await apiDelete(`/workspaces/${encodeURIComponent(workspaceId)}`);
}

export async function fetchSessionDetail(
  sessionId: string
): Promise<SessionDetail | null> {
  const res = await fetch(buildApiUrl(`/sessions/${encodeURIComponent(sessionId)}`), {
    headers: { ...getAuthHeaders() },
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (res.status === 404) {
    return null;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(body, res.status));
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
      queueSize: typeof pq.queue_size === "number" ? pq.queue_size : 1,
    };
  }

  return {
    id: (data.id as string) ?? sessionId,
    messageCount: (data.message_count as number) ?? 0,
    inFlight: (data.in_flight as boolean) ?? false,
    activeStreamId: (data.active_stream_id as string | null) ?? null,
    latestSeq: (data.latest_seq as number) ?? 0,
    fullAccessEnabled: (data.full_access_enabled as boolean) ?? false,
    chatMode: (data.chat_mode as "write" | "read" | "plan") ?? "write",
    currentModel: (data.current_model as string | null) ?? null,
    currentModelName: (data.current_model_name as string | null) ?? null,
    visionCapable: typeof data.vision_capable === "boolean" ? data.vision_capable : null,
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
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
  return res.json();
}

export async function updateSessionTitle(
  sessionId: string,
  title: string,
): Promise<{ status: string; title: string }> {
  return apiPatch(`/sessions/${encodeURIComponent(sessionId)}/title`, {
    title,
  });
}

export async function fetchSessionMessages(
  sessionId: string,
  limit = 50,
  offset = 0
): Promise<unknown[]> {
  const res: { messages?: unknown[] } = await apiGet(
    `/sessions/${encodeURIComponent(sessionId)}/messages?limit=${limit}&offset=${offset}`
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
      `/sessions/${encodeURIComponent(sessionId)}/excel-events`
    );
  } catch {
    return { diffs: [], previews: [], affected_files: [] };
  }
}

// ── Session Export ───────────────────────────────────

export type ExportFormat = "md" | "json";

function filenameFromDisposition(disposition: string, fallback: string): string {
  const star = disposition.match(/filename\*=(?:UTF-8''|utf-8'')([^;]+)/i);
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^"(.*)"$/, "$1"));
    } catch {
      // fall through to quoted filename
    }
  }
  const quoted = disposition.match(/filename="([^"]+)"/);
  if (quoted?.[1]) return quoted[1];
  const plain = disposition.match(/filename=([^;]+)/);
  if (plain?.[1]) return plain[1].trim().replace(/^"(.*)"$/, "$1");
  return fallback;
}

/**
 * 导出会话为 Markdown 或 JSON，触发浏览器下载。
 */
export async function exportSession(
  sessionId: string,
  format: ExportFormat = "md",
): Promise<void> {
  const params = new URLSearchParams({ format });
  const url = buildApiUrl(
    `/sessions/${encodeURIComponent(sessionId)}/export?${params.toString()}`,
  );
  const res = await fetch(url, { headers: { ...getAuthHeaders() }, signal: _withTimeout(_UPLOAD_TIMEOUT_MS) });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
  const blob = await res.blob();
  const filename = filenameFromDisposition(
    res.headers.get("Content-Disposition") || "",
    `session.${format}`,
  );

  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(a.href);
    a.remove();
  }, 100);
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

// ── Inline Interaction API (blocking ask_user / approval) ──

export async function answerQuestion(
  sessionId: string,
  questionId: string,
  answer: string,
): Promise<{ status: string; resume_required?: boolean }> {
  const url = buildApiUrl(`/chat/${encodeURIComponent(sessionId)}/answer`, { direct: true });
  const res = await fetch(url, _withCredentials(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ question_id: questionId, answer }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  }));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
  return res.json();
}

export async function submitApproval(
  sessionId: string,
  approvalId: string,
  decision: "accept" | "reject" | "fullaccess",
): Promise<{ status: string; resume_required?: boolean }> {
  const url = buildApiUrl(`/chat/${encodeURIComponent(sessionId)}/approve`, { direct: true });
  const res = await fetch(url, _withCredentials(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ approval_id: approvalId, decision }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  }));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
  return res.json();
}

export async function toggleFullAccess(
  sessionId: string,
  enabled: boolean,
): Promise<{ session_id: string; full_access_enabled: boolean }> {
  const url = buildApiUrl(`/sessions/${encodeURIComponent(sessionId)}/full-access`, { direct: true });
  const res = await fetch(url, _withCredentials(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ enabled }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  }));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
  return res.json();
}

export async function abortChat(sessionId: string): Promise<{ status: string }> {
  const url = buildApiUrl("/chat/abort", { direct: true });
  const res = await fetch(url, _withCredentials(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ session_id: sessionId }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  }));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
  return res.json();
}

export async function rollbackChat(opts: {
  sessionId: string;
  turnIndex: number;
  newMessage?: string;
  resendMode?: boolean;
}): Promise<{
  status: string;
  removed_messages: number;
  turn_index: number;
}> {
  const rollbackUrl = buildApiUrl("/chat/rollback", { direct: true });
  const res = await fetch(rollbackUrl, _withCredentials(rollbackUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({
      session_id: opts.sessionId,
      turn_index: opts.turnIndex,
      new_message: opts.newMessage ?? null,
      resend_mode: opts.resendMode ?? false,
    }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  }));
  if (!res.ok) return handleAuthError(res);
  return res.json();
}

// ── 回滚预览 API ─────────────────────────────────────────

export interface RollbackFileChange {
  path: string;
  change_type: "added" | "modified" | "deleted";
  before_size: number | null;
  after_size: number | null;
  is_binary: boolean;
  diff: string | null;
  tool_name: string;
}

export interface RollbackPreviewResult {
  turn_index: number;
  removed_messages: number;
  file_changes: RollbackFileChange[];
}

export async function rollbackPreview(
  sessionId: string,
  turnIndex: number,
): Promise<RollbackPreviewResult> {
  const previewUrl = buildApiUrl("/chat/rollback/preview", { direct: true });
  const res = await fetch(previewUrl, _withCredentials(previewUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ session_id: sessionId, turn_index: turnIndex }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  }));
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
  // 保留绝对路径不变，以便后端可根据工作区进行校验。
  if (p.startsWith("/")) return p;
  if (!p.startsWith("./")) p = `./${p}`;
  return p;
}

export interface WorkspaceRequestScope {
  sessionId?: string | null;
  workspaceId?: string | null;
}

function appendWorkspaceScope(params: URLSearchParams, scope?: WorkspaceRequestScope): void {
  if (scope?.sessionId) params.set("session_id", scope.sessionId);
  if (scope?.workspaceId) params.set("workspace_id", scope.workspaceId);
}

function buildExcelSnapshotUrl(
  path: string,
  opts?: { sheet?: string; maxRows?: number } & WorkspaceRequestScope,
): string {
  const params = new URLSearchParams({ path: normalizeExcelPath(path) });
  if (opts?.sheet) params.set("sheet", opts.sheet);
  if (opts?.maxRows) params.set("max_rows", String(opts.maxRows));
  appendWorkspaceScope(params, opts);
  return buildApiUrl(`/files/excel/snapshot?${params.toString()}`);
}

export function buildExcelFileUrl(path: string, sessionId?: string | null, workspaceId?: string | null): string {
  const params = new URLSearchParams({ path: normalizeExcelPath(path) });
  appendWorkspaceScope(params, { sessionId, workspaceId });
  return buildApiUrl(`/files/excel?${params.toString()}`);
}

// ── Word 预览 API ─────────────────────────────────────────

export function buildWordFileUrl(path: string, sessionId?: string | null, workspaceId?: string | null): string {
  const params = new URLSearchParams({ path: normalizeExcelPath(path) });
  appendWorkspaceScope(params, { sessionId, workspaceId });
  return buildApiUrl(`/files/word?${params.toString()}`);
}

function buildWordSnapshotUrl(
  path: string,
  opts?: { maxParagraphs?: number } & WorkspaceRequestScope,
): string {
  const params = new URLSearchParams({ path: normalizeExcelPath(path) });
  if (opts?.maxParagraphs) params.set("max_paragraphs", String(opts.maxParagraphs));
  appendWorkspaceScope(params, opts);
  return buildApiUrl(`/files/word/snapshot?${params.toString()}`);
}

export interface WordSnapshotResponse {
  file: string;
  content_version?: string;
  total_paragraphs: number;
  returned_paragraphs: number;
  truncated: boolean;
  paragraphs: {
    text: string;
    style: string;
    heading_level?: number;
    alignment?: string;
    runs?: {
      text: string;
      bold?: boolean;
      italic?: boolean;
      underline?: boolean;
      size_pt?: number;
      font_name?: string;
      color?: string;
    }[];
  }[];
  tables: {
    index: number;
    rows: number;
    columns: number;
    data: string[][];
  }[];
  total_tables: number;
  sections: number;
  properties: { title?: string; author?: string };
}

export async function fetchWordSnapshot(
  path: string,
  opts?: { maxParagraphs?: number } & WorkspaceRequestScope,
): Promise<WordSnapshotResponse> {
  const url = buildWordSnapshotUrl(path, opts);
  const res = await fetch(url, _withCredentials(url, {
    headers: { ...getAuthHeaders() },
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  }));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
  return res.json();
}

export async function writeWordContent(
  path: string,
  operations: { action: string; index?: number; text?: string; style?: string }[],
  opts?: WorkspaceRequestScope & { expectedVersion: string },
): Promise<{ status: string; applied_count: number; errors?: string[]; content_version?: string }> {
  const expectedVersion = opts?.expectedVersion;
  if (!expectedVersion) {
    throw new Error("Word write 必须提供 expected_version");
  }
  const url = buildApiUrl("/files/word/write");
  const res = await fetch(url, _withCredentials(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({
      path: normalizeExcelPath(path),
      operations,
      session_id: opts?.sessionId,
      workspace_id: opts?.workspaceId,
      expected_version: expectedVersion,
    }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  }));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
  return res.json();
}

export interface ExcelFileListItem {
  path: string;
  filename: string;
  modified_at: number;
  is_dir?: boolean;
}

export async function fetchExcelFiles(sessionId?: string | null, workspaceId?: string | null): Promise<ExcelFileListItem[]> {
  const params = new URLSearchParams();
  appendWorkspaceScope(params, { sessionId, workspaceId });
  const qs = params.toString();
  const url = buildApiUrl(`/files/excel/list${qs ? `?${qs}` : ""}`);
  const res = await fetch(url, { headers: { ...getAuthHeaders() }, signal: _withTimeout(_DEFAULT_TIMEOUT_MS) });
  if (!res.ok) return [];
  const data = await res.json();
  return data.files ?? [];
}

export interface WorkspaceFileList {
  files: ExcelFileListItem[];
  /** 后端按上限截断时为 true：files 不是完整清单，不可用于存在性校验 */
  truncated: boolean;
}

export async function fetchWorkspaceFiles(sessionId?: string | null, workspaceId?: string | null): Promise<WorkspaceFileList> {
  const params = new URLSearchParams();
  appendWorkspaceScope(params, { sessionId, workspaceId });
  const qs = params.toString();
  const url = buildApiUrl(`/files/workspace/list${qs ? `?${qs}` : ""}`);
  const res = await fetch(url, { headers: { ...getAuthHeaders() }, signal: _withTimeout(_DEFAULT_TIMEOUT_MS) });
  if (!res.ok) throw new Error(`工作区文件列表加载失败: ${res.status}`);
  const data = await res.json();
  return { files: data.files ?? [], truncated: !!data.truncated };
}

// ── FileRegistry API ─────────────────────────────────────

export interface FileRegistryEntry {
  id: string;
  workspace: string;
  canonical_path: string;
  original_name: string;
  file_type: string;
  size_bytes: number;
  origin: string;
  origin_session_id: string | null;
  origin_turn: number | null;
  origin_tool: string | null;
  parent_file_id: string | null;
  sheet_meta: Record<string, unknown>[];
  content_hash: string;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  events?: FileRegistryEvent[];
  children?: FileRegistryEntry[];
  lineage?: FileRegistryEntry[];
}

export interface FileRegistryEvent {
  id: string;
  file_id: string;
  event_type: string;
  session_id: string | null;
  turn: number | null;
  tool_name: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export async function fetchFileRegistry(opts?: {
  includeDeleted?: boolean;
  includeEvents?: boolean;
  fileId?: string;
  sessionId?: string | null;
  workspaceId?: string | null;
}): Promise<{ files: FileRegistryEntry[]; total: number } | { file: FileRegistryEntry }> {
  const params = new URLSearchParams();
  if (opts?.includeDeleted) params.set("include_deleted", "true");
  if (opts?.includeEvents) params.set("include_events", "true");
  if (opts?.fileId) params.set("file_id", opts.fileId);
  appendWorkspaceScope(params, opts);
  const qs = params.toString();
  return apiGet(`/files/registry${qs ? `?${qs}` : ""}`);
}

// ── File Groups API ──────────────────────────────────────

export interface FileGroupMember {
  file_id: string;
  canonical_path: string;
  original_name: string;
  file_type: string;
  role: string;
  added_at: string;
}

export interface FileGroup {
  id: string;
  workspace: string;
  name: string;
  description: string;
  members: FileGroupMember[];
  created_at: string;
  updated_at: string;
}

export async function fetchFileGroups(sessionId?: string | null): Promise<{ groups: FileGroup[] }> {
  try {
    const params = new URLSearchParams();
    if (sessionId) params.set("session_id", sessionId);
    const qs = params.toString();
    return await apiGet<{ groups: FileGroup[] }>(`/files/groups${qs ? `?${qs}` : ""}`);
  } catch {
    return { groups: [] };
  }
}

export async function createFileGroup(opts: {
  name: string;
  description?: string;
  file_ids?: { id: string; role?: string }[];
  sessionId?: string | null;
}): Promise<FileGroup> {
  return apiPost<FileGroup>("/files/groups", {
    name: opts.name,
    description: opts.description,
    file_ids: opts.file_ids,
    session_id: opts.sessionId || undefined,
  });
}

export async function updateFileGroup(
  groupId: string,
  opts: { name?: string; description?: string; sessionId?: string | null },
): Promise<FileGroup> {
  const params = new URLSearchParams();
  if (opts.sessionId) params.set("session_id", opts.sessionId);
  const qs = params.toString();
  return apiPut<FileGroup>(
    `/files/groups/${encodeURIComponent(groupId)}${qs ? `?${qs}` : ""}`,
    { name: opts.name, description: opts.description },
  );
}

export async function deleteFileGroup(groupId: string, sessionId?: string | null): Promise<void> {
  const params = new URLSearchParams();
  if (sessionId) params.set("session_id", sessionId);
  const qs = params.toString();
  await apiDelete(`/files/groups/${encodeURIComponent(groupId)}${qs ? `?${qs}` : ""}`);
}

export async function updateFileGroupMembers(
  groupId: string,
  opts: { add?: { file_id: string; role?: string }[]; remove?: string[]; sessionId?: string | null },
): Promise<FileGroup> {
  const params = new URLSearchParams();
  if (opts.sessionId) params.set("session_id", opts.sessionId);
  const qs = params.toString();
  return apiPut<FileGroup>(
    `/files/groups/${encodeURIComponent(groupId)}/members${qs ? `?${qs}` : ""}`,
    { add: opts.add, remove: opts.remove },
  );
}

// ── Cross-file Compare APIs ──────────────────────────────

export interface SharedColumnAPI {
  col_a: string;
  col_b: string;
  match_type: "exact" | "normalized" | "value_overlap";
  overlap_ratio: number;
}

export interface CompareResponse {
  file_a: AllSheetsSnapshotResponse;
  file_b: AllSheetsSnapshotResponse;
  relationships: {
    shared_columns: SharedColumnAPI[];
    merge_hint?: { file_a: string; file_b: string; key_column_a: string; key_column_b: string; suggested_join: string };
  };
}

export async function fetchExcelCompare(
  pathA: string,
  pathB: string,
  opts?: WorkspaceRequestScope & { maxRows?: number },
): Promise<CompareResponse> {
  const params = new URLSearchParams({
    path_a: normalizeExcelPath(pathA),
    path_b: normalizeExcelPath(pathB),
  });
  appendWorkspaceScope(params, opts);
  if (opts?.maxRows) params.set("max_rows", String(opts.maxRows));
  const url = buildApiUrl(`/files/excel/compare?${params.toString()}`);
  const res = await fetch(url, {
    headers: { ...getAuthHeaders() },
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
  return res.json();
}

// ── Workspace file management APIs ───────────────────────

export async function workspaceMkdir(path: string, sessionId?: string | null, workspaceId?: string | null): Promise<void> {
  const url = buildApiUrl("/files/workspace/mkdir");
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ path, session_id: sessionId || undefined, workspace_id: workspaceId || undefined }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
}

export async function workspaceCreateFile(path: string, sessionId?: string | null, workspaceId?: string | null): Promise<void> {
  const url = buildApiUrl("/files/workspace/create");
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ path, session_id: sessionId || undefined, workspace_id: workspaceId || undefined }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
}

export async function workspaceDeleteItem(path: string, sessionId?: string | null, workspaceId?: string | null): Promise<void> {
  const url = buildApiUrl("/files/workspace/item");
  const res = await fetch(url, {
    method: "DELETE",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ path, session_id: sessionId || undefined, workspace_id: workspaceId || undefined }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
}

export async function workspaceRenameItem(oldPath: string, newPath: string, sessionId?: string | null, workspaceId?: string | null): Promise<void> {
  const url = buildApiUrl("/files/workspace/rename");
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ old_path: oldPath, new_path: newPath, session_id: sessionId || undefined, workspace_id: workspaceId || undefined }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
}

export async function uploadFileToFolder(
  file: File,
  folder: string,
  sessionId?: string | null,
  workspaceId?: string | null,
): Promise<{ filename: string; path: string; size: number }> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("folder", folder);
  if (sessionId) formData.append("session_id", sessionId);
  if (workspaceId) formData.append("workspace_id", workspaceId);
  const res = await fetch(buildApiUrl("/upload"), {
    method: "POST",
    headers: { ...getAuthHeaders() },
    body: formData,
    signal: _withTimeout(_UPLOAD_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
  return res.json();
}

export interface AllSheetsSnapshotResponse {
  file: string;
  sheets: string[];
  all_snapshots: ExcelSnapshot[];
  content_version?: string;
}

// ── Snapshot 缓存（TTL 30s，避免重复请求同一文件） ──
const _snapshotCache = new Map<string, { data: AllSheetsSnapshotResponse; ts: number }>();
const _snapshotInflight = new Map<string, Promise<AllSheetsSnapshotResponse>>();
const _SNAPSHOT_TTL_MS = 30_000;

export function fileCachePrefix(workspaceKey: string, relative: string): string {
  return `${workspaceKey}|${normalizeExcelPath(relative)}`;
}

export function matchesFileCacheKey(
  key: string,
  opts?: { workspaceKey?: string; relative?: string },
): boolean {
  if (!opts?.workspaceKey && !opts?.relative) return true;
  const parts = key.split("|");
  const keyWs = parts[0] ?? "";
  const keyPath = parts[1] ?? "";
  if (opts.workspaceKey && keyWs !== opts.workspaceKey) return false;
  if (opts.relative && keyPath !== normalizeExcelPath(opts.relative)) return false;
  return true;
}

function dropCacheKeys(
  maps: Array<Map<string, unknown>>,
  opts?: { workspaceKey?: string; relative?: string },
): void {
  if (!opts?.workspaceKey && !opts?.relative) {
    for (const map of maps) map.clear();
    return;
  }
  for (const map of maps) {
    for (const key of [...map.keys()]) {
      if (matchesFileCacheKey(key, opts)) map.delete(key);
    }
  }
}

export function snapshotCacheKey(
  path: string,
  opts?: { maxRows?: number; withStyles?: boolean; workspaceKey?: string } & WorkspaceRequestScope,
): string {
  return [
    fileCachePrefix(opts?.workspaceKey || "_", path),
    opts?.maxRows ?? "",
    opts?.withStyles !== false ? "1" : "0",
  ].join("|");
}

/** 使指定文件的 snapshot 缓存失效（文件变更后调用） */
export function invalidateSnapshotCache(opts?: { workspaceKey?: string; relative?: string }) {
  dropCacheKeys(
    [_snapshotCache as Map<string, unknown>, _snapshotInflight as Map<string, unknown>],
    opts,
  );
}

/** @deprecated 编辑器请用 prefetchWorkbookView */
export function prefetchExcelSnapshot(
  path: string,
  opts?: { maxRows?: number; withStyles?: boolean; workspaceKey?: string } & WorkspaceRequestScope,
) {
  if (!path || (!opts?.sessionId && !opts?.workspaceId)) return;
  void fetchAllSheetsSnapshot(path, {
    maxRows: opts?.maxRows ?? 500,
    withStyles: opts?.withStyles !== false,
    sessionId: opts.sessionId,
    workspaceId: opts.workspaceId,
    workspaceKey: opts.workspaceKey,
  }).catch(() => null);
}

export async function fetchAllSheetsSnapshot(
  path: string,
  opts?: { maxRows?: number; withStyles?: boolean; workspaceKey?: string } & WorkspaceRequestScope,
): Promise<AllSheetsSnapshotResponse> {
  const cacheKey = snapshotCacheKey(path, opts);
  const cached = _snapshotCache.get(cacheKey);
  if (cached && Date.now() - cached.ts < _SNAPSHOT_TTL_MS) {
    return cached.data;
  }

  const inflight = _snapshotInflight.get(cacheKey);
  if (inflight) return inflight;

  const pending = (async () => {
    const params = new URLSearchParams({ path: normalizeExcelPath(path), all_sheets: "1" });
    if (opts?.maxRows) params.set("max_rows", String(opts.maxRows));
    appendWorkspaceScope(params, opts);
    params.set("with_styles", opts?.withStyles !== false ? "1" : "0");
    const url = buildApiUrl(`/files/excel/snapshot?${params.toString()}`);
    const res = await fetch(url, { headers: { ...getAuthHeaders() }, signal: _withTimeout(_DEFAULT_TIMEOUT_MS) });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(formatApiErrorMessage(data, res.status));
    }
    const result: AllSheetsSnapshotResponse = await res.json();
    _snapshotCache.set(cacheKey, { data: result, ts: Date.now() });
    return result;
  })();

  _snapshotInflight.set(cacheKey, pending);
  try {
    return await pending;
  } finally {
    if (_snapshotInflight.get(cacheKey) === pending) {
      _snapshotInflight.delete(cacheKey);
    }
  }
}

export async function fetchExcelSnapshot(
  path: string,
  opts?: { sheet?: string; maxRows?: number } & WorkspaceRequestScope,
): Promise<ExcelSnapshot> {
  const res = await fetch(buildExcelSnapshotUrl(path, opts), {
    headers: { ...getAuthHeaders() },
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
  return res.json();
}

export type WorkbookViewResponse = import("@/lib/workbook-view").WorkbookViewSnapshot;

const _viewCache = new Map<string, { data: WorkbookViewResponse; ts: number }>();
type ViewFlight = {
  promise: Promise<WorkbookViewResponse>;
  controller: AbortController;
  readers: Set<symbol>;
};
const _viewInflight = new Map<string, ViewFlight>();
const _VIEW_TTL_MS = 30_000;
const _VIEW_OPEN_TTL_MS = 5_000;
const _VIEW_CACHE_LIMIT = 64;

function cacheView(key: string, data: WorkbookViewResponse) {
  _viewCache.delete(key);
  _viewCache.set(key, { data, ts: Date.now() });
  while (_viewCache.size > _VIEW_CACHE_LIMIT) _viewCache.delete(_viewCache.keys().next().value!);
}

function readViewFlight(flight: ViewFlight, signal?: AbortSignal): Promise<WorkbookViewResponse> {
  const reader = Symbol();
  flight.readers.add(reader);
  return new Promise((resolve, reject) => {
    const release = () => {
      signal?.removeEventListener("abort", abort);
      flight.readers.delete(reader);
      if (!flight.readers.size) flight.controller.abort();
    };
    const abort = () => { release(); reject(new DOMException("视图请求已取消", "AbortError")); };
    if (signal?.aborted) { abort(); return; }
    signal?.addEventListener("abort", abort, { once: true });
    flight.promise.then(resolve, reject).finally(release);
  });
}

export function viewCacheKey(opts: {
  workspaceKey: string;
  relative: string;
  version?: string;
  sheet?: string;
  rect?: string;
  withStyles?: boolean;
}): string {
  return [
    fileCachePrefix(opts.workspaceKey, opts.relative),
    opts.version || "unknown",
    opts.sheet || "*",
    opts.rect || "A1:AX200",
    opts.withStyles !== false ? "1" : "0",
  ].join("|");
}

export function invalidateWorkbookViewCache(opts?: {
  workspaceKey?: string;
  relative?: string;
}): void {
  dropCacheKeys([_viewCache as Map<string, unknown>], opts);
  for (const [key, flight] of _viewInflight) {
    if (!matchesFileCacheKey(key, opts)) continue;
    _viewInflight.delete(key);
    flight.controller.abort();
  }
}

/** 写入/恢复后同时清 snapshot 与 view，键空间与读取一致。 */
export function invalidateWorkbookCaches(opts?: {
  workspaceKey?: string;
  relative?: string;
}): void {
  invalidateSnapshotCache(opts);
  invalidateWorkbookViewCache(opts);
}

export async function fetchWorkbookView(opts: {
  path: string;
  workspaceKey: string;
  sessionId?: string;
  workspaceId?: string | null;
  sheet?: string;
  rect?: string;
  withStyles?: boolean;
  expectedVersion?: string;
  viewGeneration?: number;
  signal?: AbortSignal;
}): Promise<WorkbookViewResponse> {
  opts.signal?.throwIfAborted();
  if (!opts.sessionId && !opts.workspaceId) {
    throw new Error("无法确定工作区，请从会话重新打开文件");
  }
  const cacheKey = viewCacheKey({
    workspaceKey: opts.workspaceKey,
    relative: opts.path,
    version: opts.expectedVersion,
    sheet: opts.sheet,
    rect: opts.rect,
    withStyles: opts.withStyles,
  });
  // A response without a requested version is a short-lived open hint. All
  // returned windows are also stored under their actual immutable version.
  const inflightKey = cacheKey;
  const cached = _viewCache.get(cacheKey);
  if (cached && Date.now() - cached.ts < (opts.expectedVersion ? _VIEW_TTL_MS : _VIEW_OPEN_TTL_MS)) {
    return cached.data;
  }
  const inflight = _viewInflight.get(inflightKey);
  if (inflight && !inflight.controller.signal.aborted) return readViewFlight(inflight, opts.signal);

  const controller = new AbortController();
  const flight: ViewFlight = { controller, readers: new Set(), promise: null! };
  flight.promise = (async () => {
    const params = new URLSearchParams({ path: normalizeExcelPath(opts.path) });
    if (opts.sessionId) params.set("session_id", opts.sessionId);
    if (opts.workspaceId) params.set("workspace_id", opts.workspaceId);
    if (opts.sheet) params.set("sheet", opts.sheet);
    if (opts.rect) params.set("rect", opts.rect);
    params.set("with_styles", opts.withStyles !== false ? "1" : "0");
    if (opts.expectedVersion) params.set("expected_version", opts.expectedVersion);
    const res = await fetch(buildApiUrl(`/files/excel/view?${params.toString()}`), {
      headers: { ...getAuthHeaders() },
      signal: _withTimeout(_DEFAULT_TIMEOUT_MS, controller.signal),
    });
    const data = await res.json().catch(() => ({} as Record<string, unknown>));
    if (_viewInflight.get(inflightKey) !== flight || controller.signal.aborted) {
      throw new DOMException("视图请求已失效", "AbortError");
    }
    if (res.status === 409) {
      const err = new Error((data.error as string) || "STALE_VIEW");
      (err as Error & { code?: string }).code = String(data.code || "STALE_VIEW");
      throw err;
    }
    if (!res.ok) {
      const err = new Error(
        (typeof data.error === "string" && data.error) || `View error: ${res.status}`,
      ) as Error & { status?: number; code?: string };
      err.status = res.status;
      if (typeof data.code === "string") err.code = data.code;
      if (res.status === 404 && (!data.code || data.code === "PATH_INVALID")) {
        // 后端确认文件不存在：剔除该工作区桶里的陈旧 recentFiles 条目，
        // 否则侧栏/工作表面板会持续指向已删除路径反复 404。
        void import("@/stores/excel-store")
          .then(({ useExcelStore }) => {
            useExcelStore.getState().evictRecentFile(opts.path, opts.workspaceKey);
          })
          .catch(() => {});
      }
      throw err;
    }
    const result = data as WorkbookViewResponse;
    if (!result.content_version || result.file?.workspaceKey !== opts.workspaceKey
      || normalizeExcelPath(result.file.relative) !== normalizeExcelPath(opts.path)
      || (opts.expectedVersion && opts.expectedVersion !== result.content_version)) {
      throw new Error("STALE_VIEW: 响应文件或版本不匹配");
    }
    cacheView(cacheKey, result);
    cacheView(viewCacheKey({ workspaceKey: opts.workspaceKey, relative: opts.path,
      version: result.content_version, sheet: opts.sheet, rect: opts.rect, withStyles: opts.withStyles }), result);
    // An implicit active-sheet request can be reused by an explicit tab request.
    if (!opts.sheet && result.windows.length === 1) cacheView(viewCacheKey({
      workspaceKey: opts.workspaceKey, relative: opts.path, version: result.content_version,
      sheet: result.windows[0].sheet, rect: opts.rect, withStyles: opts.withStyles,
    }), result);
    return result;
  })().finally(() => {
    if (_viewInflight.get(inflightKey) === flight) {
      _viewInflight.delete(inflightKey);
    }
  });
  _viewInflight.set(inflightKey, flight);
  return readViewFlight(flight, opts.signal);
}

export function prefetchWorkbookView(opts: {
  path: string;
  workspaceKey: string;
  sessionId?: string;
  workspaceId?: string | null;
  sheet?: string;
  signal?: AbortSignal;
}): void {
  if (!opts.path || (!opts.sessionId && !opts.workspaceId)) return;
  void fetchWorkbookView({ ...opts, withStyles: false }).catch(() => null);
}

export interface ExcelWriteResponse {
  status: string;
  cells_written: number;
  content_version?: string;
  code?: string;
  operation_id?: string;
  state?: string;
}

export async function writeExcelCells(opts: {
  path: string;
  sheet?: string;
  changes?: { cell: string; value: unknown; sheet?: string; style?: unknown }[];
  operations?: Record<string, unknown>[];
  sessionId?: string;
  workspaceId?: string | null;
  expectedVersion?: string | null;
  operationId?: string;
}): Promise<ExcelWriteResponse> {
  const url = buildApiUrl("/files/excel/write");
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({
      path: normalizeExcelPath(opts.path),
      sheet: opts.sheet ?? null,
      changes: opts.changes ?? [],
      operations: opts.operations ?? null,
      session_id: opts.sessionId ?? null,
      workspace_id: opts.workspaceId ?? null,
      expected_version: opts.expectedVersion ?? null,
      operation_id: opts.operationId ?? null,
    }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  const data = await res.json().catch(() => ({} as Record<string, unknown>));
  if (res.status === 409) {
    return {
      status: "conflict",
      cells_written: 0,
      code: typeof data.code === "string" ? data.code : "VERSION_CONFLICT",
      content_version: typeof data.content_version === "string" ? data.content_version : undefined,
    };
  }
  if (!res.ok) {
    throw new Error(
      (typeof data.error === "string" && data.error) || `Write error: ${res.status}`,
    );
  }
  return {
    status: typeof data.status === "string" ? data.status : "success",
    cells_written: typeof data.cells_written === "number" ? data.cells_written : 0,
    content_version: typeof data.content_version === "string" ? data.content_version : undefined,
    code: typeof data.code === "string" ? data.code : undefined,
  };
}

/**
 * 从工作区下载文件并返回 Blob（不触发浏览器下载）。
 * 用于重试时重新获取图片内容以编码 base64。
 */
export async function fetchFileBlob(path: string, sessionId?: string | null, workspaceId?: string | null): Promise<Blob> {
  const params = new URLSearchParams({ path: normalizeExcelPath(path) });
  appendWorkspaceScope(params, { sessionId, workspaceId });
  const url = buildApiUrl(`/files/download?${params.toString()}`, { direct: true });
  const res = await fetch(url, _withCredentials(url, {
    headers: { ...getAuthHeaders() },
    signal: _withTimeout(_UPLOAD_TIMEOUT_MS),
  }));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
  return res.blob();
}

// 下载冷却时间配置（毫秒）—— 按路径独立冷却，不阻塞不同附件的并发下载
const DOWNLOAD_COOLDOWN_MS = 1000;
const _downloadCooldowns = new Map<string, number>();

export async function downloadFile(
  path: string,
  filename?: string,
  sessionId?: string | null,
  workspaceId?: string | null,
): Promise<void> {
  const now = Date.now();
  const cooldownKey = `${workspaceId || sessionId || "_"}|${normalizeExcelPath(path)}`;
  const lastTime = _downloadCooldowns.get(cooldownKey) ?? 0;
  if (now - lastTime < DOWNLOAD_COOLDOWN_MS) {
    // 同一文件冷却中，忽略本次请求
    return;
  }
  _downloadCooldowns.set(cooldownKey, now);
  const params = new URLSearchParams({ path: normalizeExcelPath(path) });
  appendWorkspaceScope(params, { sessionId, workspaceId });
  const url = buildApiUrl(`/files/download?${params.toString()}`, { direct: true });
  const res = await fetch(url, _withCredentials(url, {
    headers: { ...getAuthHeaders() },
    signal: _withTimeout(_UPLOAD_TIMEOUT_MS),
  }));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
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

export async function uploadFile(file: File, sessionId?: string | null, workspaceId?: string | null): Promise<{
  filename: string;
  path: string;
  size: number;
}> {
  const formData = new FormData();
  formData.append("file", file);
  if (sessionId) formData.append("session_id", sessionId);
  if (workspaceId) formData.append("workspace_id", workspaceId);
  const res = await fetch(buildApiUrl("/upload"), {
    method: "POST",
    headers: { ...getAuthHeaders() },
    body: formData,
    signal: _withTimeout(_UPLOAD_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
  return res.json();
}

export async function uploadFileFromUrl(
  url: string,
  sessionId?: string | null,
  workspaceId?: string | null,
): Promise<{
  filename: string;
  path: string;
  size: number;
}> {
  const res = await fetch(buildApiUrl("/upload-from-url"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({
      url,
      session_id: sessionId || undefined,
      workspace_id: workspaceId || undefined,
    }),
    signal: _withTimeout(_UPLOAD_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiErrorMessage(data, res.status));
  }
  return res.json();
}

// ── 工作区事务 API（原备份应用）────

// ── 操作历史时间线 API ────────────────────────────────────

export interface OperationChange {
  path: string;
  change_type: "added" | "modified" | "deleted";
  before_size: number | null;
  after_size: number | null;
  is_binary: boolean;
}

export interface OperationRecord {
  approval_id: string;
  tool_name: string;
  arguments_summary: Record<string, string>;
  session_turn: number | null;
  created_at_utc: string;
  applied_at_utc: string;
  execution_status: "success" | "failed";
  undoable: boolean;
  result_preview: string;
  changes: OperationChange[];
}

export interface OperationDetail extends OperationRecord {
  arguments: Record<string, unknown>;
  patch_content: string | null;
  error_type: string | null;
  error_message: string | null;
}

export interface OperationsListResponse {
  operations: OperationRecord[];
  total: number;
  has_more: boolean;
}

export async function fetchOperations(
  sessionId: string,
  opts?: { limit?: number; offset?: number },
): Promise<OperationsListResponse> {
  const params = new URLSearchParams();
  if (opts?.limit != null) params.set("limit", String(opts.limit));
  if (opts?.offset != null) params.set("offset", String(opts.offset));
  const qs = params.toString() ? `?${params}` : "";
  const url = buildApiUrl(`/sessions/${encodeURIComponent(sessionId)}/operations${qs}`);
  const res = await fetch(url, { headers: getAuthHeaders(), signal: _withTimeout(_DEFAULT_TIMEOUT_MS) });
  if (!res.ok) await handleAuthError(res);
  return res.json();
}

export async function fetchOperationDetail(
  sessionId: string,
  approvalId: string,
): Promise<OperationDetail> {
  const url = buildApiUrl(
    `/sessions/${encodeURIComponent(sessionId)}/operations/${encodeURIComponent(approvalId)}`,
  );
  const res = await fetch(url, { headers: getAuthHeaders(), signal: _withTimeout(_DEFAULT_TIMEOUT_MS) });
  if (!res.ok) await handleAuthError(res);
  return res.json();
}

export async function undoOperation(
  sessionId: string,
  approvalId: string,
): Promise<{ status: string; message: string; approval_id: string }> {
  const url = buildApiUrl(
    `/sessions/${encodeURIComponent(sessionId)}/operations/${encodeURIComponent(approvalId)}/undo`,
  );
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) await handleAuthError(res);
  return res.json();
}

export function buildBackupDownloadUrl(sessionId: string, filePath: string): string {
  return buildApiUrl(
    `/files/excel?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(filePath)}`
  );
}

// ── 模型检测 API ─────────────────────────────────────────

export interface TestConnectionResult {
  ok: boolean;
  error: string;
  model: string;
  base_url?: string;
  is_placeholder?: boolean;
  hint?: string;
}

export async function testModelConnection(opts: {
  name?: string;
  model?: string;
  base_url?: string;
  api_key?: string;
}): Promise<TestConnectionResult> {
  return apiPost<TestConnectionResult>("/config/models/test-connection", opts);
}

export interface RemoteModelItem {
  id: string;
  owned_by?: string;
}

export interface ListRemoteModelsResult {
  models: RemoteModelItem[];
  error?: string;
  hint?: string;
}

export async function listRemoteModels(opts: {
  name?: string;
  base_url?: string;
  api_key?: string;
  protocol?: string;
}): Promise<ListRemoteModelsResult> {
  return apiPost<ListRemoteModelsResult>("/config/models/list-remote", opts, { direct: true });
}

export interface PlaceholderCheckResult {
  has_placeholder: boolean;
  items: { name: string; field: string; model: string }[];
}

export async function checkModelPlaceholder(): Promise<PlaceholderCheckResult> {
  return apiGet<PlaceholderCheckResult>("/config/models/check-placeholder");
}

// ── Chat Turns API ───────────────────────────────────────

export interface ChatTurn {
  index: number;
  content_preview: string;
  msg_index: number;
}

export async function fetchChatTurns(
  sessionId: string,
): Promise<ChatTurn[]> {
  const res: { turns?: ChatTurn[] } = await apiGet(
    `/chat/turns?session_id=${encodeURIComponent(sessionId)}`,
  );
  return res.turns ?? [];
}

export interface WorkbookRevisionItem {
  revision_id: string;
  content_version: string;
  reason: string;
  sequence: number;
  transaction_id: string;
  label: string;
  parent_revision_id: string | null;
  created_at?: string;
}

export interface RevisionListResponse {
  path: string;
  content_version: string | null;
  revisions: WorkbookRevisionItem[];
}

export async function fetchRevisions(
  path: string,
  opts?: { sessionId?: string; workspaceId?: string | null; limit?: number; signal?: AbortSignal },
): Promise<RevisionListResponse> {
  const params = new URLSearchParams({ path });
  if (opts?.sessionId) params.set("session_id", opts.sessionId);
  if (opts?.workspaceId) params.set("workspace_id", opts.workspaceId);
  if (opts?.limit) params.set("limit", String(opts.limit));
  return apiGet<RevisionListResponse>(`/revisions?${params.toString()}`, { signal: opts?.signal });
}

export async function restoreRevision(opts: {
  path: string;
  revisionId: string;
  expectedVersion?: string | null;
  sessionId?: string | null;
  workspaceId?: string | null;
}): Promise<{ status: string; path: string; content_version: string; restored_revision: string }> {
  return apiPost("/revisions/restore", {
    path: opts.path,
    revision_id: opts.revisionId,
    ...(opts.expectedVersion ? { expected_version: opts.expectedVersion } : {}),
    session_id: opts.sessionId ?? null,
    workspace_id: opts.workspaceId ?? null,
  });
}

export async function deleteRevision(opts: {
  path: string;
  revisionId: string;
  sessionId?: string | null;
  workspaceId?: string | null;
}): Promise<{ status: string; path: string; deleted_revision: string }> {
  return apiPost("/revisions/delete", {
    path: opts.path,
    revision_id: opts.revisionId,
    session_id: opts.sessionId ?? null,
    workspace_id: opts.workspaceId ?? null,
  });
}

export async function fetchRevisionPreview(opts: {
  path: string;
  revisionId: string;
  sessionId?: string | null;
  workspaceId?: string | null;
  signal?: AbortSignal;
}): Promise<Partial<import("@/lib/workbook-view").WorkbookViewSnapshot> & {
  revision_id: string;
  revision_reason: string;
  revision_label: string;
  paragraphs?: { text?: string }[];
}> {
  const params = new URLSearchParams({ path: opts.path, revision_id: opts.revisionId });
  if (opts.sessionId) params.set("session_id", opts.sessionId);
  if (opts.workspaceId) params.set("workspace_id", opts.workspaceId);
  return apiGet(`/revisions/preview?${params.toString()}`, { signal: opts.signal });
}

// ── Version Advanced Operations ─────────────────────────

export async function cleanupVersionBackups(
  maxKeep = 2,
): Promise<{ status: string; removed_count: number; removed: string[] }> {
  return apiPost("/version/backups/cleanup", { max_keep: maxKeep });
}

export interface UpdateApplyResult {
  accepted?: boolean;
  success?: boolean;
  old_version?: string;
  new_version?: string;
  backup_dir?: string;
  steps_completed?: string[];
  error?: string | null;
  needs_restart?: boolean;
  message?: string;
}

export async function startVersionUpgrade(opts?: {
  skipBackup?: boolean;
  skipDeps?: boolean;
  useMirror?: boolean;
}): Promise<UpdateApplyResult> {
  return apiPost("/version/upgrade", {
    skip_backup: opts?.skipBackup ?? false,
    skip_deps: opts?.skipDeps ?? false,
    use_mirror: opts?.useMirror ?? false,
  });
}

export async function restoreVersionBackup(
  backupName: string,
): Promise<{ status: string; message: string }> {
  return apiPost("/version/backups/restore", { backup_name: backupName });
}

export async function migrateVersionData(
  source?: string,
): Promise<{ status: string; migrated: Record<string, unknown> }> {
  return apiPost("/version/data/migrate", { source: source ?? "" });
}

// ── Version Manifest ────────────────────────────────────

export interface VersionManifest {
  release_id: string;
  backend_version: string;
  api_schema_version: number;
  frontend_build_id: string | null;
  version_fingerprint?: string | null;
  git_commit: string | null;
  deployed_at: string | null;
  deploy_mode: string | null;
  topology: string | null;
  last_upgrade?: {
    ok?: boolean | null;
    action?: string;
    outcome?: string;
    error?: string | null;
    finished_at?: string;
    old_version?: string;
    new_version?: string;
  } | null;
}

export async function fetchVersionManifest(): Promise<VersionManifest> {
  return apiGet<VersionManifest>("/version/manifest");
}

// ── Remote Deploy Operations ────────────────────────────

export interface DeployStatusInfo {
  deploy_script_found: boolean;
  deploy_script_path: string | null;
  env_deploy_found: boolean;
  servers: Record<string, string>;
  site_urls: string[];
  version: string;
  artifacts: {
    name: string;
    path: string;
    size_mb: number;
    modified: string;
  }[];
  recent_history: string[];
  is_deploying: boolean;
  local_lock: { pid: string; started_at: string } | null;
}

export async function fetchDeployStatus(): Promise<DeployStatusInfo> {
  return apiGet<DeployStatusInfo>("/deploy/status");
}

export interface DeployResult {
  success: boolean;
  version: string;
  artifact_path: string;
  steps_completed: string[];
  deploy_output?: string;
  error: string | null;
}

export async function buildFrontendArtifact(): Promise<DeployResult> {
  return apiPost<DeployResult>("/deploy/build", {});
}

export async function executeRemoteDeploy(opts?: {
  target?: "full" | "backend" | "frontend";
  skipBuild?: boolean;
  artifactPath?: string;
  fromLocal?: boolean;
  skipDeps?: boolean;
}): Promise<DeployResult> {
  return apiPost<DeployResult>("/deploy/execute", {
    target: opts?.target ?? "full",
    skip_build: opts?.skipBuild ?? false,
    artifact_path: opts?.artifactPath ?? "",
    from_local: opts?.fromLocal ?? true,
    skip_deps: opts?.skipDeps ?? false,
  });
}

// ── Structured Deploy History ───────────────────────────

export interface DeployHistoryEntry {
  release_id: string;
  timestamp: string;
  status: string;
  topology: string;
  mode: string;
  branch: string;
  duration_s: number;
  git_commit: string;
  pre_deploy_commit: string;
}

export async function fetchDeployHistory(): Promise<{ history: DeployHistoryEntry[] }> {
  return apiGet<{ history: DeployHistoryEntry[] }>("/deploy/history");
}

// ── Remote Rollback ─────────────────────────────────────

export interface RollbackResult {
  success: boolean;
  output?: string;
  target?: string;
  release_id?: string;
  commit?: string;
  error?: string;
}

export async function executeRollback(opts: {
  target?: "full" | "backend" | "frontend";
  releaseId?: string;
  commit?: string;
  skipDeps?: boolean;
}): Promise<RollbackResult> {
  return apiPost<RollbackResult>("/deploy/rollback", {
    target: opts.target ?? "full",
    release_id: opts.releaseId ?? "",
    commit: opts.commit ?? "",
    skip_deps: opts.skipDeps ?? false,
  });
}



// ── Deploy Lock Status ──────────────────────────────────

export interface RemoteLockInfo {
  locked: boolean;
  holder_host: string;
  holder_user: string;
  holder_pid: string;
  locked_since: string;
  elapsed_s: number;
  expired: boolean;
  error: string | null;
}

export interface DeployLockStatus {
  local_locked: boolean;
  remote: RemoteLockInfo;
}

export async function fetchDeployLockStatus(): Promise<DeployLockStatus> {
  return apiGet<DeployLockStatus>("/deploy/lock/status");
}

// ── Deploy Log ──────────────────────────────────────────

export async function fetchDeployLog(releaseId: string): Promise<{ release_id: string; log: string }> {
  return apiGet<{ release_id: string; log: string }>(`/deploy/history/${encodeURIComponent(releaseId)}/log`);
}
