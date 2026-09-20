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

function looksLikeTimestampedBackupName(name: string): boolean {
  return TS_BACKUP_NAME_RE.test(name);
}

function extractWorkspaceRelative(raw: string): string {
  let p = normalizeSlashes(raw);
  if (!p) return "";
  if (p.startsWith("<path>/")) p = p.slice("<path>/".length).trim();

  const uploadsIdx = p.indexOf("/uploads/");
  if (uploadsIdx >= 0) return p.slice(uploadsIdx + 1);
  if (p.startsWith("uploads/")) return p;

  const outputsIdx = p.indexOf("/outputs/");
  if (outputsIdx >= 0) return p.slice(outputsIdx + 1);
  if (p.startsWith("outputs/")) return p;

  p = p.replace(/^\.\//, "");
  if (p.startsWith("/")) {
    const parts = p.split("/").filter(Boolean);
    if (parts.length <= 2 && (parts[0] === "workspace" || parts[0] === "tmp" || parts[0] === "Users" || parts[0] === "home" || parts[0] === "var" || parts[0] === "private")) {
      return parts[parts.length - 1] ?? "";
    }
    return parts[parts.length - 1] ?? "";
  }
  return p;
}

/** Public identity (`./rel`) or null if reserved / overlay leftover. */
export function toPublicFileIdentity(raw: string): string | null {
  const text = String(raw ?? "").trim();
  if (!text || text.length > 260 || /[\n\r\t]/.test(text)) return null;

  const rel = extractWorkspaceRelative(text);
  if (!rel) return null;
  if (isReservedRelative(rel)) return null;

  const base = rel.split("/").pop() || rel;
  if (looksLikeTimestampedBackupName(base)) return null;
  if (rel.includes("outputs/backups")) return null;

  const parts = rel.split("/").filter(Boolean);
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
  if ((rel.startsWith("uploads/") || stamp) && UPLOAD_HEX_PREFIX_RE.test(base)) {
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
  return normalizeSlashes(identity).replace(/^\.\//, "");
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
