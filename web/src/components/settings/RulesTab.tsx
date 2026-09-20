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
    <div className="em-settings-section flex items-center gap-3 rounded-xl border border-border px-3 py-3">
      <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--em-primary-alpha-06)] text-[var(--em-primary)]">
        <ScrollText className="h-3.5 w-3.5" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[13px] leading-relaxed break-words">{rule.content}</p>
        <p className="mt-0.5 text-[10px] text-muted-foreground">
          {rule.enabled ? "已启用" : "已停用"}
        </p>
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
  const [active, setActive] = useState(isSettingsDemoActive);
  useEffect(() => {
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
    <div className="space-y-3">
      <section className="em-plugin-panel space-y-3 rounded-xl border p-3">
        <div className="flex items-start gap-2">
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]">
            <Plus className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0">
            <h4 className="text-sm font-medium">添加规则</h4>
            <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
              用一句明确的话描述 Agent 必须遵守的行为。
            </p>
          </div>
        </div>
        <div className="flex flex-col sm:flex-row gap-2">
          <Input
            value={globalInput}
            onChange={(e) => setGlobalInput(e.target.value)}
            className="h-9 text-xs flex-1"
            placeholder="例如：修改前先保留原始数据列"
            data-coach-id="coach-settings-rule-input"
            onKeyDown={(e) => e.key === "Enter" && handleAddGlobalRule()}
          />
          <Button
            size="sm"
            className="h-9 text-xs gap-1 text-white shrink-0"
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
      </section>

      <section className="em-plugin-panel space-y-3 rounded-xl border p-3" data-coach-id="coach-settings-rules-list">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <ScrollText className="h-4 w-4 text-[var(--em-primary)]" />
            <div>
              <h4 className="text-sm font-medium">全局规则</h4>
              <p className="text-[10px] text-muted-foreground">每个新任务都会加载</p>
            </div>
          </div>
          <span className="rounded-full bg-muted px-2 py-1 text-[10px] font-medium text-muted-foreground">
            {globalRules.length} 条
          </span>
        </div>

        <DemoRulesBanner />
        <div className="space-y-2">
          {globalRules.length === 0 && !isSettingsDemoActive() ? (
            <div className="rounded-xl border border-dashed px-4 py-10 text-center">
              <ScrollText className="mx-auto h-7 w-7 text-muted-foreground/30" />
              <p className="mt-2 text-xs font-medium text-muted-foreground">还没有全局规则</p>
              <p className="mt-1 text-[10px] text-muted-foreground/70">在上方添加后，可随时单独启停</p>
            </div>
          ) : (
            globalRules.map((rule) => (
              <RuleRow
                key={rule.id}
                rule={rule}
                onToggle={handleToggleGlobal}
                onDelete={handleDeleteGlobal}
                updating={updating}
              />
            ))
          )}
        </div>
      </section>

      {sessionId && (
        <section className="em-plugin-panel space-y-3 rounded-xl border p-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <ToggleLeft className="h-4 w-4 text-muted-foreground" />
              <div>
                <h4 className="text-sm font-medium">会话规则</h4>
                <p className="text-[10px] text-muted-foreground">仅覆盖当前任务</p>
              </div>
            </div>
            <span className="rounded-full bg-muted px-2 py-1 text-[10px] font-medium text-muted-foreground">
              {sessionRules.length} 条
            </span>
          </div>

          <div className="flex flex-col sm:flex-row gap-2">
            <Input
              value={sessionInput}
              onChange={(e) => setSessionInput(e.target.value)}
              className="h-9 text-xs flex-1"
              placeholder="输入仅用于当前任务的规则..."
              onKeyDown={(e) => e.key === "Enter" && handleAddSessionRule()}
            />
            <Button
              size="sm"
              className="h-9 text-xs gap-1 text-white shrink-0"
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

          <div className="space-y-2">
            {sessionRules.length === 0 ? (
              <p className="rounded-xl border border-dashed py-6 text-center text-xs text-muted-foreground">
                当前任务没有专属规则
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
        </section>
      )}
    </div>
  );
}
