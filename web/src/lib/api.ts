import type { SessionDetail } from "@/lib/types";
import { useAuthStore } from "@/stores/auth-store";
import { resolveDirectBackendOrigin } from "@/lib/backend-origin";

const API_BASE_PATH = "/api/v1";

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

export function getAuthHeaders(): Record<string, string> {
  const token = useAuthStore.getState().accessToken;
  return token ? { Authorization: `Bearer ${token}` } : {};
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
  // 大多数浏览器请求通过 Next.js rewrite 代理，保持同源（避免局域网设备访问时的 CORS 问题）。
  //
  // 但 SSE（Server-Sent Events）流必须绕过代理，因为 Next.js rewrite 会缓冲整个
  // 响应后才转发给客户端，这会完全破坏实时流式传输。需要实时流的调用方
  // 传入 `direct: true` 直连后端。
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

/**
 * 将外部头像 URL 转换为后端代理 URL，解决浏览器直连被 GFW 屏蔽的问题。
 * 仅对已知外部域名（Google/GitHub/QQ）进行代理，其他 URL 原样返回。
 */
const _PROXY_AVATAR_DOMAINS = [
  "lh3.googleusercontent.com",
  "avatars.githubusercontent.com",
  "thirdqq.qlogo.cn",
  "q.qlogo.cn",
];

export function proxyAvatarUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const hostname = new URL(url).hostname;
    if (_PROXY_AVATAR_DOMAINS.includes(hostname)) {
      return `${API_BASE_PATH}/auth/avatar-proxy?url=${encodeURIComponent(url)}`;
    }
  } catch {
    // invalid URL, return as-is
  }
  return url;
}

/**
 * 解析头像 URL 为可直接用于 <img src> 的完整地址。
 * - 外部域名（Google/GitHub/QQ）→ 走代理
 * - 本地 /avatar-file 端点 → 追加 token query param（<img> 无法发送 Authorization header）
 * - 管理员查看其他用户头像 → 改写为 admin 端点
 */
