import { get, set, del, keys } from "idb-keyval";
import type { Message } from "./types";

const PREFIX = "chat_msgs_";
const MAX_CACHED_SESSIONS = 50;

/** 清空 IndexedDB 中所有会话消息缓存。 */
export async function clearAllCachedMessages(): Promise<void> {
  try {
    const allKeys = (await keys()).filter(
      (k) => typeof k === "string" && (k as string).startsWith(PREFIX)
    );
    for (const key of allKeys) {
      await del(key);
    }
  } catch {
    // 静默忽略
  }
}

export async function loadCachedMessages(sessionId: string): Promise<Message[] | null> {
  try {
    return (await get<Message[]>(`${PREFIX}${sessionId}`)) ?? null;
  } catch {
    return null;
  }
}

export async function saveCachedMessages(sessionId: string, messages: Message[]): Promise<void> {
  try {
    await set(`${PREFIX}${sessionId}`, messages);
    await evictOldest();
  } catch {
    // 静默忽略
  }
}

export async function deleteCachedMessages(sessionId: string): Promise<void> {
  try {
    await del(`${PREFIX}${sessionId}`);
  } catch {
    // 静默忽略
  }
}

async function evictOldest(): Promise<void> {
  const allKeys = (await keys()).filter(
    (k) => typeof k === "string" && (k as string).startsWith(PREFIX)
  );
  if (allKeys.length <= MAX_CACHED_SESSIONS) return;
  const toDelete = allKeys.slice(0, allKeys.length - MAX_CACHED_SESSIONS);
  for (const key of toDelete) {
    await del(key);
  }
}
