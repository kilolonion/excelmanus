/**
 * 凭据加密工具。
 *
 * 使用 Web Crypto API (AES-GCM) 加密保存的密码，
 * 加密密钥存储在 IndexedDB 中（不可见于 localStorage 面板），
 * 即使 localStorage 内容泄露，密码也不会以明文形式暴露。
 *
 * 兼容规则：
 * - 带 `enc:` 前缀的是加密密码
 * - 不带前缀的视为旧版明文，解密时原样返回（兼容迁移）
 */

import { createStore, get, set } from "idb-keyval";

const keyStore = createStore("excelmanus-keystore", "crypto-keys");
const KEY_ID = "credential-key";
const CREDENTIAL_PREFIX = "enc:";
const FALLBACK_PREFIX = "b64:";

async function getOrCreateKey(): Promise<CryptoKey> {
  try {
    const existing = await get<CryptoKey>(KEY_ID, keyStore);
    if (existing) return existing;
  } catch {
    // IndexedDB 不可用或损坏，后续会生成新 key
  }

  const key = await crypto.subtle.generateKey(
    { name: "AES-GCM", length: 256 },
    false, // 不可导出
    ["encrypt", "decrypt"],
  );

  try {
    await set(KEY_ID, key, keyStore);
  } catch {
    // 写入失败（隐私模式等），key 仅在内存中可用
  }

  return key;
}

/**
 * 加密明文密码，返回 `enc:<base64>` 格式的密文。
 */
export async function encryptCredential(plaintext: string): Promise<string> {
  try {
    const key = await getOrCreateKey();
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const encoded = new TextEncoder().encode(plaintext);
    const ciphertext = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv },
      key,
      encoded,
    );
    const ctBytes = new Uint8Array(ciphertext);
    const combined = new Uint8Array(iv.length + ctBytes.length);
    combined.set(iv);
    combined.set(ctBytes, iv.length);
    return CREDENTIAL_PREFIX + btoa(String.fromCharCode(...combined));
  } catch {
    // Web Crypto 不可用（极端环境），回退为 base64 混淆（使用不同前缀以便解密时区分）
    return FALLBACK_PREFIX + btoa(unescape(encodeURIComponent(plaintext)));
  }
}

/**
 * 解密已加密的密码。
 *
 * - 带 `enc:` 前缀 → AES-GCM 解密
 * - 不带前缀 → 视为旧版明文，原样返回（兼容迁移）
 * - 解密失败 → 返回 null（密钥丢失或数据损坏）
 */
export async function decryptCredential(
  stored: string,
): Promise<string | null> {
  if (!stored) return null;

  // base64 降级（Web Crypto 不可用时生成）— 直接解码
  if (stored.startsWith(FALLBACK_PREFIX)) {
    try {
      return decodeURIComponent(escape(atob(stored.slice(FALLBACK_PREFIX.length))));
    } catch {
      return null;
    }
  }

  // 旧版明文（无前缀）— 原样返回以兼容迁移
  if (!stored.startsWith(CREDENTIAL_PREFIX)) {
    return stored;
  }

  try {
    const key = await getOrCreateKey();
    const b64 = stored.slice(CREDENTIAL_PREFIX.length);
    const combined = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const iv = combined.slice(0, 12);
    const ciphertext = combined.slice(12);
    const decrypted = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv },
      key,
      ciphertext,
    );
    return new TextDecoder().decode(decrypted);
  } catch {
    return null; // 密钥丢失或数据损坏
  }
}