export function resolveAvatarSrc(
  url: string | null | undefined,
  accessToken: string | null | undefined,
  opts?: { userId?: string; isAdmin?: boolean },
): string | null {
  if (!url) return null;

  // 管理员查看其他用户的本地头像：改写为 admin 端点
  if (opts?.isAdmin && opts?.userId && url.includes("/avatar-file")) {
    const base = `${API_BASE_PATH}/auth/admin/users/${opts.userId}/avatar-file`;
    return accessToken ? `${base}?token=${accessToken}` : base;
  }

  const base = proxyAvatarUrl(url);
  if (!base || !accessToken) return base;
  // 本地头像端点追加 token
  if (base.includes("/avatar-file")) {
    const sep = base.includes("?") ? "&" : "?";
    return `${base}${sep}token=${accessToken}`;
  }
  return base;
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
 * 带 auth token 刷新重试的 fetch 包装（用于直连调用）。
 * - 遇到 401 时自动刷新 token 并重试一次。
 * - 遇到暂态网络错误或 502/503/504 时自动重试一次（间隔 1 秒）。
 */
export async function directFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const doFetch = async () => {
    const headers = new Headers(init?.headers);
    const token = useAuthStore.getState().accessToken;
    if (token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    // 如果调用方未提供 signal，注入默认超时；SSE 调用方自带 AbortController 的 signal 不受影响
    const signal = init?.signal ?? _withTimeout(_DEFAULT_TIMEOUT_MS);
    // 跨域请求自动携带 credentials，确保 cookie 随请求发送（匹配后端 allow_credentials=True）
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

  if (res.status === 401) {
    const { refreshToken } = useAuthStore.getState();
    if (refreshToken) {
      const { refreshAccessToken } = await import("./auth-api");
      const ok = await refreshAccessToken();
      if (ok) {
        res = await doFetch();
      }
      // refreshAccessToken 内部已处理：认证错误→logout，网络错误→仅返回 false。
      // 此处不再重复 logout/redirect，避免网络波动踢出用户。
    } else {
      useAuthStore.getState().logout();
      if (typeof window !== "undefined") window.location.href = "/login";
    }
  }
  return res;
}

async function handleAuthError(res: Response): Promise<never> {
  if (res.status === 401) {
    const { refreshToken, logout } = useAuthStore.getState();
    if (refreshToken) {
      const { refreshAccessToken } = await import("./auth-api");
      await refreshAccessToken();
      // refreshAccessToken 内部已处理：认证错误→logout+清 cookie，网络错误→仅返回 false。
    } else {
      logout();
      if (typeof window !== "undefined") window.location.href = "/login";
    }
  }
  const data = await res.json().catch(() => ({}));
  throw new Error(data.error || data.detail || `API error: ${res.status}`);
}

export async function apiGet<T = unknown>(path: string, opts?: { direct?: boolean }): Promise<T> {
  const res = await fetch(buildApiUrl(path, opts), {
    headers: { ...getAuthHeaders() },
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) return handleAuthError(res);
  return res.json();
}

export async function apiPost<T = unknown>(
  path: string,
  body: unknown,
  opts?: { direct?: boolean },
): Promise<T> {
  const res = await fetch(buildApiUrl(path, opts), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(body),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) return handleAuthError(res);
  return res.json();
}

export async function apiPut<T = unknown>(
  path: string,
  body: unknown,
  opts?: { direct?: boolean },
): Promise<T> {
  const res = await fetch(buildApiUrl(path, opts), {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(body),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) return handleAuthError(res);
  return res.json();
}

export async function apiPatch<T = unknown>(
  path: string,
  body: unknown,
  opts?: { direct?: boolean },
): Promise<T> {
  const res = await fetch(buildApiUrl(path, opts), {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(body),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) return handleAuthError(res);
  return res.json();
}

export async function apiDelete(path: string, opts?: { direct?: boolean }): Promise<void> {
  const res = await fetch(buildApiUrl(path, opts), {
    method: "DELETE",
    headers: { ...getAuthHeaders() },
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
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
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
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
    activeStreamId: (data.active_stream_id as string | null) ?? null,
    latestSeq: (data.latest_seq as number) ?? 0,
    fullAccessEnabled: (data.full_access_enabled as boolean) ?? false,
    chatMode: (data.chat_mode as "write" | "read" | "plan") ?? "write",
    currentModel: (data.current_model as string | null) ?? null,
    currentModelName: (data.current_model_name as string | null) ?? null,
    visionCapable: (data.vision_capable as boolean) ?? false,
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

// ── Session Export / Import ──────────────────────────

export type ExportFormat = "md" | "txt" | "emx";

/**
 * 导出会话为指定格式，触发浏览器下载。
 */
export async function exportSession(
  sessionId: string,
  format: ExportFormat = "md",
  opts?: { includeWorkspace?: boolean },
): Promise<void> {
  const params = new URLSearchParams({ format });
  if (format === "emx" && opts?.includeWorkspace === false) {
    params.set("include_workspace", "false");
  }
  const url = buildApiUrl(
    `/sessions/${encodeURIComponent(sessionId)}/export?${params.toString()}`,
  );
  const res = await fetch(url, { headers: { ...getAuthHeaders() }, signal: _withTimeout(_UPLOAD_TIMEOUT_MS) });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `导出失败: ${res.status}`);
  }
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") || "";
  const filenameMatch = disposition.match(/filename="?([^"]+)"?/);
  const filename = filenameMatch?.[1] || `session.${format}`;

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

/**
 * 从 EMX JSON 导入会话（v2.0 完整恢复）。
 */
export async function importSession(
  emxData: unknown,
): Promise<{
  status: string;
  session_id: string;
  title: string;
  message_count: number;
  files_restored?: number;
  memories_restored?: number;
  state_restored?: boolean;
}> {
  const url = buildApiUrl("/sessions/import");
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(emxData),
    signal: _withTimeout(_UPLOAD_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `导入失败: ${res.status}`);
  }
  return res.json();
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
): Promise<{ status: string }> {
  const url = buildApiUrl(`/chat/${encodeURIComponent(sessionId)}/answer`, { direct: true });
  const res = await fetch(url, _withCredentials(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ question_id: questionId, answer }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  }));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Answer error: ${res.status}`);
  }
  return res.json();
}

export async function submitApproval(
  sessionId: string,
  approvalId: string,
  decision: "accept" | "reject" | "fullaccess",
): Promise<{ status: string }> {
  const url = buildApiUrl(`/chat/${encodeURIComponent(sessionId)}/approve`, { direct: true });
  const res = await fetch(url, _withCredentials(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ approval_id: approvalId, decision }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  }));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Approve error: ${res.status}`);
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
    throw new Error(data.error || data.detail || `Toggle full-access error: ${res.status}`);
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
    throw new Error(data.error || data.detail || `Abort error: ${res.status}`);
  }
  return res.json();
}

export async function rollbackChat(opts: {
  sessionId: string;
  turnIndex: number;
  rollbackFiles?: boolean;
  newMessage?: string;
  resendMode?: boolean;
}): Promise<{
  status: string;
  removed_messages: number;
  file_rollback_results: string[];
  turn_index: number;
}> {
  const rollbackUrl = buildApiUrl("/chat/rollback", { direct: true });
  const res = await fetch(rollbackUrl, _withCredentials(rollbackUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({
      session_id: opts.sessionId,
      turn_index: opts.turnIndex,
      rollback_files: opts.rollbackFiles ?? false,
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
  is_dir?: boolean;
}

export async function fetchExcelFiles(): Promise<ExcelFileListItem[]> {
  const url = buildApiUrl("/files/excel/list");
  const res = await fetch(url, { headers: { ...getAuthHeaders() }, signal: _withTimeout(_DEFAULT_TIMEOUT_MS) });
  if (!res.ok) return [];
  const data = await res.json();
  return data.files ?? [];
}

export async function fetchWorkspaceFiles(): Promise<ExcelFileListItem[]> {
  const url = buildApiUrl("/files/workspace/list");
  const res = await fetch(url, { headers: { ...getAuthHeaders() }, signal: _withTimeout(_DEFAULT_TIMEOUT_MS) });
  if (!res.ok) return [];
  const data = await res.json();
  return data.files ?? [];
}

export interface WorkspaceStorage {
  total_bytes: number;
  size_mb: number;
  max_bytes: number;
  max_size_mb: number;
  file_count: number;
  max_files: number;
  over_size: boolean;
  over_files: boolean;
}

export async function fetchWorkspaceStorage(): Promise<WorkspaceStorage | null> {
  const url = buildApiUrl("/files/workspace/storage");
  try {
    const res = await fetch(url, {
      headers: { ...getAuthHeaders() },
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
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
  staging_path: string | null;
  is_active_cow: boolean;
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
}): Promise<{ files: FileRegistryEntry[]; total: number } | { file: FileRegistryEntry }> {
  const params = new URLSearchParams();
  if (opts?.includeDeleted) params.set("include_deleted", "true");
  if (opts?.includeEvents) params.set("include_events", "true");
  if (opts?.fileId) params.set("file_id", opts.fileId);
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

export async function fetchFileGroups(): Promise<{ groups: FileGroup[] }> {
  try {
    return await apiGet<{ groups: FileGroup[] }>("/files/groups");
  } catch {
    return { groups: [] };
  }
}

export async function createFileGroup(opts: {
  name: string;
  description?: string;
  file_ids?: { id: string; role?: string }[];
}): Promise<FileGroup> {
  return apiPost<FileGroup>("/files/groups", opts);
}

export async function updateFileGroup(
  groupId: string,
  opts: { name?: string; description?: string },
): Promise<FileGroup> {
  return apiPut<FileGroup>(`/files/groups/${encodeURIComponent(groupId)}`, opts);
}

export async function deleteFileGroup(groupId: string): Promise<void> {
  await apiDelete(`/files/groups/${encodeURIComponent(groupId)}`);
}

export async function updateFileGroupMembers(
  groupId: string,
  opts: { add?: { file_id: string; role?: string }[]; remove?: string[] },
): Promise<FileGroup> {
  return apiPut<FileGroup>(
    `/files/groups/${encodeURIComponent(groupId)}/members`,
    opts,
  );
}

// ── Cross-file Compare & Relationships APIs ──────────────

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
  opts?: { sessionId?: string; maxRows?: number },
): Promise<CompareResponse> {
  const params = new URLSearchParams({
    path_a: normalizeExcelPath(pathA),
    path_b: normalizeExcelPath(pathB),
  });
  if (opts?.sessionId) params.set("session_id", opts.sessionId);
  if (opts?.maxRows) params.set("max_rows", String(opts.maxRows));
  const url = buildApiUrl(`/files/excel/compare?${params.toString()}`);
  const res = await fetch(url, {
    headers: { ...getAuthHeaders() },
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Compare error: ${res.status}`);
  }
  return res.json();
}

export interface RelationshipDiscoveryAPI {
  files_analyzed: number;
  file_pairs: {
    file_a: string;
    file_b: string;
    shared_columns: SharedColumnAPI[];
  }[];
  summary: string;
  merge_hints?: {
    file_a: string;
    file_b: string;
    key_column_a: string;
    key_column_b: string;
    suggested_join: string;
    suggested_join_label?: string;
    relationship?: string;
    pandas_hint?: string;
  }[];
}

export async function fetchFileRelationships(
  opts?: { directory?: string },
): Promise<RelationshipDiscoveryAPI> {
  const params = new URLSearchParams();
  if (opts?.directory) params.set("directory", opts.directory);
  const qs = params.toString();
  const url = buildApiUrl(`/files/relationships${qs ? `?${qs}` : ""}`);
  const res = await fetch(url, {
    headers: { ...getAuthHeaders() },
    signal: _withTimeout(60_000),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Relationships error: ${res.status}`);
  }
  return res.json();
}

// ── Workspace file management APIs ───────────────────────

export async function workspaceMkdir(path: string): Promise<void> {
  const url = buildApiUrl("/files/workspace/mkdir");
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ path }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || data.detail || `mkdir error: ${res.status}`);
  }
}

export async function workspaceCreateFile(path: string): Promise<void> {
  const url = buildApiUrl("/files/workspace/create");
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ path }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || data.detail || `create error: ${res.status}`);
  }
}

export async function workspaceDeleteItem(path: string): Promise<void> {
  const url = buildApiUrl("/files/workspace/item");
  const res = await fetch(url, {
    method: "DELETE",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ path }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || data.detail || `delete error: ${res.status}`);
  }
}

