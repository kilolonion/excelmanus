/**
 * Canonical public file identity for badges / recent files / history recovery.
 *
 * Mirrors excelmanus/workspace/identity.py.
 * Frontend cannot realpath; it normalizes ./ vs abs vs missing prefix, drops reserved
 * / backup leftovers, and dedupes by identity — not raw string.
 */

const RESERVED_PREFIXES = [
  ".excelmanus",
  "outputs/backups",
  "outputs/.versions",
  "outputs/audits",
  ".versions",
] as const;

const TS_BACKUP_NAME_RE = /^(.+)_\d{8}T\d{6}_[0-9a-fA-F]{4}(\.[^.]+)$/;
const UPLOAD_HEX_PREFIX_RE = /^[0-9a-fA-F]{8}_/;

function looksLikeTimestampedBackupName(name: string): boolean {
  return TS_BACKUP_NAME_RE.test(name);
}

function normalizeSlashes(raw: string): string {
  return String(raw ?? "").trim().replace(/\\/g, "/");
}

function isReservedRelative(rel: string): boolean {
  const text = rel.replace(/^\.\//, "").replace(/^\/+/, "");
  if (!text) return false;
  const first = text.split("/")[0] ?? "";
  if (first === ".excelmanus" || first === ".versions") return true;
  return RESERVED_PREFIXES.some((prefix) => text === prefix || text.startsWith(`${prefix}/`));
}

/**
 * Markdown link hrefs arrive URI-encoded (mdast-util-to-hast `normalizeUri`
 * escapes non-ASCII). Decode once back to the on-disk path; malformed `%XX`
 * input is returned untouched.
 */
export function decodeUriEscapedPath(href: string): string {
  try {
    return decodeURIComponent(href);
  } catch {
    return href;
  }
}

/** Lexical normalization only: never infer a workspace from a directory name. */
export function normalizeFilePath(raw: string): string {
  let p = normalizeSlashes(raw);
  if (p.startsWith("<path>/")) p = p.slice("<path>/".length).trim();
  if (!p) return "";
  const prefix = p.startsWith("//") ? "//" : p.startsWith("/") ? "/" : "";
  const parts = p.split("/").filter((part) => part && part !== ".");
  const path = prefix + parts.join("/");
  return prefix || /^[A-Za-z]:/.test(path) ? path : path ? `./${path}` : "";
}

/** Public identity (`./rel`) or null if reserved / overlay leftover. */
export function toPublicFileIdentity(raw: string, workspaceRoot?: string): string | null {
  const text = String(raw ?? "").trim();
  if (!text || /[\n\r\t\0]/.test(text)) return null;

  let path = normalizeFilePath(text);
  if (path.startsWith("/") || /^[A-Za-z]:/.test(path)) {
    // Absolute paths can only be restored with the actual bound workspace.
    // In particular, a nested `team/uploads` is not the workspace's uploads.
    if (!workspaceRoot) return null;
    const root = normalizeFilePath(workspaceRoot).replace(/\/$/, "");
    const windows = /^[A-Za-z]:\//.test(root) || root.startsWith("//");
    const key = windows ? path.toLowerCase() : path;
    const rootKey = windows ? root.toLowerCase() : root;
    if (!key.startsWith(`${rootKey}/`)) return null;
    path = path.slice(root.length + 1);
  }
  const rel = path.replace(/^\.\//, "");
  if (!rel) return null;
  if (isReservedRelative(rel)) return null;
  if (looksLikeTimestampedBackupName(rel.split("/").pop() || "")) return null;


  const parts = rel.split("/").filter(Boolean);
  if (parts.includes("..")) return null;
  if (parts.some((part) => part.startsWith(".") || part.startsWith("~$"))) return null;

  const leaf = parts[parts.length - 1] ?? "";
  if (leaf.startsWith("_rc_") || leaf.startsWith("_sw_")) return null;
  if (rel === "scripts/temp" || rel.startsWith("scripts/temp/")) return null;

  return `./${rel.replace(/^\.\//, "")}`;
}

/**
 * Display filename: never leak internal encodings. Strips the
 * ``uploads/{8hex}_`` prefix (any depth) and the FVM
 * ``_{YYYYMMDDTHHMMSS}_{4hex}`` backup suffix — mirrors
 * ``excelmanus/workspace/identity.py::display_name_for``.
 */
export function displayFileName(identity: string): string {
  const rel = normalizeSlashes(identity).replace(/^\.\//, "");
  let base = rel.split("/").pop() || rel;
  const stamp = TS_BACKUP_NAME_RE.exec(base);
  if (stamp) base = `${stamp[1]}${stamp[2]}`;
  if ((rel.startsWith("uploads/") || rel.includes("/uploads/") || stamp) && UPLOAD_HEX_PREFIX_RE.test(base)) {
    base = base.slice(9);
  }
  return base;
}

/** Display path: keep the directory portion, clean the leaf name. */
export function displayFilePath(identity: string): string {
  const norm = normalizeSlashes(identity);
  if (norm.replace(/^\.\//, "").endsWith("/")) return norm;
  const name = displayFileName(norm);
  const stripped = norm.replace(/^\.\//, "");
  const idx = stripped.lastIndexOf("/");
  const rel = name ? (idx >= 0 ? `${stripped.slice(0, idx + 1)}${name}` : name) : stripped;
  return norm.startsWith("./") ? `./${rel}` : rel;
}

export function identityKey(identity: string): string {
  return normalizeFilePath(identity).replace(/^\.\//, "");
}

/** Dedupe incoming paths onto existing by canonical identity. */
export function mergeAffectedFiles(existing: string[], incoming: string[]): string[] {
  const byKey = new Map<string, string>();
  for (const item of existing) {
    const ident = toPublicFileIdentity(item);
    if (!ident) continue;
    byKey.set(identityKey(ident), ident);
  }
  for (const item of incoming) {
    const ident = toPublicFileIdentity(item);
    if (!ident) continue;
    const key = identityKey(ident);
    if (!byKey.has(key)) byKey.set(key, ident);
  }
  return Array.from(byKey.values());
}

/** Write-tool file_path only — do not scan tool JSON for every .xlsx token. */
export function collectHistoryAffectedFiles(
  toolName: string,
  args: Record<string, unknown>,
  writeToolNames: Set<string>,
): string[] {
  if (!writeToolNames.has(toolName)) return [];
  const argFilePath = typeof args.file_path === "string" ? args.file_path : "";
  const ident = toPublicFileIdentity(argFilePath);
  return ident ? [ident] : [];
}
