"use client";

import { useEffect, useState, useCallback } from "react";
import { Plus, Trash2, Loader2, ScrollText, ToggleLeft, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { apiGet, apiPost, apiPatch, apiDelete } from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";
import { isSettingsDemoActive, onSettingsDemoChange, DEMO_RULES } from "@/components/onboarding/demo-settings";

interface Rule {
  id: string;
  content: string;
  enabled: boolean;
  created_at: string;
}

interface RulesTabProps {
  sessionId?: string;
}

function RuleRow({
  rule,
  onToggle,
  onDelete,
  updating,
}: {
  rule: Rule;
  onToggle: (rule: Rule) => void;
  onDelete: (rule: Rule) => void;
  updating: string | null;
}) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-border px-3 py-2.5 sm:py-2">
      <div className="flex-1 min-w-0">
        <p className="text-sm break-words">{rule.content}</p>
      </div>
      <div className="flex items-center gap-1.5 flex-shrink-0">
        <Switch
          checked={rule.enabled}
          onCheckedChange={() => onToggle(rule)}
          disabled={updating === rule.id}
        />
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 text-destructive"
          onClick={() => onDelete(rule)}
          disabled={updating === rule.id}
        >
          {updating === rule.id ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Trash2 className="h-3 w-3" />
          )}
        </Button>
      </div>
    </div>
  );
}

function DemoRulesBanner() {
  const [active, setActive] = useState(false);
  useEffect(() => {
    setActive(isSettingsDemoActive());
    return onSettingsDemoChange(() => setActive(isSettingsDemoActive()));
  }, []);
  if (!active) return null;
  return (
    <div className="space-y-1.5">
      {DEMO_RULES.map((rule) => (
        <div
          key={rule.id}
          className="flex items-center gap-2 rounded-lg border border-dashed border-[var(--em-primary-alpha-25)] bg-[var(--em-primary-alpha-06)] px-3 py-2.5 sm:py-2 settings-tour-demo-item"
        >
          <Sparkles className="h-3 w-3 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
          <div className="flex-1 min-w-0">
            <p className="text-sm">{rule.content}</p>
            <p className="text-[10px] text-muted-foreground mt-0.5">示例规则</p>
          </div>
          <Switch checked={rule.enabled} disabled className="flex-shrink-0 opacity-60" />
        </div>
      ))}
    </div>
  );
}