export async function workspaceRenameItem(oldPath: string, newPath: string): Promise<void> {
  const url = buildApiUrl("/files/workspace/rename");
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ old_path: oldPath, new_path: newPath }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || data.detail || `rename error: ${res.status}`);
  }
}

export async function uploadFileToFolder(
  file: File,
  folder: string
): Promise<{ filename: string; path: string; size: number }> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("folder", folder);
  const res = await directFetch(buildApiUrl("/upload", { direct: true }), {
    method: "POST",
    body: formData,
    signal: _withTimeout(_UPLOAD_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Upload error: ${res.status}`);
  }
  return res.json();
}

export interface AllSheetsSnapshotResponse {
  file: string;
  sheets: string[];
  all_snapshots: ExcelSnapshot[];
}

// ── Snapshot 缓存（TTL 30s，避免重复请求同一文件） ──
const _snapshotCache = new Map<string, { data: AllSheetsSnapshotResponse; ts: number }>();
const _SNAPSHOT_TTL_MS = 30_000;

function _snapshotCacheKey(path: string, opts?: { maxRows?: number; withStyles?: boolean }): string {
  return `${normalizeExcelPath(path)}|${opts?.maxRows ?? ""}|${opts?.withStyles !== false ? "1" : "0"}`;
}

/** 使指定文件的 snapshot 缓存失效（文件变更后调用） */
export function invalidateSnapshotCache(path?: string) {
  if (!path) { _snapshotCache.clear(); return; }
  const norm = normalizeExcelPath(path);
  for (const key of _snapshotCache.keys()) {
    if (key.startsWith(norm + "|")) _snapshotCache.delete(key);
  }
}

export async function fetchAllSheetsSnapshot(
  path: string,
  opts?: { maxRows?: number; sessionId?: string; withStyles?: boolean }
): Promise<AllSheetsSnapshotResponse> {
  const cacheKey = _snapshotCacheKey(path, opts);
  const cached = _snapshotCache.get(cacheKey);
  if (cached && Date.now() - cached.ts < _SNAPSHOT_TTL_MS) {
    return cached.data;
  }

  const params = new URLSearchParams({ path: normalizeExcelPath(path), all_sheets: "1" });
  if (opts?.maxRows) params.set("max_rows", String(opts.maxRows));
  if (opts?.sessionId) params.set("session_id", opts.sessionId);
  params.set("with_styles", opts?.withStyles !== false ? "1" : "0");
  const url = buildApiUrl(`/files/excel/snapshot?${params.toString()}`);
  const res = await fetch(url, { headers: { ...getAuthHeaders() }, signal: _withTimeout(_DEFAULT_TIMEOUT_MS) });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Snapshot error: ${res.status}`);
  }
  const result: AllSheetsSnapshotResponse = await res.json();
  _snapshotCache.set(cacheKey, { data: result, ts: Date.now() });
  return result;
}

