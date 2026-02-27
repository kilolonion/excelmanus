import { normalizeExcelPath } from "@/lib/api";

/* ── Tree data types & helpers ── */

export interface TreeNode {
  name: string;
  fullPath: string;
  children: TreeNode[];
  file?: { path: string; filename: string };
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

/** Check if a folder name indicates it's a backup/origin folder */
export function isSysFolderName(name: string): string | null {
  const lower = name.toLowerCase();
  if (lower === "backups" || lower === "backup") return "备份";
  if (lower === "originals" || lower === "original") return "原始";
  if (lower === "output" || lower === "outputs") return "输出";
  return null;
}
