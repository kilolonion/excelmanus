"use client";

import { useCallback, useEffect, useRef, useState, type Dispatch, type MutableRefObject, type RefObject, type SetStateAction } from "react";
import { uploadFile, uploadFileFromUrl } from "@/lib/api";
import type { AttachedFile } from "@/lib/types";
import { detectFileUrl, friendlyUploadError, isImageFile } from "./chat-input-constants";
import { insertTokensIntoText, scheduleTextareaCursor, toDisplayMentionTokens } from "./chat-input-insert";

interface UseChatUploadOptions {
  text: string;
  setText: Dispatch<SetStateAction<string>>;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  tokenMapRef: MutableRefObject<Map<string, string>>;
  setConfirmedTokens: Dispatch<SetStateAction<Set<string>>>;
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

  const retryUpload = useCallback(
    (id: string, file: File) => {
      setFiles((prev) =>
        prev.map((f) =>
          f.id === id ? { ...f, status: "uploading" as const, error: undefined } : f
        )
      );
      triggerUpload(id, file);
    },
    [triggerUpload]
  );

  const removeFile = useCallback((id: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  }, []);

  const insertFileMentions = useCallback((newFiles: File[]) => {
    const attached: AttachedFile[] = newFiles.map((f) => ({
      id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      file: f,
      status: "uploading" as const,
    }));
    setFiles((prev) => [...prev, ...attached]);
    for (const af of attached) {
      triggerUpload(af.id, af.file);
    }
    const docFiles = newFiles.filter((f) => !isImageFile(f.name));
    if (docFiles.length > 0) {
      const displayTokens = toDisplayMentionTokens(
        docFiles.map((f) => `@${f.name}`),
        tokenMapRef.current,
      );
      setConfirmedTokens((prev) => {
        const next = new Set(prev);
        displayTokens.forEach((token) => next.add(token));
        return next;
      });
      const textarea = textareaRef.current;
      const cursorPos = textarea?.selectionStart ?? text.length;
      const { newText, newCursorPos } = insertTokensIntoText(text, cursorPos, displayTokens);
      setText(newText);
      scheduleTextareaCursor(textarea, newCursorPos);
    }
  }, [text, triggerUpload, textareaRef, tokenMapRef, setConfirmedTokens, setText]);

  const applySuggestionDraft = useCallback((draftText: string, newFiles: File[]) => {
    const attached: AttachedFile[] = newFiles.map((f) => ({
      id: `sample-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      file: f,
      status: "uploading" as const,
    }));
    setFiles(attached);
    for (const af of attached) {
      triggerUpload(af.id, af.file);
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
    const id = `${Date.now()}-url-${Math.random().toString(36).slice(2)}`;
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
        triggerUrlUpload(fileUrl);
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
    applySuggestionDraft,
    handlePaste,
    hasUploadingFiles: files.some((af) => af.status === "uploading"),
    hasFailedFiles: files.some((af) => af.status === "failed"),
  };
}