export async function fetchExcelSnapshot(
  path: string,
  opts?: { sheet?: string; maxRows?: number; sessionId?: string }
): Promise<ExcelSnapshot> {
  const res = await fetch(buildExcelSnapshotUrl(path, opts), {
    headers: { ...getAuthHeaders() },
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
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
          signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
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
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Write error: ${res.status}`);
  }
  return res.json();
}

/**
 * 从工作区下载文件并返回 Blob（不触发浏览器下载）。
 * 用于重试时重新获取图片内容以编码 base64。
 */
export async function fetchFileBlob(path: string, sessionId?: string): Promise<Blob> {
  const params = new URLSearchParams({ path: normalizeExcelPath(path) });
  if (sessionId) params.set("session_id", sessionId);
  const url = buildApiUrl(`/files/download?${params.toString()}`, { direct: true });
  const res = await fetch(url, _withCredentials(url, {
    headers: { ...getAuthHeaders() },
    signal: _withTimeout(_UPLOAD_TIMEOUT_MS),
  }));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `fetchFileBlob error: ${res.status}`);
  }
  return res.blob();
}

// 下载冷却时间配置（毫秒）—— 按路径独立冷却，不阻塞不同附件的并发下载
const DOWNLOAD_COOLDOWN_MS = 1000;
const _downloadCooldowns = new Map<string, number>();

export async function downloadFile(path: string, filename?: string, sessionId?: string): Promise<void> {
  const now = Date.now();
  const lastTime = _downloadCooldowns.get(path) ?? 0;
  if (now - lastTime < DOWNLOAD_COOLDOWN_MS) {
    // 同一文件冷却中，忽略本次请求
    return;
  }
  _downloadCooldowns.set(path, now);
  const params = new URLSearchParams({ path: normalizeExcelPath(path) });
  if (sessionId) params.set("session_id", sessionId);
  const url = buildApiUrl(`/files/download?${params.toString()}`, { direct: true });
  const res = await fetch(url, _withCredentials(url, {
    headers: { ...getAuthHeaders() },
    signal: _withTimeout(_UPLOAD_TIMEOUT_MS),
  }));
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
  const res = await directFetch(buildApiUrl("/upload", { direct: true }), {
    method: "POST",
    body: formData,
    signal: _withTimeout(_UPLOAD_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Upload error: ${res.status}`);
  }
  return res.json();
}

