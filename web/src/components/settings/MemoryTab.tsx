"use client";

import { useEffect, useState, useCallback } from "react";
import { Trash2, Loader2, Brain, ChevronDown, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { apiGet, apiDelete } from "@/lib/api";

interface MemoryEntry {
  id: string;
  content: string;
  category: string;
  timestamp: string;
  source: string;
}

const CATEGORY_LABELS: Record<string, string> = {
  file_pattern: "文件结构",
  user_pref: "用户偏好",
  error_solution: "错误解决方案",
  general: "通用",
};

const CATEGORY_COLORS: Record<string, string> = {
  file_pattern: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
  user_pref: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
  error_solution: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
  general: "bg-gray-100 text-gray-700 dark:bg-gray-900 dark:text-gray-300",
};

const CATEGORIES = ["file_pattern", "user_pref", "error_solution", "general"] as const;
const CONTENT_PREVIEW_LEN = 80;

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return ts;
  }
}

export function MemoryTab() {
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const fetchEntries = useCallback(async () => {
    setLoading(true);
    try {
      const url = categoryFilter ? `/memory?category=${encodeURIComponent(categoryFilter)}` : "/memory";
      const data = await apiGet<MemoryEntry[]>(url);
      setEntries(Array.isArray(data) ? data : []);
    } catch {
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, [categoryFilter]);

  useEffect(() => {
    fetchEntries();
  }, [fetchEntries]);

  const toggleExpand = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (!confirm("确定删除这条记忆？")) return;
    setDeletingId(id);
    try {
      await apiDelete(`/memory/${encodeURIComponent(id)}`);
      setEntries((prev) => prev.filter((ent) => ent.id !== id));
    } catch (err) {
      alert(err instanceof Error ? err.message : "删除失败");
    } finally {
      setDeletingId(null);
    }
  };

  const filteredEntries = entries;
  const groupedByCategory = categoryFilter
    ? { [categoryFilter]: filteredEntries }
    : filteredEntries.reduce<Record<string, MemoryEntry[]>>((acc, e) => {
        (acc[e.category] = acc[e.category] || []).push(e);
        return acc;
      }, {});

  const orderedCategories = categoryFilter ? [categoryFilter] : CATEGORIES.filter((c) => groupedByCategory[c]?.length);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs text-muted-foreground">查看与管理 Agent 持久记忆</p>
      </div>

      {/* Category filter chips */}
      <div className="flex flex-wrap gap-1.5">
        <Button
          size="sm"
          variant={categoryFilter === null ? "default" : "outline"}
          className="h-7 text-xs px-2.5"
          onClick={() => setCategoryFilter(null)}
        >
          全部
        </Button>
        {CATEGORIES.map((cat) => (
          <Button
            key={cat}
            size="sm"
            variant={categoryFilter === cat ? "default" : "outline"}
            className="h-7 text-xs px-2.5"
            onClick={() => setCategoryFilter(cat)}
          >
            {CATEGORY_LABELS[cat] ?? cat}
          </Button>
        ))}
      </div>

      {/* Memory list grouped by category */}
      <div className="space-y-3">
        {orderedCategories.length === 0 && (
          <p className="text-xs text-muted-foreground text-center py-8">
            Agent 尚未记录任何记忆
          </p>
        )}
        {orderedCategories.map((cat) => (
          <div key={cat} className="space-y-1.5">
            {!categoryFilter && (
              <div className="flex items-center gap-1.5 px-1">
                <Badge
                  className={`text-[10px] px-1.5 py-0 ${CATEGORY_COLORS[cat] ?? ""}`}
                  variant="secondary"
                >
                  {CATEGORY_LABELS[cat] ?? cat}
                </Badge>
                <span className="text-[10px] text-muted-foreground">
                  ({groupedByCategory[cat]?.length ?? 0})
                </span>
              </div>
            )}
            {(groupedByCategory[cat] ?? []).map((entry) => {
              const isExpanded = expandedId === entry.id;
              const isLong = entry.content.length > CONTENT_PREVIEW_LEN;
              const preview = isLong ? `${entry.content.slice(0, CONTENT_PREVIEW_LEN)}…` : entry.content;

              return (
                <div
                  key={entry.id}
                  className="rounded-lg border border-border"
                >
                  <div
                    className="flex items-start gap-2 px-3 py-2.5 cursor-pointer hover:bg-muted/50 transition-colors"
                    onClick={() => toggleExpand(entry.id)}
                  >
                    {isLong ? (
                      isExpanded ? (
                        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0 mt-0.5" />
                      ) : (
                        <ChevronRight className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0 mt-0.5" />
                      )
                    ) : (
                      <Brain className="h-3.5 w-3.5 flex-shrink-0 mt-0.5" style={{ color: "var(--em-primary)" }} />
                    )}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-foreground">
                        {isExpanded ? entry.content : preview}
                      </p>
                      <div className="flex items-center gap-2 mt-1 flex-wrap">
                        <span className="text-[10px] text-muted-foreground">
                          {formatTimestamp(entry.timestamp)}
                        </span>
                        {categoryFilter && (
                          <Badge
                            className={`text-[10px] px-1.5 py-0 ${CATEGORY_COLORS[entry.category] ?? ""}`}
                            variant="secondary"
                          >
                            {CATEGORY_LABELS[entry.category] ?? entry.category}
                          </Badge>
                        )}
                        {entry.source && (
                          <span className="text-[10px] text-muted-foreground">{entry.source}</span>
                        )}
                      </div>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 text-destructive flex-shrink-0"
                      disabled={deletingId === entry.id}
                      onClick={(e) => handleDelete(e, entry.id)}
                    >
                      {deletingId === entry.id ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Trash2 className="h-3 w-3" />
                      )}
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
