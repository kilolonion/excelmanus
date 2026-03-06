export async function mapWithConcurrency<T, R>(
  items: readonly T[],
  worker: (item: T, index: number) => Promise<R>,
  limit = 4,
): Promise<R[]> {
  if (items.length === 0) return [];
  const cap = Math.max(1, Math.floor(limit));
  const results = new Array<R>(items.length);
  let nextIndex = 0;

  async function runWorker(): Promise<void> {
    while (true) {
      const current = nextIndex;
      nextIndex += 1;
      if (current >= items.length) return;
      results[current] = await worker(items[current], current);
    }
  }

  const workers = Array.from({ length: Math.min(cap, items.length) }, () => runWorker());
  await Promise.all(workers);
  return results;
}

export async function forEachWithConcurrency<T>(
  items: readonly T[],
  worker: (item: T, index: number) => Promise<void>,
  limit = 4,
): Promise<void> {
  await mapWithConcurrency(items, worker, limit);
}