export async function uploadFileFromUrl(url: string): Promise<{
  filename: string;
  path: string;
  size: number;
}> {
  const res = await directFetch(buildApiUrl("/upload-from-url", { direct: true }), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
    signal: _withTimeout(_UPLOAD_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `URL upload error: ${res.status}`);
  }
  return res.json();
}

// ── 工作区事务 API（原备份应用）────

export interface BackupFileSummary {
  cells_changed?: number;
  cells_added?: number;
  cells_removed?: number;
  sheets_added?: string[];
  sheets_removed?: string[];
  size_delta_bytes?: number;
}

export interface BackupFile {
  original_path: string;
  backup_path: string;
  exists: boolean;
  modified_at: number | null;
  summary?: BackupFileSummary;
}

export interface BackupListResponse {
  files: BackupFile[];
  backup_enabled: boolean;
  in_flight?: boolean;
}

export async function fetchBackupList(
  sessionId: string
): Promise<BackupListResponse> {
  const url = buildApiUrl(
    `/workspace/staged?session_id=${encodeURIComponent(sessionId)}`
  );
  const res = await fetch(url, { headers: { ...getAuthHeaders() }, signal: _withTimeout(_DEFAULT_TIMEOUT_MS) });
  if (!res.ok) {
    return { files: [], backup_enabled: false };
  }
  return res.json();
}

export interface AppliedFile {
  original: string;
  backup: string;
  undo_path?: string;
}

export async function applyBackup(opts: {
  sessionId: string;
  files?: string[];
}): Promise<{ status: string; applied: AppliedFile[]; count: number; pending_count: number }> {
  const url = buildApiUrl("/workspace/commit");
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({
      session_id: opts.sessionId,
      files: opts.files ?? null,
    }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
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
}): Promise<{ status: string; discarded: number | string; pending_count?: number }> {
  const url = buildApiUrl("/workspace/rollback");
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({
      session_id: opts.sessionId,
      files: opts.files ?? null,
    }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || data.detail || `Rollback error: ${res.status}`);
  }
  return res.json();
}

export async function undoBackup(opts: {
  sessionId: string;
  originalPath: string;
  undoPath: string;
}): Promise<{ status: string; undone: string }> {
  const url = buildApiUrl("/backup/undo");
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({
      session_id: opts.sessionId,
      original_path: opts.originalPath,
      undo_path: opts.undoPath,
    }),
    signal: _withTimeout(_DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || data.detail || `Undo error: ${res.status}`);
  }
  return res.json();
}

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

