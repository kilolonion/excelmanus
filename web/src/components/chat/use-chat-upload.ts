"use client";

import { useCallback, useEffect, useRef, useState, type Dispatch, type MutableRefObject, type RefObject, type SetStateAction } from "react";
import { fetchFileBlob, uploadFileFromUrl } from "@/lib/api";
import { ensureWorkbookSession, importWorkspaceFile } from "@/lib/open-workbook";
import { identityKey, toPublicFileIdentity } from "@/lib/file-identity";
import type { AttachedFile } from "@/lib/types";
import { useSessionStore } from "@/stores/session-store";
import { workspaceKeyForSessionId } from "@/lib/workspace-file-ref";
import { isVisionImageUpload } from "@/lib/file-kind";
import { detectFileUrl, friendlyUploadError } from "./chat-input-constants";
import {
  formatFileMention,
  insertTokensIntoText,
  scheduleTextareaCursor,
  toDisplayMentionTokens,
  trackRecentExcelFile,
} from "./chat-input-insert";
import { workspaceFileMention, type WorkspaceDroppedFile } from "./chat-drop";

interface UseChatUploadOptions {
  text: string;
  setText: Dispatch<SetStateAction<string>>;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  tokenMapRef: MutableRefObject<Map<string, string>>;
  setConfirmedTokens: Dispatch<SetStateAction<Set<string>>>;
}

