/** Nest Code Mode SDK subcalls under their parent run_code card. */

export type ToolCallTreeItem = {
  toolCallId?: string;
  parentCallId?: string;
};

export type NestedToolCall<T extends ToolCallTreeItem> = {
  item: T;
  children: T[];
};

export function nestToolCallsByParent<T extends ToolCallTreeItem>(
  items: T[],
): NestedToolCall<T>[] {
  const ids = new Set(
    items.map((item) => item.toolCallId).filter((id): id is string => Boolean(id)),
  );
  const childrenByParent = new Map<string, T[]>();
  const roots: T[] = [];
  for (const item of items) {
    const parent = item.parentCallId;
    if (parent && ids.has(parent) && parent !== item.toolCallId) {
      const list = childrenByParent.get(parent) ?? [];
      list.push(item);
      childrenByParent.set(parent, list);
    } else {
      roots.push(item);
    }
  }
  return roots.map((item) => ({
    item,
    children: item.toolCallId ? (childrenByParent.get(item.toolCallId) ?? []) : [],
  }));
}