// ── ClawHub API ──────────────────────────────────────────

export interface ClawHubSearchResult {
  slug: string;
  display_name: string;
  summary: string;
  version: string | null;
  score: number;
  updated_at: number | null;
}

export interface ClawHubSkillDetail {
  slug: string;
  display_name: string;
  summary: string;
  tags: string[];
  latest_version: string | null;
  latest_changelog: string;
  owner_handle: string | null;
  owner_display_name: string | null;
  stats: Record<string, unknown>;
  created_at: number;
  updated_at: number;
}

export interface ClawHubUpdateInfo {
  slug: string;
  installed_version: string | null;
  latest_version: string | null;
  update_available: boolean;
}

export interface ClawHubInstalled {
  slug: string;
  version: string | null;
}

export async function clawhubSearch(
  query: string,
  limit = 15
): Promise<{ results: ClawHubSearchResult[] }> {
  return apiGet(`/clawhub/search?q=${encodeURIComponent(query)}&limit=${limit}`);
}

export async function clawhubSkillDetail(
  slug: string
): Promise<ClawHubSkillDetail> {
  return apiGet(`/clawhub/skill/${encodeURIComponent(slug)}`);
}

export async function clawhubInstall(opts: {
  slug: string;
  version?: string;
  overwrite?: boolean;
}): Promise<Record<string, unknown>> {
  return apiPost("/clawhub/install", opts);
}

export async function clawhubCheckUpdates(): Promise<{
  updates: ClawHubUpdateInfo[];
}> {
  return apiGet("/clawhub/updates");
}

export async function clawhubUpdate(opts: {
  slug?: string;
  version?: string;
  all?: boolean;
}): Promise<{ results: Record<string, unknown>[] }> {
  return apiPost("/clawhub/update", opts);
}

export async function clawhubListInstalled(): Promise<{
  installed: ClawHubInstalled[];
}> {
  return apiGet("/clawhub/installed");
}

// ── Docker Sandbox / Session Isolation API ───────────────

export interface DockerSandboxStatus {
  docker_sandbox_enabled: boolean;
  docker_available: boolean;
  sandbox_image_ready: boolean;
}

export async function fetchDockerSandboxStatus(): Promise<DockerSandboxStatus> {
  return apiGet<DockerSandboxStatus>("/settings/docker-sandbox");
}

export async function setDockerSandbox(enabled: boolean): Promise<{
  status: string;
  docker_sandbox_enabled: boolean;
}> {
  return apiPut("/settings/docker-sandbox", { enabled });
}

export async function buildDockerSandboxImage(force = false): Promise<{
  status: string;
  message: string;
}> {
  return apiPost("/settings/docker-sandbox/build", { force });
}

export interface SessionIsolationStatus {
  session_isolation_enabled: boolean;
}

