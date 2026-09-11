import type { FileAttachment } from "@/lib/types";

export const UPLOADED_FILE_LABEL = "已上传文件";
export const UPLOADED_IMAGE_LABEL = "已上传图片";

const IMAGE_SENT_PLACEHOLDER_RE = /\n?\[图片 #\d+ 已在之前的对话中发送\]\s*$/g;

/**
 * Agent-facing upload notice. Must stay in sync with backend title stripping
 * in excelmanus/api_routes_chat.py (`[已上传文件: ...]` / `[已上传图片: ...]`).
 */
export function formatUploadNotice(kind: "file" | "image", path: string): string {
  const label = kind === "image" ? UPLOADED_IMAGE_LABEL : UPLOADED_FILE_LABEL;
  return `[${label}: ${path}]`;
}

/**
 * Matches `[已上传文件: path]` / `[已上传图片: path]`.
 * Also recovers historically mojibake'd notices that still contain an uploads/ path.
 */
const UPLOAD_NOTICE_RE =
  /\[(?:已上传(?:文件|图片):\s*([^\]\n]+)|[^\n\[\]]{1,40}[:：?]\s*((?:\.\.?\/)?uploads\/[^\]\n]+))\]/g;

export function stripImageSentPlaceholder(content: string): string {
  return content.replace(IMAGE_SENT_PLACEHOLDER_RE, "").trim();
}

export function extractFileAttachmentsFromContent(
  rawContent: string,
): { content: string; files: FileAttachment[] } {
  const files: FileAttachment[] = [];
  UPLOAD_NOTICE_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = UPLOAD_NOTICE_RE.exec(rawContent)) !== null) {
    const filePath = (match[1] || match[2] || "").trim();
    if (!filePath) continue;
    const filename = filePath.split("/").pop() || filePath;
    files.push({ filename, path: filePath, size: 0 });
  }
  UPLOAD_NOTICE_RE.lastIndex = 0;
  const cleaned = rawContent
    .replace(UPLOAD_NOTICE_RE, "")
    .replace(/^\n+/, "")
    .trim();
  return { content: cleaned || (files.length > 0 ? "" : rawContent.trim()), files };
}
