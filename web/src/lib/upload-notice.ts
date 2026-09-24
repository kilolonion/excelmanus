import type { FileAttachment } from "@/lib/types";
import {
  displayFileName,
  identityKey,
  normalizeFilePath,
  toPublicFileIdentity,
} from "@/lib/file-identity";
import { stripInjectedUserPromptBlocks } from "@/lib/injected-user-prompt";

export const UPLOADED_FILE_LABEL = "已上传文件";
export const UPLOADED_IMAGE_LABEL = "已上传图片";

const IMAGE_SENT_PLACEHOLDER_RE = /\n?\[图片 #\d+ 已在之前的对话中发送\]\s*$/g;

/**
 * Stable identity for an attachment rendered in a user message.
 *
 * Upload paths are the durable identity.  Do not use the array position (or
 * the temporary File object id): optimistic messages, SSE receipts and
 * history hydration can arrive in a different order.
 */
export function fileAttachmentMarker(file: Pick<FileAttachment, "path" | "filename">): string {
  const identity = toPublicFileIdentity(file.path) || normalizeFilePath(file.path) || file.filename;
  return `attachment:${identityKey(identity) || file.filename}`;
}

/** Merge duplicate attachment records while retaining the most complete one. */
export function dedupeFileAttachments(files: FileAttachment[] | undefined): FileAttachment[] {
  if (!files || files.length === 0) return [];
  const byMarker = new Map<string, FileAttachment>();
  for (const file of files) {
    if (!file || typeof file.path !== "string" || !file.path.trim()) continue;
    const marker = fileAttachmentMarker(file);
    const previous = byMarker.get(marker);
    if (!previous || (previous.size <= 0 && file.size > 0)) {
      byMarker.set(marker, file);
    }
  }
  return [...byMarker.values()];
}

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

// Workbook conversation context is sent to the model as text for backwards
// compatibility. It is metadata, however, and must never be repeated in the
// user-facing bubble. Restrict the match to the canonical JSON form produced
// by formatWorkbookMessage so a user's ordinary prose is preserved.
const WORKBOOK_SHEET_CONTEXT_RE = /^[ \t]*当前工作表：\s*(?:"(?:\\.|[^"\\])*"|'[^'\n]*')\s*$/gm;
const WORKBOOK_DISCUSSION_CONTEXT_RE = /^[ \t]*当前讨论的表格：\s*\{[^\n]*\}\s*$/gm;
const WORKBOOK_FILE_AND_SHEET_CONTEXT_RE = /^(?:[ \t]*@file:[^\n]+|[ \t]*[^\n]{1,260}\.(?:xlsx?|xlsm|xlsb|csv))\r?\n[ \t]*当前工作表：\s*(?:"(?:\\.|[^"\\])*"|'[^'\n]*')\s*\r?\n?/gim;

export function stripGeneratedWorkbookContext(content: string): string {
  return content
    .replace(WORKBOOK_FILE_AND_SHEET_CONTEXT_RE, "")
    .replace(WORKBOOK_SHEET_CONTEXT_RE, "")
    .replace(WORKBOOK_DISCUSSION_CONTEXT_RE, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/**
 * Canonical UI projection for every user-message source (optimistic, SSE,
 * history and retry). The model still receives the original transport text;
 * only the visible copy and attachment list are normalized here.
 */
export function prepareUserMessageDisplay(rawContent: string): { content: string; files: FileAttachment[] } {
  const extracted = extractFileAttachmentsFromContent(stripImageSentPlaceholder(String(rawContent ?? "")));
  const content = stripGeneratedWorkbookContext(stripInjectedUserPromptBlocks(extracted.content));
  return { content, files: dedupeFileAttachments(extracted.files) };
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
    const filename = displayFileName(filePath) || filePath;
    files.push({ filename, path: filePath, size: 0 });
  }
  UPLOAD_NOTICE_RE.lastIndex = 0;
  const cleaned = rawContent
    .replace(UPLOAD_NOTICE_RE, "")
    .replace(/^\n+/, "")
    .trim();
  return { content: cleaned || (files.length > 0 ? "" : rawContent.trim()), files: dedupeFileAttachments(files) };
}