export async function fetchSessionIsolationStatus(): Promise<SessionIsolationStatus> {
  return apiGet<SessionIsolationStatus>("/settings/session-isolation");
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

// ── Checkpoint API ───────────────────────────────────────

export interface CheckpointItem {
  turn_number: number;
  created_at: string;
  files_modified: string[];
  tool_names: string[];
  version_count: number;
}

export interface CheckpointListResponse {
  checkpoints: CheckpointItem[];
  checkpoint_enabled: boolean;
  error?: string;
}

export async function fetchCheckpoints(
  sessionId: string,
): Promise<CheckpointListResponse> {
  try {
    return await apiGet<CheckpointListResponse>(
      `/checkpoint/list?session_id=${encodeURIComponent(sessionId)}`,
    );
  } catch {
    return { checkpoints: [], checkpoint_enabled: false };
  }
}

export async function checkpointRollback(
  sessionId: string,
  turnNumber: number,
): Promise<{ status: string; turn_number: number; restored_files: string[]; count: number }> {
  return apiPost(`/checkpoint/rollback`, {
    session_id: sessionId,
    turn_number: turnNumber,
  });
}

// ── Version Advanced Operations ─────────────────────────

export async function cleanupVersionBackups(
  maxKeep = 2,
): Promise<{ status: string; removed_count: number; removed: string[] }> {
  return apiPost("/version/backups/cleanup", { max_keep: maxKeep });
}

export interface UpdateApplyResult {
  success: boolean;
  old_version: string;
  new_version: string;
  backup_dir: string;
  steps_completed: string[];
  error: string | null;
  needs_restart: boolean;
}

export async function applyVersionUpdate(opts?: {
  skipBackup?: boolean;
  skipDeps?: boolean;
  useMirror?: boolean;
}): Promise<UpdateApplyResult> {
  return apiPost("/version/update/apply", {
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
  git_commit: string | null;
  deployed_at: string | null;
  deploy_mode: string | null;
  topology: string | null;
  min_frontend_build_id: string | null;
  min_backend_version: string | null;
}

export async function fetchVersionManifest(): Promise<VersionManifest> {
  return apiGet<VersionManifest>("/version/manifest");
}

// ── Streaming Update (SSE) ──────────────────────────────

export interface UpdateProgressEvent {
  message: string;
  percent: number;
}

export type UpdateDoneEvent = UpdateApplyResult;

/**
 * 以 SSE 流式执行更新，实时接收进度事件。
 * 返回一个 AbortController 供调用方取消。
 */
export function streamVersionUpdate(
  opts: {
    skipBackup?: boolean;
    skipDeps?: boolean;
    useMirror?: boolean;
  },
  callbacks: {
    onProgress: (ev: UpdateProgressEvent) => void;
    onDone: (ev: UpdateDoneEvent) => void;
    onError: (error: string) => void;
  },
): AbortController {
  const controller = new AbortController();

  (async () => {
    try {
      const resp = await directFetch(
        `${API_BASE_PATH}/version/update/stream`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            skip_backup: opts.skipBackup ?? false,
            skip_deps: opts.skipDeps ?? false,
            use_mirror: opts.useMirror ?? false,
          }),
          signal: controller.signal,
        },
      );

      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        callbacks.onError(`HTTP ${resp.status}: ${text}`);
        return;
      }

      const reader = resp.body?.getReader();
      if (!reader) {
        callbacks.onError("服务端未返回响应体");
        return;
      }

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let currentEvent = "";
        let currentData = "";

        for (const line of lines) {
          if (line.startsWith("event:")) {
            currentEvent = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            currentData = line.slice(5).trim();
          } else if (line === "" && currentEvent && currentData) {
            try {
              const parsed = JSON.parse(currentData);
              if (currentEvent === "progress") {
                callbacks.onProgress(parsed as UpdateProgressEvent);
              } else if (currentEvent === "done") {
                callbacks.onDone(parsed as UpdateDoneEvent);
              } else if (currentEvent === "error") {
                callbacks.onError(parsed.error || "未知错误");
              }
            } catch {
              // malformed JSON, skip
            }
            currentEvent = "";
            currentData = "";
          }
        }
      }

      reader.releaseLock();
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        callbacks.onError((err as Error).message || "流式更新连接失败");
      }
    }
  })();

  return controller;
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

/**
 * 以 SSE 流式执行回滚，实时接收进度事件。
 */
export function streamRollback(
  opts: {
    target?: "full" | "backend" | "frontend";
    releaseId?: string;
    commit?: string;
    skipDeps?: boolean;
  },
  handlers: {
    onProgress?: (ev: { message: string; percent: number }) => void;
    onDone?: (result: RollbackResult) => void;
    onError?: (error: string) => void;
  },
): AbortController {
  const controller = new AbortController();
  const url = buildApiUrl("/deploy/rollback/stream");

  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      target: opts.target ?? "full",
      release_id: opts.releaseId ?? "",
      commit: opts.commit ?? "",
      skip_deps: opts.skipDeps ?? false,
    }),
    signal: controller.signal,
  })
    .then(async (resp) => {
      if (!resp.ok || !resp.body) {
        handlers.onError?.(`HTTP ${resp.status}`);
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        const lines = buf.split("\n");
        buf = lines.pop() || "";

        let eventType = "progress";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (eventType === "progress") handlers.onProgress?.(data);
              else if (eventType === "done") handlers.onDone?.(data);
              else if (eventType === "error") handlers.onError?.(data.error || "未知错误");
            } catch { /* ignore parse errors */ }
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== "AbortError") handlers.onError?.(String(err));
    });

  return controller;
}

// ── Canary (灰度) Management ────────────────────────────

export interface CanaryStatus {
  active: boolean;
  current_weight: number;
  step: number;
  total_steps: number;
  started_at: string | null;
  candidate_port: number | null;
  observe_seconds: number | null;
}

