const WORD_EXTENSIONS = new Set(["docx"]);

const EXCEL_EXTENSIONS = new Set(["xlsx", "xls", "xlsm", "xlsb"]);

const IMAGE_EXTENSIONS = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "bmp",
  "webp",
  "svg",
]);

const TEXT_EXTENSIONS = new Set([
  "txt",
  "md",
  "markdown",
  "json",
  "js",
  "jsx",
  "ts",
  "tsx",
  "py",
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
  "csv",
  "tsv",
]);

function getExtension(filename: string): string {
  const lower = filename.toLowerCase();
  const dotIndex = lower.lastIndexOf(".");
  if (dotIndex < 0) return "";
  return lower.slice(dotIndex + 1);
}

export function isWordFile(filename: string): boolean {
  return WORD_EXTENSIONS.has(getExtension(filename));
}

export function isExcelFile(filename: string): boolean {
  return EXCEL_EXTENSIONS.has(getExtension(filename));
}

export function isImageFile(filename: string): boolean {
  return IMAGE_EXTENSIONS.has(getExtension(filename));
}

export function isTextPreviewableFile(filename: string): boolean {
  const lower = filename.toLowerCase();
  if (lower === ".gitignore" || lower === ".dockerignore" || lower === ".env") {
    return true;
  }
  if (lower.startsWith(".env.")) {
    return true;
  }
  return TEXT_EXTENSIONS.has(getExtension(lower));
}

export function isPreviewableWorkspaceFile(filename: string): boolean {
  return isImageFile(filename) || isTextPreviewableFile(filename);
}
