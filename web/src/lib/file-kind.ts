/**
 * 工作区文件的唯一 kind 表。
 *
 * 打开入口、图标、tooltip、聊天路径、侧栏都必须走这里，禁止再抄一份扩展名 Set。
 * spreadsheet 口径对齐工具层 / Univer：csv 是表；tsv 后端 Univer 流不接收，保持 text。
 * 视觉附件资格（isVisionImageFile）单独列出：svg 可预览，默认不送进模型。
 * 未知后缀按 text 打开，真正能不能读由后端 /files/read 的 UTF-8 sniff 守门。
 */

export type WorkspaceFileKind = "spreadsheet" | "word" | "image" | "text" | "binary";

const SPREADSHEET_EXTENSIONS = new Set(["xlsx", "xls", "xlsm", "xlsb", "csv"]);

const WORD_EXTENSIONS = new Set(["docx"]);

const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "bmp", "webp", "svg"]);

const VISION_IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "bmp", "webp"]);

const VISION_IMAGE_MIMES = new Set([
  "image/png",
  "image/jpeg",
  "image/jpg",
  "image/gif",
  "image/webp",
  "image/bmp",
]);

const TEXT_EXTENSIONS = new Set([
  "txt",
  "md",
  "markdown",
  "mdx",
  "rst",
  "json",
  "js",
  "jsx",
  "mjs",
  "cjs",
  "ts",
  "tsx",
  "py",
  "pyi",
  "rb",
  "go",
  "rs",
  "java",
  "c",
  "cpp",
  "h",
  "hpp",
  "cs",
  "php",
  "swift",
  "kt",
  "scala",
  "sh",
  "bash",
  "zsh",
  "bat",
  "ps1",
  "sql",
  "html",
  "css",
  "scss",
  "less",
  "xml",
  "yaml",
  "yml",
  "toml",
  "ini",
  "cfg",
  "conf",
  "log",
  "graphql",
  "gql",
  "vue",
  "svelte",
  "ex",
  "exs",
  "erl",
  "hs",
  "ml",
  "fs",
  "clj",
  "lua",
  "r",
  "dart",
  "groovy",
  "tsv",
  "ipynb",
  "lock",
]);

const KNOWN_BINARY_EXTENSIONS = new Set([
  "pdf",
  "zip",
  "tar",
  "gz",
  "tgz",
  "7z",
  "rar",
  "pptx",
  "ppt",
  "doc",
  "bin",
  "exe",
  "dll",
  "so",
  "dylib",
  "wasm",
  "class",
  "pyc",
  "pyd",
  "iso",
  "dmg",
  "apk",
  "mp3",
  "mp4",
  "wav",
  "avi",
  "mov",
  "webm",
  "woff",
  "woff2",
  "ttf",
  "otf",
  "eot",
  "ico",
  "msi",
  "deb",
  "rpm",
]);

const SPECIAL_TEXT_FILENAMES = new Set([
  ".gitignore",
  ".dockerignore",
  ".env",
  ".editorconfig",
  ".npmrc",
  ".nvmrc",
  ".prettierrc",
  "dockerfile",
  "makefile",
  "license",
  "copying",
  "gemfile",
  "procfile",
  "rakefile",
  "jenkinsfile",
  "readme",
  "changelog",
  "authors",
  "notice",
  "vagrantfile",
]);

function lastPathSegment(filenameOrPath: string): string {
  const normalized = filenameOrPath.replace(/\\/g, "/").trim();
  const slash = normalized.lastIndexOf("/");
  return slash >= 0 ? normalized.slice(slash + 1) : normalized;
}

export function fileNameOf(filenameOrPath: string): string {
  return lastPathSegment(filenameOrPath);
}

function getExtension(filename: string): string {
  const lower = lastPathSegment(filename).toLowerCase();
  if (lower.startsWith(".") && lower.indexOf(".", 1) < 0) {
    return "";
  }
  const dotIndex = lower.lastIndexOf(".");
  if (dotIndex <= 0) return "";
  return lower.slice(dotIndex + 1);
}

