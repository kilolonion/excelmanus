import { normalizeExcelPath } from "@/lib/api";

/* ── Tree data types & helpers ── */

export interface TreeNode {
  name: string;
  fullPath: string;
  children: TreeNode[];
  file?: { path: string; filename: string };
}

export interface WorkspaceFileEntry {
  path: string;
  filename: string;
  is_dir?: boolean;
}

export function normalizePath(p: string): string {
  const n = normalizeExcelPath(p);
  return n.startsWith("./") ? n.slice(2) : n;
}

export function buildTree(files: { path: string; filename: string; is_dir?: boolean }[]): TreeNode {
  const root: TreeNode = { name: "", fullPath: "", children: [] };

  const ensureFolder = (parent: TreeNode, name: string, fullPath: string): TreeNode => {
    let existing = parent.children.find((c) => !c.file && c.name === name);
    if (!existing) {
      existing = { name, fullPath, children: [] };
      parent.children.push(existing);
    }
    return existing;
  };

  for (const file of files) {
    const normalized = normalizePath(file.path);
    const parts = normalized.split("/").filter(Boolean);
    if (parts.length === 0) continue;

    if (file.is_dir) {
      // 创建文件夹节点（及中间目录）
      let current = root;
      for (let i = 0; i < parts.length; i++) {
        current = ensureFolder(current, parts[i], parts.slice(0, i + 1).join("/"));
      }
    } else {
      // 创建文件节点及中间目录
      let current = root;
      for (let i = 0; i < parts.length - 1; i++) {
        current = ensureFolder(current, parts[i], parts.slice(0, i + 1).join("/"));
      }
      const leafName = parts[parts.length - 1];
      current.children.push({
        name: leafName,
        fullPath: normalized,
        children: [],
        file,
      });
    }
  }

  return collapseTree(root);
}

function collapseTree(node: TreeNode): TreeNode {
  node.children = node.children.map(collapseTree);

  // 排序：先文件夹后文件，均按字母序
  node.children.sort((a, b) => {
    const aIsDir = !a.file;
    const bIsDir = !b.file;
    if (aIsDir !== bIsDir) return aIsDir ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  // 折叠单子文件夹（类似 VSCode）
  if (!node.file && node.children.length === 1 && !node.children[0].file && node.name !== "") {
    const child = node.children[0];
    return {
      ...child,
      name: `${node.name}/${child.name}`,
      fullPath: child.fullPath,
    };
  }

  return node;
}

/** Count all leaf files in a tree node (recursive) */
export function countFiles(node: TreeNode): number {
  if (node.file) return 1;
  return node.children.reduce((sum, c) => sum + countFiles(c), 0);
}

/* ── System file filtering ── */

/** Directory names considered internal / system (hidden by default). */
const SYSTEM_DIR_NAMES = new Set([
  "scripts", "outputs", "backups", "originals",
  "__pycache__", "node_modules", ".versions",
]);

/** File extensions considered internal / system (hidden by default). */
const SYSTEM_EXTENSIONS = new Set([
  ".py", ".pyc", ".pyo",
  ".log",
  ".db", ".db-shm", ".db-wal",
  ".sh", ".bat", ".ps1",
]);

/** Check whether a workspace file entry is a "system" file that should be hidden by default. */
export function isSystemFile(entry: { path: string; filename: string; is_dir?: boolean }): boolean {
  const normalized = normalizePath(entry.path);
  const parts = normalized.split("/").filter(Boolean);

  // Directory: check if top-level dir name is a system dir
  if (entry.is_dir) {
    const topDir = parts[0]?.toLowerCase();
    return SYSTEM_DIR_NAMES.has(topDir ?? "");
  }

  // File inside a system directory
  if (parts.length >= 2) {
    const topDir = parts[0].toLowerCase();
    if (SYSTEM_DIR_NAMES.has(topDir)) return true;
  }

  // File at root level: check extension
  const name = entry.filename.toLowerCase();
  for (const ext of SYSTEM_EXTENSIONS) {
    if (name.endsWith(ext)) return true;
  }

  return false;
}

/** Filter workspace files, optionally hiding system files. */
export function filterWorkspaceFiles(
  files: { path: string; filename: string; is_dir?: boolean }[],
  showSystem: boolean,
): { path: string; filename: string; is_dir?: boolean }[] {
  if (showSystem) return files;
  return files.filter((f) => !isSystemFile(f));
}

function _basename(path: string): string {
  const normalized = normalizePath(path);
  const parts = normalized.split("/").filter(Boolean);
  return parts[parts.length - 1] || normalized;
}

/**
 * Add or replace a workspace item by path (path compare is normalized).
 */
export function upsertWorkspaceEntry(
  files: WorkspaceFileEntry[],
  entry: WorkspaceFileEntry,
): WorkspaceFileEntry[] {
  const key = normalizePath(entry.path);
  const next = files.filter((f) => normalizePath(f.path) !== key);
  const normalizedPath = normalizePath(entry.path);
  return [
    ...next,
    {
      ...entry,
      path: normalizedPath,
      filename: entry.filename || _basename(normalizedPath),
    },
  ];
}

/**
 * Remove one or more workspace items. If a path is a folder, all descendants are removed too.
 */
export function removeWorkspaceEntries(
  files: WorkspaceFileEntry[],
  paths: string[],
): WorkspaceFileEntry[] {
  const targets = paths
    .map((p) => normalizePath(p))
    .filter((p) => p.length > 0);
  if (targets.length === 0) return files;

  return files.filter((f) => {
    const current = normalizePath(f.path);
    for (const t of targets) {
      if (current === t || current.startsWith(`${t}/`)) return false;
    }
    return true;
  });
}

/**
 * Rename/move an item in workspace list. For folders, descendants are renamed by prefix.
 */
export function renameWorkspaceEntries(
  files: WorkspaceFileEntry[],
  oldPath: string,
  newPath: string,
): WorkspaceFileEntry[] {
  const oldNorm = normalizePath(oldPath);
  const newNorm = normalizePath(newPath);
  if (!oldNorm || !newNorm || oldNorm === newNorm) return files;

  return files.map((f) => {
    const current = normalizePath(f.path);
    if (current !== oldNorm && !current.startsWith(`${oldNorm}/`)) return f;

    const suffix = current === oldNorm ? "" : current.slice(oldNorm.length + 1);
    const replaced = suffix ? `${newNorm}/${suffix}` : newNorm;
    return {
      ...f,
      path: replaced,
      filename: _basename(replaced),
    };
  });
}

/** Check if a folder name indicates it's a backup/origin folder */
export function isSysFolderName(name: string): string | null {
  const lower = name.toLowerCase();
  if (lower === "backups" || lower === "backup") return "备份";
  if (lower === "originals" || lower === "original") return "原始";
  if (lower === "output" || lower === "outputs") return "输出";
  return null;
}
