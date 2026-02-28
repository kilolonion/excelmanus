"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import { Trash2, Loader2, Brain, ChevronRight, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { apiGet, apiDelete } from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";

interface MemoryEntry {
  id: string;
  content: string;
  category: string;
  timestamp: string;
  source: string;
}

const CATEGORY_LABELS: Record<string, string> = {
  all: "全部",
  file_pattern: "文件结构",
  user_pref: "用户偏好",
  error_solution: "错误方案",
  general: "通用",
};

const CATEGORY_COLORS: Record<string, string> = {
  file_pattern: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  user_pref: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  error_solution: "bg-rose-500/15 text-rose-600 dark:text-rose-400",
  general: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400",
};

const CATEGORIES = ["file_pattern", "user_pref", "error_solution", "general"] as const;
const CONTENT_PREVIEW_LEN = 100;

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

  const categoryCounts = useMemo(() => {
    const counts: Record<string, number> = { all: entries.length };
    for (const e of entries) counts[e.category] = (counts[e.category] || 0) + 1;
    return counts;
  }, [entries]);

  const fetchEntries = useCallback(async (force = false) => {
    const url = "/memory";
    if (!force) {
      const cached = settingsCache.get<MemoryEntry[]>(url);
      if (cached) { setEntries(cached); return; }
    }
    setLoading(true);
    try {
      const data = await apiGet<MemoryEntry[]>(url);
      const items = Array.isArray(data) ? data : [];
      settingsCache.set(url, items);
      setEntries(items);
    } catch {
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, []);

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
      settingsCache.invalidatePrefix("/memory");
      setEntries((prev) => prev.filter((ent) => ent.id !== id));
    } catch (err) {
      alert(err instanceof Error ? err.message : "删除失败");
    } finally {
      setDeletingId(null);
    }
  };

  const filteredEntries = categoryFilter ? entries.filter((e) => e.category === categoryFilter) : entries;
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
    <div className="space-y-3">

      {/* ── Category filter pills ── */}
      <div className="flex items-center gap-1 overflow-x-auto scrollbar-none">
        {([null, ...CATEGORIES] as const).map((cat) => {
          const key = cat ?? "all";
          const isActive = categoryFilter === cat;
          const count = cat ? (categoryCounts[cat] || 0) : categoryCounts.all;
          if (cat && count === 0) return null;
          return (
            <button
              key={key}
              type="button"
              className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium transition-colors whitespace-nowrap border ${
                isActive
                  ? "text-white border-transparent"
                  : "border-border text-muted-foreground hover:bg-muted/60 hover:text-foreground"
              }`}
              style={isActive ? { backgroundColor: "var(--em-primary)" } : undefined}
              onClick={() => setCategoryFilter(cat)}
            >
              {CATEGORY_LABELS[key] ?? key}
              <span className={`text-[10px] ${isActive ? "text-white/70" : "text-muted-foreground/60"}`}>
                {count}
              </span>
            </button>
          );
        })}
      </div>

      {/* ── Memory list grouped by category ── */}
      <div className="space-y-3">
        {orderedCategories.length === 0 && (
          <div className="text-center py-8">
            <Brain className="h-8 w-8 mx-auto mb-2 text-muted-foreground/30" />
            <p className="text-xs text-muted-foreground">
              {categoryFilter ? `暂无「${CATEGORY_LABELS[categoryFilter] ?? categoryFilter}」类记忆` : "Agent 尚未记录任何记忆"}
            </p>
          </div>
        )}
        {orderedCategories.map((cat) => (
          <div key={cat} className="space-y-1.5">
            {!categoryFilter && (
              <div className="flex items-center gap-1.5 px-0.5 pt-1">
                <Badge
                  className={`text-[10px] px-1.5 py-0 border-0 ${CATEGORY_COLORS[cat] ?? "bg-muted text-muted-foreground"}`}
                  variant="secondary"
                >
                  {CATEGORY_LABELS[cat] ?? cat}
                </Badge>
                <span className="text-[10px] text-muted-foreground/60">
                  {groupedByCategory[cat]?.length ?? 0}
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
                  className="rounded-lg border border-border overflow-hidden"
                >
                  {/* ── Summary row ── */}
                  <div
                    className="flex items-start gap-2 px-3 py-2.5 cursor-pointer hover:bg-muted/30 transition-colors"
                    onClick={() => toggleExpand(entry.id)}
                  >
                    <span
                      className="text-muted-foreground transition-transform flex-shrink-0 mt-0.5"
                      style={{ transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)" }}
                    >
                      <ChevronRight className="h-3.5 w-3.5" />
                    </span>
                    <Brain className="h-3.5 w-3.5 flex-shrink-0 mt-0.5" style={{ color: "var(--em-primary)" }} />
                    <div className="flex-1 min-w-0">
                      <p className="text-[13px] text-foreground leading-relaxed">
                        {isExpanded ? entry.content : preview}
                      </p>
                      <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                        <span className="text-[10px] text-muted-foreground/70">
                          {formatTimestamp(entry.timestamp)}
                        </span>
                        {categoryFilter && (
                          <Badge
                            className={`text-[9px] px-1.5 py-0 border-0 ${CATEGORY_COLORS[entry.category] ?? "bg-muted text-muted-foreground"}`}
                            variant="secondary"
                          >
                            {CATEGORY_LABELS[entry.category] ?? entry.category}
                          </Badge>
                        )}
                        {entry.source && (
                          <span className="text-[10px] text-muted-foreground/50 font-mono">{entry.source}</span>
                        )}
                      </div>
                    </div>
                    <div className="flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 text-destructive"
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
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
