"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Loader2,
  Save,
  CheckCircle2,
  Shield,
  Bot,
  FolderArchive,
  RotateCcw,
  Gauge,
  Shrink,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { apiGet, apiPut } from "@/lib/api";

interface RuntimeConfig {
  subagent_enabled: boolean;
  backup_enabled: boolean;
  external_safe_mode: boolean;
  max_iterations: number;
  compaction_enabled: boolean;
  compaction_threshold_ratio: number;
  code_policy_enabled: boolean;
}

interface ToggleItem {
  key: keyof RuntimeConfig;
  label: string;
  desc: string;
  icon: React.ReactNode;
  type: "bool" | "int" | "float";
}

const ITEMS: ToggleItem[] = [
  {
    key: "subagent_enabled",
    label: "子代理",
    desc: "启用 Explorer / Verifier 等子代理",
    icon: <Bot className="h-4 w-4" />,
    type: "bool",
  },
  {
    key: "backup_enabled",
    label: "备份沙盒",
    desc: "文件操作前自动创建备份副本",
    icon: <FolderArchive className="h-4 w-4" />,
    type: "bool",
  },
  {
    key: "external_safe_mode",
    label: "安全模式",
    desc: "过滤 SSE 中的内部事件（工具调用/思考等）",
    icon: <Shield className="h-4 w-4" />,
    type: "bool",
  },
  {
    key: "code_policy_enabled",
    label: "代码策略",
    desc: "启用代码安全策略引擎（沙盒限制）",
    icon: <Shield className="h-4 w-4" />,
    type: "bool",
  },
  {
    key: "compaction_enabled",
    label: "上下文压缩",
    desc: "Token 超阈值时自动摘要压缩",
    icon: <Shrink className="h-4 w-4" />,
    type: "bool",
  },
  {
    key: "max_iterations",
    label: "最大迭代次数",
    desc: "单轮对话中工具调用循环上限",
    icon: <RotateCcw className="h-4 w-4" />,
    type: "int",
  },
  {
    key: "compaction_threshold_ratio",
    label: "压缩阈值比例",
    desc: "Token 使用率超过此比例触发自动压缩 (0-1)",
    icon: <Gauge className="h-4 w-4" />,
    type: "float",
  },
];

export function RuntimeTab() {
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [draft, setDraft] = useState<Partial<RuntimeConfig>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const fetchConfig = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiGet<RuntimeConfig>("/config/runtime");
      setConfig(data);
      setDraft({});
    } catch {
      // Backend not ready
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const merged = config
    ? { ...config, ...draft }
    : null;

  const hasChanges = Object.keys(draft).length > 0;

  const handleSave = async () => {
    if (!hasChanges) return;
    setSaving(true);
    try {
      await apiPut("/config/runtime", draft);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      await fetchConfig();
    } catch {
      // ignore
    } finally {
      setSaving(false);
    }
  };

  if (loading && !config) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin mr-2" />
        加载配置…
      </div>
    );
  }

  if (!merged) {
    return (
      <div className="text-center py-12 text-muted-foreground text-sm">
        无法获取运行时配置
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        调整运行时行为配置，保存后立即生效并持久化到 .env 文件。
      </p>

      <div className="space-y-3">
        {ITEMS.map((item) => {
          const value = merged[item.key];
          return (
            <div key={item.key}>
              <div className="flex items-center justify-between gap-4">
                <div className="flex items-start gap-3 flex-1 min-w-0">
                  <span className="mt-0.5 text-muted-foreground">{item.icon}</span>
                  <div className="min-w-0">
                    <div className="text-sm font-medium">{item.label}</div>
                    <div className="text-xs text-muted-foreground">{item.desc}</div>
                  </div>
                </div>

                {item.type === "bool" ? (
                  <Switch
                    checked={value as boolean}
                    onCheckedChange={(checked: boolean) =>
                      setDraft((prev) => ({ ...prev, [item.key]: checked }))
                    }
                  />
                ) : (
                  <Input
                    type="number"
                    className="w-24 h-8 text-sm text-right"
                    step={item.type === "float" ? 0.05 : 1}
                    min={item.type === "float" ? 0 : 1}
                    max={item.type === "float" ? 1 : 500}
                    value={value as number}
                    onChange={(e) => {
                      const v =
                        item.type === "float"
                          ? parseFloat(e.target.value)
                          : parseInt(e.target.value, 10);
                      if (!isNaN(v)) {
                        setDraft((prev) => ({ ...prev, [item.key]: v }));
                      }
                    }}
                  />
                )}
              </div>
              <Separator className="mt-3" />
            </div>
          );
        })}
      </div>

      <div className="flex justify-end pt-2">
        <Button
          size="sm"
          disabled={!hasChanges || saving}
          onClick={handleSave}
          className="gap-1.5"
        >
          {saving ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : saved ? (
            <CheckCircle2 className="h-3.5 w-3.5" />
          ) : (
            <Save className="h-3.5 w-3.5" />
          )}
          {saved ? "已保存" : "保存"}
        </Button>
      </div>
    </div>
  );
}
