import { get, set, del, keys } from "idb-keyval";
import type { Message } from "./types";

const PREFIX = "chat_msgs_";
const MAX_CACHED_SESSIONS = 50;

export interface CachedMessagesV2 {
  version: 2;
  messages: Message[];
  messageOrder: string[];
  messagesById: Record<string, Message>;
}

function _buildCachedPayload(messages: Message[]): CachedMessagesV2 {
  const normalized: Message[] = [];
  const messageOrder: string[] = [];
  const messagesById: Record<string, Message> = {};
  for (const msg of messages) {
    const msgId = String(msg.id || "").trim();
    if (!msgId) continue;
    if (messagesById[msgId]) {
      messagesById[msgId] = msg;
      const idx = messageOrder.indexOf(msgId);
      if (idx >= 0) normalized[idx] = msg;
      continue;
    }
    messageOrder.push(msgId);
    messagesById[msgId] = msg;
    normalized.push(msg);
  }
  return {
    version: 2,
    messages: normalized,
    messageOrder,
    messagesById,
  };
}

function _normalizeCachedPayload(raw: CachedMessagesV2): CachedMessagesV2 {
  if (!Array.isArray(raw.messages)) return _buildCachedPayload([]);
  return _buildCachedPayload(raw.messages);
}

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
  const key = `${PREFIX}${sessionId}`;
  try {
    const raw = await get<unknown>(key);
    if (!raw) return null;
    if (Array.isArray(raw)) {
      const migrated = _buildCachedPayload(raw as Message[]);
      set(key, migrated).catch(async () => {
        await del(key).catch(() => {});
      });
      return migrated.messages;
    }
    if (typeof raw === "object" && raw !== null && (raw as { version?: number }).version === 2) {
      return _normalizeCachedPayload(raw as CachedMessagesV2).messages;
    }
    await del(key).catch(() => {});
    return null;
  } catch {
    await del(key).catch(() => {});
    return null;
  }
}

export async function saveCachedMessages(sessionId: string, messages: Message[]): Promise<void> {
  try {
    await set(`${PREFIX}${sessionId}`, _buildCachedPayload(messages));
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
