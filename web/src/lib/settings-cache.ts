/**
 * 模块级缓存，用于设置页 Tab 切换时避免重复 API 请求。
 * 数据仅在用户显式保存/删除后通过 force-refetch 刷新。
 */
const _cache = new Map<string, unknown>();

export const settingsCache = {
  get<T>(key: string): T | undefined {
    return _cache.get(key) as T | undefined;
  },
  set(key: string, data: unknown) {
    _cache.set(key, data);
  },
  /** 删除指定 key */
  delete(key: string) {
    _cache.delete(key);
  },
  /** 删除所有以 prefix 开头的 key */
  invalidatePrefix(prefix: string) {
    for (const key of _cache.keys()) {
      if (key.startsWith(prefix)) _cache.delete(key);
    }
  },
  clear() {
    _cache.clear();
  },
};