function newAttachmentId(prefix = ""): string {
  return `${prefix}${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function attachmentIdentity(path: string | undefined, filename: string): string {
  if (path) {
    const ident = toPublicFileIdentity(path);
    return identityKey(ident ?? path);
  }
  return filename;
}

/** 本地选择/粘贴时上传尚未返回工作区路径，至少写入可解析的 `@file:` + basename。 */
function pendingMentionPath(filename: string): string {
  const basename = filename.replace(/\\/g, "/").split("/").pop() || "unnamed";
  // @file tokens are whitespace-delimited and the backend parser rejects
  // punctuation used as range/version delimiters.  The server applies the
  // same kind of basename sanitisation when the upload completes; this
  // placeholder only needs to remain parseable until backfill replaces it.
  return basename.replace(/[\s,;!?\[\]@]+/g, "_").replace(/^_+|_+$/g, "") || "unnamed";
}

function mentionForPendingUpload(filename: string): string {
  return formatFileMention({ path: pendingMentionPath(filename) });
}

/**
 * Chromium/Safari expose pasted files through either DataTransfer.files or
 * DataTransfer.items.  The latter is required for some native clipboard
 * producers (notably Finder and browser image copies).
 */
function clipboardFiles(data: DataTransfer): File[] {
  const files = Array.from(data.files ?? []);
  if (files.length > 0) return files;
  return Array.from(data.items ?? [])
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((file): file is File => file !== null);
}

function fileMetadataLine(line: string): string {
  let value = line.trim().replace(/^['"]|['"]$/g, "");
  if (value.startsWith("file://")) {
    try {
      value = decodeURIComponent(value.slice("file://".length));
    } catch {
      value = value.slice("file://".length);
    }
  }
  return value.replace(/\\/g, "/");
}

/**
 * Native file copies may also carry a path/URI as text.  That text is
 * clipboard metadata, while spreadsheet/browser copies can legitimately
 * carry useful TSV or HTML text alongside a file.  Only suppress text that
 * is clearly a path/URI for one of the pasted files.
 */
function isClipboardFileMetadata(data: DataTransfer, files: File[], text: string): boolean {
  if (!text.trim()) return true;
  const uriList = data.getData("text/uri-list").trim();
  const lines = (uriList || text).split(/\r?\n/).map(fileMetadataLine).filter(Boolean);
  if (lines.length === 0) return true;
  return lines.every((line) => {
    const pathLike = line.startsWith("/") || line.startsWith("~/") || /^[A-Za-z]:\//.test(line);
    if (!pathLike && !uriList) return false;
    return files.some((file) => line === file.name || line.endsWith(`/${file.name}`));
  });
}

function activeUploadScope(): { sessionId?: string; workspaceId?: string } {
  const state = useSessionStore.getState();
  const sessionId = state.activeSessionId ?? undefined;
  const workspaceId = state.sessions.find((item) => item.id === sessionId)?.workspaceId ?? undefined;
  return {
    ...(sessionId ? { sessionId } : {}),
    ...(workspaceId ? { workspaceId } : {}),
  };
}

/**
 * 上传成功后按 value 把 `@file:basename` 回填为 `@file:uploads/{hash}_{name}`。
 * display 不变；命中返回 true，未命中返回 false（幂等，不抛错）。
 */
export function backfillPendingUploadFull(
  tokenMap: Map<string, string>,
  originalName: string,
  resolvedPath: string,
): boolean {
  const pendingFull = mentionForPendingUpload(originalName);
  const resolvedFull = formatFileMention({ path: resolvedPath });
  let hit = false;
  for (const [display, full] of tokenMap) {
    if (full === pendingFull) {
      tokenMap.set(display, resolvedFull);
      hit = true;
    }
  }
  return hit;
}

export function useChatUpload({
  text,
  setText,
  textareaRef,
  tokenMapRef,
  setConfirmedTokens,
}: UseChatUploadOptions) {
  const [files, setFiles] = useState<AttachedFile[]>([]);
  const previewUrlCache = useRef(new Map<File, string>());

  const getPreviewUrl = useCallback((file: File): string => {
    let url = previewUrlCache.current.get(file);
    if (!url) {
      url = URL.createObjectURL(file);
      previewUrlCache.current.set(file, url);
    }
    return url;
  }, []);

  useEffect(() => {
    const currentFiles = new Set(files.map((af) => af.file));
    previewUrlCache.current.forEach((url, file) => {
      if (!currentFiles.has(file)) {
        URL.revokeObjectURL(url);
        previewUrlCache.current.delete(file);
      }
    });
  }, [files]);

  useEffect(() => {
    const cache = previewUrlCache.current;
    return () => {
      cache.forEach((url) => URL.revokeObjectURL(url));
      cache.clear();
    };
  }, []);

  const insertDocMentions = useCallback((fullTokens: string[]) => {
    if (fullTokens.length === 0) return;
    const displayTokens = toDisplayMentionTokens(fullTokens, tokenMapRef.current);
    setConfirmedTokens((prev) => {
      const next = new Set(prev);
      displayTokens.forEach((token) => next.add(token));
      return next;
    });
    const textarea = textareaRef.current;
    setText((prev) => {
      const cursorPos = textarea?.selectionStart ?? prev.length;
      const { newText, newCursorPos } = insertTokensIntoText(prev, cursorPos, displayTokens);
      scheduleTextareaCursor(textarea, newCursorPos);
      return newText;
    });
  }, [textareaRef, tokenMapRef, setConfirmedTokens, setText]);

  const triggerUpload = useCallback(async (id: string, file: File) => {
    try {
      const session = await ensureWorkbookSession();
      const workspaceKey = workspaceKeyForSessionId(session.id);
      const result = await importWorkspaceFile(file, session);
      setFiles((prev) =>
        prev.map((f) =>
          f.id === id ? { ...f, status: "success" as const, uploadResult: result, workspaceKey } : f
        )
      );
      if (result.path) {
        backfillPendingUploadFull(tokenMapRef.current, file.name, result.path);
        // Make a newly uploaded workbook available to both the Web tabs and
        // the native Sheet menu, even before the file sidebar has been opened.
        trackRecentExcelFile(result.path, result.filename, workspaceKey);
      }
    } catch (err) {
      const error = friendlyUploadError(err);
      setFiles((prev) =>
        prev.map((f) =>
          f.id === id ? { ...f, status: "failed" as const, error } : f
        )
      );
    }
  }, [tokenMapRef]);

  const hydrateWorkspaceImage = useCallback(async (id: string, path: string, filename: string) => {
    try {
      const scope = activeUploadScope();
      const blob = await fetchFileBlob(path, scope.sessionId, scope.workspaceId);
      const file = new File([blob], filename, { type: blob.type || "application/octet-stream" });
      setFiles((prev) =>
        prev.map((f) =>
          f.id === id
            ? {
                ...f,
                file,
                status: "success" as const,
                uploadResult: f.uploadResult
                  ? { ...f.uploadResult, size: blob.size }
                  : { filename, path, size: blob.size },
              }
            : f
        )
      );
    } catch (err) {
      const error = friendlyUploadError(err);
      setFiles((prev) =>
        prev.map((f) =>
          f.id === id ? { ...f, status: "failed" as const, error } : f
        )
      );
    }
  }, []);

  const retryUpload = useCallback(
    (id: string, file: File) => {
      setFiles((prev) => {
        const current = prev.find((f) => f.id === id);
        const next = prev.map((f) =>
          f.id === id ? { ...f, status: "uploading" as const, error: undefined } : f
        );
        if (current?.fromWorkspace && current.uploadResult) {
          void hydrateWorkspaceImage(id, current.uploadResult.path, current.uploadResult.filename);
        } else {
          void triggerUpload(id, file);
        }
        return next;
      });
    },
    [hydrateWorkspaceImage, triggerUpload]
  );

  const removeFile = useCallback((id: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  }, []);

  const insertFileMentions = useCallback((newFiles: File[]) => {
    const attached: AttachedFile[] = newFiles.map((f) => ({
      id: newAttachmentId(),
      file: f,
      status: "uploading" as const,
    }));
    setFiles((prev) => [...prev, ...attached]);
    for (const af of attached) {
      void triggerUpload(af.id, af.file);
    }
    insertDocMentions(newFiles.filter((f) => !isVisionImageUpload(f)).map((f) => mentionForPendingUpload(f.name)));
  }, [insertDocMentions, triggerUpload]);

  const attachWorkspaceFiles = useCallback((incoming: WorkspaceDroppedFile[]) => {
    if (incoming.length === 0) return;
    const existing = new Set(
      files.map((f) => attachmentIdentity(f.uploadResult?.path, f.file.name)),
    );
    const unique = incoming.filter(
      (file) => !existing.has(attachmentIdentity(file.path, file.filename)),
    );
    if (unique.length === 0) return;

    const attached: AttachedFile[] = unique.map((file) => {
      const image = isVisionImageUpload({ name: file.filename });
      return {
        id: newAttachmentId("ws-"),
        file: new File([], file.filename),
        status: image ? "uploading" as const : "success" as const,
        uploadResult: { filename: file.filename, path: file.path, size: 0 },
        fromWorkspace: true,
        workspaceKey: workspaceKeyForSessionId(useSessionStore.getState().activeSessionId),
      };
    });
    setFiles((prev) => [...prev, ...attached]);
    for (const af of attached) {
      const result = af.uploadResult!;
      if (isVisionImageUpload({ name: result.filename })) {
        void hydrateWorkspaceImage(af.id, result.path, result.filename);
      } else {
        trackRecentExcelFile(result.path, result.filename);
      }
    }
    insertDocMentions(
      unique.filter((file) => !isVisionImageUpload({ name: file.filename })).map((file) => workspaceFileMention(file)),
    );
  }, [files, hydrateWorkspaceImage, insertDocMentions]);

  const applySuggestionDraft = useCallback((draftText: string, newFiles: File[]) => {
    const attached: AttachedFile[] = newFiles.map((f) => ({
      id: newAttachmentId("sample-"),
      file: f,
      status: "uploading" as const,
    }));
    setFiles(attached);
    for (const af of attached) {
      void triggerUpload(af.id, af.file);
    }

    tokenMapRef.current.clear();
    const docFiles = newFiles.filter((f) => !isVisionImageUpload(f));
    let nextText = draftText.trim();
    if (docFiles.length > 0) {
      const displayTokens = toDisplayMentionTokens(
        docFiles.map((f) => mentionForPendingUpload(f.name)),
        tokenMapRef.current,
      );
      setConfirmedTokens(new Set(displayTokens));
      nextText = `${nextText} ${displayTokens.join(" ")}`.trim();
    } else {
      setConfirmedTokens(new Set());
    }
    setText(nextText);
    scheduleTextareaCursor(textareaRef.current, nextText.length);
  }, [triggerUpload, textareaRef, tokenMapRef, setConfirmedTokens, setText]);

  const triggerUrlUpload = useCallback(async (url: string) => {
    const filename = decodeURIComponent(url.split("/").pop()?.split("?")[0] || "file");
    const id = newAttachmentId("url-");
    const placeholder: AttachedFile = {
      id,
      file: new File([], filename),
      status: "uploading" as const,
    };
    setFiles((prev) => [...prev, placeholder]);

    try {
      const session = await ensureWorkbookSession();
      const workspaceKey = workspaceKeyForSessionId(session.id);
      const result = await uploadFileFromUrl(url, session.id, session.workspaceId);
      trackRecentExcelFile(result.path, result.filename, workspaceKey);
      setFiles((prev) =>
        prev.map((f) =>
          f.id === id ? { ...f, status: "success" as const, uploadResult: result, workspaceKey } : f
        )
      );
      if (!isVisionImageUpload({ name: result.filename })) {
        const mention = formatFileMention({ path: result.path });
        const displayTokens = toDisplayMentionTokens([mention], tokenMapRef.current);
        setConfirmedTokens((prev) => {
          const next = new Set(prev);
          displayTokens.forEach((token) => next.add(token));
          return next;
        });
        setText((prev) => {
          const replacement = displayTokens[0] ?? mention;
          const replaced = prev.replace(url, replacement);
          return replaced !== prev ? replaced : prev;
        });
      }
    } catch (err) {
      const error = friendlyUploadError(err);
      setFiles((prev) =>
        prev.map((f) =>
          f.id === id ? { ...f, status: "failed" as const, error } : f
        )
      );
    }
  }, [setConfirmedTokens, setText, tokenMapRef]);

  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const picked = clipboardFiles(e.clipboardData);
      if (picked.length > 0) {
        // 剪贴板携带文件（截图、文件管理器复制的文件、网页复制的图片等），
        // 复用「添加文件」上传管线：生成附件，非图片文件在文本中留下 @file 引用。
        const pastedText = e.clipboardData.getData("text/plain");
        if (!isClipboardFileMetadata(e.clipboardData, picked, pastedText)) {
          // 文件与文本并存（如 Excel 复制单元格会同时带上截图和 TSV）：
          // 不拦截默认粘贴，等文本落进输入框后再追加文件引用。
          setTimeout(() => insertFileMentions(picked), 0);
        } else {
          // 纯文件粘贴要拦下默认行为，避免浏览器把文件名等占位文本写入输入框。
          e.preventDefault();
          insertFileMentions(picked);
        }
        return;
      }
      const pasted = e.clipboardData.getData("text/plain");
      const fileUrl = detectFileUrl(pasted);
      if (fileUrl) {
        e.preventDefault();
        const textarea = textareaRef.current;
        const cursorPos = textarea?.selectionStart ?? text.length;
        const before = text.slice(0, cursorPos);
        const after = text.slice(textarea?.selectionEnd ?? cursorPos);
        setText(before + pasted + after);
        void triggerUrlUpload(fileUrl);
      }
    },
    [text, triggerUrlUpload, insertFileMentions, textareaRef, setText]
  );

  return {
    files,
    setFiles,
    getPreviewUrl,
    retryUpload,
    removeFile,
    insertFileMentions,
    attachWorkspaceFiles,
    applySuggestionDraft,
    handlePaste,
    hasUploadingFiles: files.some((af) => af.status === "uploading"),
    hasFailedFiles: files.some((af) => af.status === "failed"),
  };
}
