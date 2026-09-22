export type DropSide = "before" | "after";

/** Resolve the target after removing the source, including adjacent moves. */
export function moveSidebarItem<T extends { id: string }>(
  items: T[], sourceId: string, targetId: string, side: DropSide,
): T[] {
  if (sourceId === targetId) return items;
  const source = items.find((item) => item.id === sourceId);
  if (!source || !items.some((item) => item.id === targetId)) return items;
  const next = items.filter((item) => item.id !== sourceId);
  const target = next.findIndex((item) => item.id === targetId);
  next.splice(target + (side === "after" ? 1 : 0), 0, source);
  return next.every((item, index) => item === items[index]) ? items : next;
}

/** Newly created conversations stay at the top; saved conversations keep their order. */
export function applySidebarOrder<T extends { id: string }>(items: T[], order: string[] = []): T[] {
  if (!order.length) return items;
  const positions = new Map(order.map((id, index) => [id, index]));
  return [...items].sort((a, b) =>
    (positions.get(a.id) ?? -1) - (positions.get(b.id) ?? -1));
}