function isSpecialTextName(filename: string): boolean {
  const lower = lastPathSegment(filename).toLowerCase();
  if (SPECIAL_TEXT_FILENAMES.has(lower)) return true;
  return lower.startsWith(".env.");
}

export function classifyWorkspaceFile(filenameOrPath: string): WorkspaceFileKind {
  const name = lastPathSegment(filenameOrPath);
  if (!name) return "binary";
  if (isSpecialTextName(name)) return "text";
  const ext = getExtension(name);
  if (SPREADSHEET_EXTENSIONS.has(ext)) return "spreadsheet";
  if (WORD_EXTENSIONS.has(ext)) return "word";
  if (IMAGE_EXTENSIONS.has(ext)) return "image";
  if (TEXT_EXTENSIONS.has(ext)) return "text";
  if (KNOWN_BINARY_EXTENSIONS.has(ext)) return "binary";
  return "text";
}

export function isSpreadsheetFile(filename: string): boolean {
  return classifyWorkspaceFile(filename) === "spreadsheet";
}

export function isExcelFile(filename: string): boolean {
  return isSpreadsheetFile(filename);
}

export function isWordFile(filename: string): boolean {
  return classifyWorkspaceFile(filename) === "word";
}

export function isImageFile(filename: string): boolean {
  return classifyWorkspaceFile(filename) === "image";
}

export function isVisionImageFile(filename: string): boolean {
  return VISION_IMAGE_EXTENSIONS.has(getExtension(filename));
}

export function isVisionImageMime(mime: string): boolean {
  return VISION_IMAGE_MIMES.has(mime.toLowerCase().trim());
}

export function isVisionImageUpload(file: { name: string; type?: string }): boolean {
  return isVisionImageFile(file.name) || isVisionImageMime(file.type || "");
}

export function isTextFile(filename: string): boolean {
  return classifyWorkspaceFile(filename) === "text";
}

export function isTextPreviewableFile(filename: string): boolean {
  return isTextFile(filename);
}

export function isCodeFile(filename: string): boolean {
  return isTextFile(filename);
}

export function isPreviewableWorkspaceFile(filename: string): boolean {
  const kind = classifyWorkspaceFile(filename);
  return kind === "image" || kind === "text";
}

export function workspaceFileOpenHint(filename: string): string {
  switch (classifyWorkspaceFile(filename)) {
    case "spreadsheet":
      return "单击: 侧边面板 | 双击: 全屏";
    case "word":
      return "单击: 文档面板 | 双击: 全屏";
    case "image":
    case "text":
      return "单击: 预览 | 菜单: 下载";
    default:
      return "单击: 下载";
  }
}

export function isWorkspaceFileHref(href: string): boolean {
  const trimmed = href.trim();
  if (!trimmed || trimmed.length > 260) return false;
  if (trimmed.includes("://")) return false;
  if (/[\n\r\t]/.test(trimmed)) return false;
  if (trimmed.startsWith("#") || trimmed.toLowerCase().startsWith("mailto:")) return false;
  const name = lastPathSegment(trimmed);
  if (!name) return false;
  const looksLikePath =
    trimmed.includes("/") ||
    trimmed.includes("\\") ||
    name.includes(".") ||
    isSpecialTextName(name);
  if (!looksLikePath) return false;
  const kind = classifyWorkspaceFile(name);
  if (kind !== "binary") return true;
  return KNOWN_BINARY_EXTENSIONS.has(getExtension(name));
}

function acceptList(exts: Set<string>): string[] {
  return [...exts].map((ext) => `.${ext}`);
}

export const WORKSPACE_FILE_INPUT_ACCEPT = [
  ...acceptList(SPREADSHEET_EXTENSIONS),
  ...acceptList(WORD_EXTENSIONS),
  ...acceptList(IMAGE_EXTENSIONS),
  ...acceptList(TEXT_EXTENSIONS),
  ...acceptList(KNOWN_BINARY_EXTENSIONS),
].join(",");
