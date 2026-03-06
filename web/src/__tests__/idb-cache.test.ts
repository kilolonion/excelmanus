/**
 * idb-cache.ts 单元测试
 *
 * 覆盖：
 * - 旧格式（裸 Message[]）→ V2 自动迁移
 * - V2 格式正常加载
 * - 未知格式 → 自动清理不卡死
 * - 异常 → 安全回退
 * - _buildCachedPayload 去重 / 空 ID 过滤
 * - saveCachedMessages + evictOldest
 * - deleteCachedMessages
 * - clearAllCachedMessages
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import type { Message } from "@/lib/types";

// ---------------------------------------------------------------------------
// Mock idb-keyval — 用内存 Map 模拟 IndexedDB
// ---------------------------------------------------------------------------
const store = new Map<string, unknown>();

vi.mock("idb-keyval", () => ({
  get: vi.fn(async (key: string) => store.get(key)),
  set: vi.fn(async (key: string, value: unknown) => {
    store.set(key, value);
  }),
  del: vi.fn(async (key: string) => {
    store.delete(key);
  }),
  keys: vi.fn(async () => Array.from(store.keys())),
}));

// 在 mock 之后才 import 被测模块
import {
  loadCachedMessages,
  saveCachedMessages,
  deleteCachedMessages,
  clearAllCachedMessages,
} from "@/lib/idb-cache";
import { get, set, del, keys } from "idb-keyval";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeUserMsg(id: string, content = "hi"): Message {
  return { id, role: "user", content, timestamp: Date.now() };
}

function makeAssistantMsg(id: string): Message {
  return { id, role: "assistant", blocks: [], timestamp: Date.now() };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("idb-cache", () => {
  beforeEach(() => {
    store.clear();
    vi.clearAllMocks();
  });

  // ── loadCachedMessages ────────────────────────────────────────

  describe("loadCachedMessages", () => {
    it("返回 null 当 key 不存在", async () => {
      const result = await loadCachedMessages("nonexistent");
      expect(result).toBeNull();
    });

    it("旧格式（裸 Message[]）自动迁移为 V2", async () => {
      const oldFormat: Message[] = [
        makeUserMsg("u1", "hello"),
        makeAssistantMsg("a1"),
      ];
      store.set("chat_msgs_session1", oldFormat);

      const result = await loadCachedMessages("session1");

      // 返回消息正确
      expect(result).not.toBeNull();
      expect(result!.length).toBe(2);
      expect(result![0].id).toBe("u1");
      expect(result![1].id).toBe("a1");

      // 验证已迁移写回 V2 格式
      expect(set).toHaveBeenCalledWith(
        "chat_msgs_session1",
        expect.objectContaining({ version: 2 }),
      );
    });

    it("V2 格式正常加载", async () => {
      const msgs: Message[] = [makeUserMsg("u1"), makeAssistantMsg("a1")];
      const v2Payload = {
        version: 2,
        messages: msgs,
        messageOrder: ["u1", "a1"],
        messagesById: { u1: msgs[0], a1: msgs[1] },
      };
      store.set("chat_msgs_s2", v2Payload);

      const result = await loadCachedMessages("s2");
      expect(result).not.toBeNull();
      expect(result!.length).toBe(2);
      expect(result![0].id).toBe("u1");
    });

    it("V2 格式带重复 ID 时自动去重", async () => {
      const msg1 = makeUserMsg("dup", "first");
      const msg2 = makeUserMsg("dup", "second");
      const v2Payload = {
        version: 2,
        messages: [msg1, msg2],
        messageOrder: ["dup"],
        messagesById: { dup: msg2 },
      };
      store.set("chat_msgs_s3", v2Payload);

      const result = await loadCachedMessages("s3");
      expect(result!.length).toBe(1);
      expect((result![0] as any).content).toBe("second");
    });

    it("未知格式 → del(key) + 返回 null", async () => {
      store.set("chat_msgs_bad", { version: 99, data: "corrupted" });

      const result = await loadCachedMessages("bad");
      expect(result).toBeNull();
      expect(del).toHaveBeenCalledWith("chat_msgs_bad");
    });

    it("非对象非数组 → del(key) + 返回 null", async () => {
      store.set("chat_msgs_str", "just a string");

      const result = await loadCachedMessages("str");
      expect(result).toBeNull();
      expect(del).toHaveBeenCalledWith("chat_msgs_str");
    });

    it("get 抛异常 → del(key) + 返回 null 不卡死", async () => {
      vi.mocked(get).mockRejectedValueOnce(new Error("IDB corrupt"));

      const result = await loadCachedMessages("crash");
      expect(result).toBeNull();
      // 应该尝试清理
      expect(del).toHaveBeenCalledWith("chat_msgs_crash");
    });

    it("旧格式迁移写回失败时执行 del 清理", async () => {
      const oldFormat: Message[] = [makeUserMsg("u1")];
      store.set("chat_msgs_migfail", oldFormat);

      // 让 set 写回失败
      vi.mocked(set).mockRejectedValueOnce(new Error("quota exceeded"));

      const result = await loadCachedMessages("migfail");
      // 仍然返回迁移后的消息（因为迁移在内存中成功了）
      expect(result).not.toBeNull();
      expect(result!.length).toBe(1);
      // 写回失败后应尝试 del 清理
      expect(del).toHaveBeenCalledWith("chat_msgs_migfail");
    });
  });

  // ── saveCachedMessages ────────────────────────────────────────

  describe("saveCachedMessages", () => {
    it("保存消息为 V2 格式", async () => {
      const msgs: Message[] = [makeUserMsg("u1"), makeAssistantMsg("a1")];
      await saveCachedMessages("s1", msgs);

      const stored = store.get("chat_msgs_s1") as any;
      expect(stored).toBeDefined();
      expect(stored.version).toBe(2);
      expect(stored.messages.length).toBe(2);
      expect(stored.messageOrder).toEqual(["u1", "a1"]);
      expect(stored.messagesById["u1"]).toBeDefined();
      expect(stored.messagesById["a1"]).toBeDefined();
    });

    it("保存时自动去重", async () => {
      const msgs: Message[] = [
        makeUserMsg("dup", "first"),
        makeUserMsg("dup", "second"),
      ];
      await saveCachedMessages("s2", msgs);

      const stored = store.get("chat_msgs_s2") as any;
      expect(stored.messages.length).toBe(1);
      expect(stored.messagesById["dup"].content).toBe("second");
    });

    it("保存时过滤空 ID", async () => {
      const msgs: Message[] = [
        { id: "", role: "user", content: "no id" } as Message,
        makeUserMsg("valid"),
      ];
      await saveCachedMessages("s3", msgs);

      const stored = store.get("chat_msgs_s3") as any;
      expect(stored.messages.length).toBe(1);
      expect(stored.messageOrder).toEqual(["valid"]);
    });
  });

  // ── evictOldest（通过 saveCachedMessages 间接触发）─────────

  describe("evictOldest", () => {
    it("超过 50 个会话时淘汰最早的", async () => {
      // 先填充 51 个会话
      for (let i = 0; i < 51; i++) {
        store.set(`chat_msgs_sess_${String(i).padStart(3, "0")}`, {
          version: 2,
          messages: [],
          messageOrder: [],
          messagesById: {},
        });
      }

      // 触发 eviction
      await saveCachedMessages("new_sess", [makeUserMsg("u1")]);

      // keys() 被调用来检测数量
      expect(keys).toHaveBeenCalled();
      // 应该有删除操作（淘汰超出部分）
      expect(del).toHaveBeenCalled();
    });
  });

  // ── deleteCachedMessages ──────────────────────────────────────

  describe("deleteCachedMessages", () => {
    it("删除指定会话缓存", async () => {
      store.set("chat_msgs_target", { version: 2, messages: [] });
      await deleteCachedMessages("target");
      expect(store.has("chat_msgs_target")).toBe(false);
    });

    it("删除不存在的 key 不报错", async () => {
      await expect(deleteCachedMessages("nonexist")).resolves.toBeUndefined();
    });
  });

  // ── clearAllCachedMessages ────────────────────────────────────

  describe("clearAllCachedMessages", () => {
    it("清除所有 chat_msgs_ 前缀的 key", async () => {
      store.set("chat_msgs_a", { version: 2 });
      store.set("chat_msgs_b", { version: 2 });
      store.set("other_key", "keep me");

      await clearAllCachedMessages();

      expect(store.has("chat_msgs_a")).toBe(false);
      expect(store.has("chat_msgs_b")).toBe(false);
      expect(store.has("other_key")).toBe(true); // 不删除非前缀 key
    });
  });
});
