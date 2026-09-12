"use client";

import { useCallback, useEffect, useRef, useState, type Dispatch, type MutableRefObject, type RefObject, type SetStateAction } from "react";
import { fetchFileBlob, uploadFile, uploadFileFromUrl } from "@/lib/api";
import { identityKey, toPublicFileIdentity } from "@/lib/file-identity";
import type { AttachedFile } from "@/lib/types";
import { getActiveSessionId } from "@/stores/session-store";
import { detectFileUrl, friendlyUploadError, isImageFile } from "./chat-input-constants";
import {
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
      const result = await uploadFile(file);
      setFiles((prev) =>
        prev.map((f) =>
          f.id === id ? { ...f, status: "success" as const, uploadResult: result } : f
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

  const hydrateWorkspaceImage = useCallback(async (id: string, path: string, filename: string) => {
    try {
      const blob = await fetchFileBlob(path, getActiveSessionId() ?? undefined);
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
    insertDocMentions(newFiles.filter((f) => !isImageFile(f.name)).map((f) => `@${f.name}`));
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
      const image = isImageFile(file.filename);
      return {
        id: newAttachmentId("ws-"),
        file: new File([], file.filename),
        status: image ? "uploading" as const : "success" as const,
        uploadResult: { filename: file.filename, path: file.path, size: 0 },
        fromWorkspace: true,
      };
    });
    setFiles((prev) => [...prev, ...attached]);
    for (const af of attached) {
      const result = af.uploadResult!;
      if (isImageFile(result.filename)) {
        void hydrateWorkspaceImage(af.id, result.path, result.filename);
      } else {
        trackRecentExcelFile(result.path, result.filename);
      }
    }
    insertDocMentions(
      unique.filter((file) => !isImageFile(file.filename)).map((file) => workspaceFileMention(file)),
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
    const docFiles = newFiles.filter((f) => !isImageFile(f.name));
    let nextText = draftText.trim();
    if (docFiles.length > 0) {
      const displayTokens = toDisplayMentionTokens(
        docFiles.map((f) => `@${f.name}`),
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
      const result = await uploadFileFromUrl(url);
      setFiles((prev) =>
        prev.map((f) =>
          f.id === id ? { ...f, status: "success" as const, uploadResult: result } : f
        )
      );
      if (!isImageFile(result.filename)) {
        setConfirmedTokens((prev) => new Set(prev).add(`@${result.filename}`));
        setText((prev) => {
          const replaced = prev.replace(url, `@${result.filename}`);
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
  }, [setConfirmedTokens, setText]);

  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
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
    [text, triggerUrlUpload, textareaRef, setText]
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