export async function fetchCanaryStatus(): Promise<CanaryStatus> {
  return apiGet<CanaryStatus>("/deploy/canary/status");
}

export async function promoteCanary(): Promise<{
  success: boolean;
  new_weight?: number;
  step?: number;
  total_steps?: number;
  error?: string;
}> {
  return apiPost("/deploy/canary/promote", {});
}

export async function abortCanary(): Promise<{
  success: boolean;
  message?: string;
  error?: string;
}> {
  return apiPost("/deploy/canary/abort", {});
}

export async function startCanary(opts?: {
  target?: "full" | "backend";
  observeSeconds?: number;
}): Promise<{ success: boolean; message?: string; error?: string }> {
  return apiPost("/deploy/canary/start", {
    target: opts?.target ?? "full",
    observe_seconds: opts?.observeSeconds ?? 60,
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

// ── Channel Status API ───────────────────────────────────

export interface ChannelFieldDef {
  key: string;
  label: string;
  hint: string;
  required: boolean;
  secret: boolean;
  type?: string;
}

export interface ChannelDetail {
  name: string;
  status: "running" | "stopped" | "error";
  supported: boolean;
  enabled: boolean;
  credentials: Record<string, string>;
  has_required: boolean;
  missing_fields: string[];
  fields: ChannelFieldDef[];
  updated_at?: string;
  dep_installed: boolean;
  install_hint?: string;
}

export interface RateLimitConfig {
  chat_per_minute: number;
  chat_per_hour: number;
  command_per_minute: number;
  command_per_hour: number;
  upload_per_minute: number;
  upload_per_hour: number;
  global_per_minute: number;
  global_per_hour: number;
  reject_cooldown_seconds: number;
  auto_ban_threshold: number;
  auto_ban_duration_seconds: number;
}

export interface ChannelSettings {
  admin_users: string;
  group_policy: string;
  group_whitelist: string;
  group_blacklist: string;
  allowed_users: string;
  default_concurrency: string;
  default_chat_mode: string;
  public_url: string;
  tg_edit_interval_min: string;
  tg_edit_interval_max: string;
  qq_progressive_chars: string;
  qq_progressive_interval: string;
  feishu_update_interval: string;
}

export interface ChannelStatusInfo {
  enabled: boolean;
  channels: string[];
  details: ChannelDetail[];
  require_bind: boolean;
  require_bind_source: "env" | "config" | "default";
  rate_limit: RateLimitConfig;
  rate_limit_env_overrides: Record<string, string>;
  settings: ChannelSettings;
  settings_env_overrides: Record<string, string>;
}

export async function fetchServerPublicIp(): Promise<{ ip: string | null }> {
  try {
    return await apiGet<{ ip: string | null }>("/server/public-ip");
  } catch {
    return { ip: null };
  }
}

export async function fetchChannelsStatus(): Promise<ChannelStatusInfo> {
  return apiGet<ChannelStatusInfo>("/channels");
}

export async function updateChannelSettings(
  settings: Partial<ChannelSettings> & { require_bind?: boolean },
): Promise<{ status: string; updated_fields?: string[]; locked_fields?: string[]; message?: string }> {
  return apiPut("/channels/settings", settings);
}

export async function saveChannelConfig(
  channelName: string,
  credentials: Record<string, string>,
  enabled: boolean,
): Promise<{ status: string; channel: string; enabled: boolean; has_required: boolean; missing_fields: string[] }> {
  return apiPut(`/channels/${channelName}/config`, { credentials, enabled });
}

export async function deleteChannelConfig(channelName: string): Promise<void> {
  return apiDelete(`/channels/${channelName}/config`);
}

export async function startChannel(
  channelName: string,
): Promise<{ status: string; message: string }> {
  return apiPost(`/channels/${channelName}/start`, {});
}

export async function stopChannel(
  channelName: string,
): Promise<{ status: string; message: string }> {
  return apiPost(`/channels/${channelName}/stop`, {});
}

export async function testChannelConfig(
  channelName: string,
  credentials?: Record<string, string>,
): Promise<{ status: string; message: string; bot_info?: { username?: string; name?: string } }> {
  return apiPost(`/channels/${channelName}/test`, credentials ? { credentials } : {});
}

export async function updateRateLimitSettings(
  settings: Partial<RateLimitConfig>,
): Promise<{ status: string; updated_fields: string[]; locked_fields?: string[]; message?: string }> {
  return apiPut("/channels/rate-limit", settings);
}