export function RulesTab({ sessionId }: RulesTabProps) {
  const [globalRules, setGlobalRules] = useState<Rule[]>([]);
  const [sessionRules, setSessionRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState<string | null>(null);
  const [globalInput, setGlobalInput] = useState("");
  const [sessionInput, setSessionInput] = useState("");
  const [addingGlobal, setAddingGlobal] = useState(false);
  const [addingSession, setAddingSession] = useState(false);
  const sessionRulesUrl = sessionId
    ? `/sessions/${encodeURIComponent(sessionId)}/rules`
    : null;

  const fetchGlobalRules = useCallback(async (force = false) => {
    if (!force) {
      const cached = settingsCache.get<Rule[]>("/rules");
      if (cached) { setGlobalRules(cached); return; }
    }
    try {
      const data = await apiGet<Rule[]>("/rules");
      const rules = Array.isArray(data) ? data : [];
      settingsCache.set("/rules", rules);
      setGlobalRules(rules);
    } catch {
      setGlobalRules([]);
    }
  }, []);

  const fetchSessionRules = useCallback(async (force = false) => {
    if (!sessionRulesUrl) {
      setSessionRules([]);
      return;
    }
    if (!force) {
      const cached = settingsCache.get<Rule[]>(sessionRulesUrl);
      if (cached) { setSessionRules(cached); return; }
    }
    try {
      const data = await apiGet<Rule[]>(sessionRulesUrl);
      const rules = Array.isArray(data) ? data : [];
      settingsCache.set(sessionRulesUrl, rules);
      setSessionRules(rules);
    } catch {
      setSessionRules([]);
    }
  }, [sessionRulesUrl]);

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchGlobalRules(), fetchSessionRules()]).finally(() =>
      setLoading(false)
    );
  }, [fetchGlobalRules, fetchSessionRules]);

  const handleAddGlobalRule = async () => {
    const content = globalInput.trim();
    if (!content) return;
    const snapshot = globalRules;
    const tempId = `temp-global-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const optimisticRule: Rule = {
      id: tempId,
      content,
      enabled: true,
      created_at: new Date().toISOString(),
    };
    const optimisticRules = [...snapshot, optimisticRule];
    setAddingGlobal(true);
    setGlobalInput("");
    setGlobalRules(optimisticRules);
    settingsCache.set("/rules", optimisticRules);
    try {
      const created = await apiPost<Rule>("/rules", { content });
      setGlobalRules((prev) =>
        prev.some((r) => r.id === tempId)
          ? prev.map((r) => (r.id === tempId ? created : r))
          : [...prev, created]
      );
      settingsCache.delete("/rules");
    } catch (err) {
      setGlobalRules(snapshot);
      settingsCache.set("/rules", snapshot);
      setGlobalInput(content);
      alert(err instanceof Error ? err.message : "添加失败");
    } finally {
      setAddingGlobal(false);
    }
  };

  const handleAddSessionRule = async () => {
    const content = sessionInput.trim();
    if (!content || !sessionRulesUrl) return;
    const snapshot = sessionRules;
    const tempId = `temp-session-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const optimisticRule: Rule = {
      id: tempId,
      content,
      enabled: true,
      created_at: new Date().toISOString(),
    };
    const optimisticRules = [...snapshot, optimisticRule];
    setAddingSession(true);
    setSessionInput("");
    setSessionRules(optimisticRules);
    settingsCache.set(sessionRulesUrl, optimisticRules);
    try {
      const created = await apiPost<Rule>(sessionRulesUrl, { content });
      setSessionRules((prev) =>
        prev.some((r) => r.id === tempId)
          ? prev.map((r) => (r.id === tempId ? created : r))
          : [...prev, created]
      );
      settingsCache.invalidatePrefix("/sessions/");
    } catch (err) {
      setSessionRules(snapshot);
      settingsCache.set(sessionRulesUrl, snapshot);
      setSessionInput(content);
      alert(err instanceof Error ? err.message : "添加失败");
    } finally {
      setAddingSession(false);
    }
  };

  const handleToggleGlobal = async (rule: Rule) => {
    const snapshot = globalRules;
    const optimisticRules = snapshot.map((r) =>
      r.id === rule.id ? { ...r, enabled: !r.enabled } : r
    );
    setUpdating(rule.id);
    setGlobalRules(optimisticRules);
    settingsCache.set("/rules", optimisticRules);
    try {
      const updated = await apiPatch<Rule>(`/rules/${encodeURIComponent(rule.id)}`, {
        enabled: !rule.enabled,
      });
      settingsCache.delete("/rules");
      setGlobalRules((prev) =>
        prev.map((r) => (r.id === rule.id ? updated : r))
      );
    } catch (err) {
      setGlobalRules(snapshot);
      settingsCache.set("/rules", snapshot);
      alert(err instanceof Error ? err.message : "更新失败");
    } finally {
      setUpdating(null);
    }
  };

  const handleToggleSession = async (rule: Rule) => {
    if (!sessionId || !sessionRulesUrl) return;
    const snapshot = sessionRules;
    const optimisticRules = snapshot.map((r) =>
      r.id === rule.id ? { ...r, enabled: !r.enabled } : r
    );
    setUpdating(rule.id);
    setSessionRules(optimisticRules);
    settingsCache.set(sessionRulesUrl, optimisticRules);
    try {
      const updated = await apiPatch<Rule>(
        `/sessions/${encodeURIComponent(sessionId)}/rules/${encodeURIComponent(rule.id)}`,
        { enabled: !rule.enabled }
      );
      settingsCache.invalidatePrefix("/sessions/");
      setSessionRules((prev) =>
        prev.map((r) => (r.id === rule.id ? updated : r))
      );
    } catch (err) {
      setSessionRules(snapshot);
      settingsCache.set(sessionRulesUrl, snapshot);
      alert(err instanceof Error ? err.message : "更新失败");
    } finally {
      setUpdating(null);
    }
  };

  const handleDeleteGlobal = async (rule: Rule) => {
    if (!confirm("确定删除该规则？")) return;
    const snapshot = globalRules;
    const optimisticRules = snapshot.filter((r) => r.id !== rule.id);
    setUpdating(rule.id);
    setGlobalRules(optimisticRules);
    settingsCache.set("/rules", optimisticRules);
    try {
      await apiDelete(`/rules/${encodeURIComponent(rule.id)}`);
      settingsCache.delete("/rules");
    } catch (err) {
      setGlobalRules(snapshot);
      settingsCache.set("/rules", snapshot);
      alert(err instanceof Error ? err.message : "删除失败");
    } finally {
      setUpdating(null);
    }
  };

  const handleDeleteSession = async (rule: Rule) => {
    if (!sessionId || !sessionRulesUrl || !confirm("确定删除该规则？")) return;
    const snapshot = sessionRules;
    const optimisticRules = snapshot.filter((r) => r.id !== rule.id);
    setUpdating(rule.id);
    setSessionRules(optimisticRules);
    settingsCache.set(sessionRulesUrl, optimisticRules);
    try {
      await apiDelete(
        `/sessions/${encodeURIComponent(sessionId)}/rules/${encodeURIComponent(rule.id)}`
      );
      settingsCache.invalidatePrefix("/sessions/");
    } catch (err) {
      setSessionRules(snapshot);
      settingsCache.set(sessionRulesUrl, snapshot);
      alert(err instanceof Error ? err.message : "删除失败");
    } finally {
      setUpdating(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex flex-col flex-1">
      {/* Rules list content */}
      <div className="flex-1 flex flex-col gap-6">
        {/* 全局规则 */}
        <div className="flex-1 flex flex-col gap-3" data-coach-id="coach-settings-rules-list">
          <div className="flex items-center gap-2">
            <ScrollText className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />
            <span className="text-sm font-medium">全局规则</span>
          </div>

          <DemoRulesBanner />
          <div className="flex-1 flex flex-col gap-2">
            {globalRules.length === 0 && !isSettingsDemoActive() ? (
              <div className="flex-1 flex items-center justify-center text-xs text-muted-foreground border border-dashed rounded-lg">
                暂无规则
              </div>
            ) : (
              <>
                {globalRules.map((rule) => (
                  <RuleRow
                    key={rule.id}
                    rule={rule}
                    onToggle={handleToggleGlobal}
                    onDelete={handleDeleteGlobal}
                    updating={updating}
                  />
                ))}
              </>
            )}
          </div>
        </div>

        {/* 会话规则（仅当 sessionId 存在时显示） */}
        {sessionId && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <ToggleLeft className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="text-sm font-medium">会话规则</span>
            </div>

            <div className="space-y-2">
              {sessionRules.length === 0 ? (
                <p className="text-xs text-muted-foreground text-center py-6 border border-dashed rounded-lg">
                  暂无规则
                </p>
              ) : (
                sessionRules.map((rule) => (
                  <RuleRow
                    key={rule.id}
                    rule={rule}
                    onToggle={handleToggleSession}
                    onDelete={handleDeleteSession}
                    updating={updating}
                  />
                ))
              )}
            </div>
          </div>
        )}
      </div>

      {/* Input bars pinned at bottom */}
      <div className="shrink-0 mt-auto pt-4 space-y-3 border-t border-border/60">
        <div className="flex flex-col sm:flex-row gap-2">
          <Input
            value={globalInput}
            onChange={(e) => setGlobalInput(e.target.value)}
            className="h-8 sm:h-7 text-xs flex-1"
            placeholder="输入新规则内容..."
            data-coach-id="coach-settings-rule-input"
            onKeyDown={(e) => e.key === "Enter" && handleAddGlobalRule()}
          />
          <Button
            size="sm"
            className="h-8 sm:h-7 text-xs gap-1 text-white shrink-0"
            style={{ backgroundColor: "var(--em-primary)" }}
            disabled={addingGlobal || !globalInput.trim()}
            onClick={handleAddGlobalRule}
            data-coach-id="coach-settings-rule-add-btn"
          >
            {addingGlobal ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Plus className="h-3 w-3" />
            )}
            添加规则
          </Button>
        </div>

        {sessionId && (
          <div className="flex flex-col sm:flex-row gap-2">
            <Input
              value={sessionInput}
              onChange={(e) => setSessionInput(e.target.value)}
              className="h-8 sm:h-7 text-xs flex-1"
              placeholder="输入会话规则内容..."
              onKeyDown={(e) => e.key === "Enter" && handleAddSessionRule()}
            />
            <Button
              size="sm"
              className="h-8 sm:h-7 text-xs gap-1 text-white shrink-0"
              style={{ backgroundColor: "var(--em-primary)" }}
              disabled={addingSession || !sessionInput.trim()}
              onClick={handleAddSessionRule}
            >
              {addingSession ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Plus className="h-3 w-3" />
              )}
              添加规则
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
