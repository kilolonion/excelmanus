/**
 * 聊天输入框文件上传的桥接注册表。
 * ChatInput 挂载时注册 insertFileMentions，桌面菜单「上传文件」等
 * 输入框外部的入口通过 uploadChatFiles 复用同一上传管线。
 */

type ChatFileUploadHandler = (files: File[]) => void;

let handler: ChatFileUploadHandler | null = null;

export function registerChatFileUpload(fn: ChatFileUploadHandler): () => void {
  handler = fn;
  return () => {
    if (handler === fn) handler = null;
  };
}

export function uploadChatFiles(files: File[]): boolean {
  if (!handler || files.length === 0) return false;
  handler(files);
  return true;
}
