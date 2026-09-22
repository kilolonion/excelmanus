"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import { motion } from "framer-motion";
import {
  Plus,
  Trash2,
  Pencil,
  Save,
  X,
  Loader2,
  Package,
  Code,
  FileText,
  FolderOpen,
  Github,
  Download,
  CheckCircle2,
  Sparkles,
  Bot,
  Gauge,
  Terminal,
  Timer,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { apiGet, apiPost, apiPatch, apiDelete } from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";
import { MiniCheckbox } from "@/components/ui/MiniCheckbox";
import { isSettingsDemoActive, onSettingsDemoChange, DEMO_SKILLS } from "@/components/onboarding/demo-settings";
import { SettingsEntityCard } from "@/components/settings/SettingsEntityCard";
import { RuntimeSettingsPanel, type RuntimeSettingGroup } from "./RuntimeSettingsPanel";

/* ── Types ── */

interface SkillSummary {
  name: string;
  description: string;
  source: string;
  writable: boolean;
  "argument-hint"?: string;
}

interface SkillDetail extends SkillSummary {
  "file-patterns": string[];
  resources: string[];
  version: string;
  instructions: string;
  resource_contents: Record<string, string>;
  hooks: Record<string, unknown>;
  model: string | null;
  metadata: Record<string, unknown>;
}

interface ImportResult {
  status: string;
  name: string;
  detail?: {
    name: string;
    description: string;
    source_type: string;
    files_copied: string[];
    dest_dir: string;
  };
}

const SOURCE_COLORS: Record<string, string> = {
  system: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  user: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  project: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
};

const SOURCE_LABELS: Record<string, string> = {
  all: "全部",
  system: "系统",
  user: "用户",
  project: "项目",
};

const SKILL_SETTING_GROUPS: RuntimeSettingGroup[] = [
  {
    title: "技能发现",
    description: "选择 Agent 会从哪些兼容目录加载技能，以及每次激活技能时可注入的内容上限。",
    icon: <Sparkles className="h-4 w-4" />,
    items: [
      {
        key: "skills_discovery_enabled",
        label: "自动发现兼容目录",
        desc: "除内置和用户 / 项目目录外，还扫描其他工具常用的技能目录。",
        icon: <Sparkles className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "skills_discovery_include_agents",
        label: "加载 .agents/skills",
        desc: "扫描项目里的 .agents/skills 文件夹；此设置与子代理无关。",
        disabledDesc: "开启自动发现兼容目录后可使用。",
        disabledWhen: (settings) => !settings.skills_discovery_enabled,
        icon: <Bot className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "skills_discovery_scan_workspace_ancestors",
        label: "扫描工作区上级目录",
        desc: "从当前目录到项目根，逐层查找 .agents/skills。",
        disabledDesc: "开启自动发现兼容目录后可使用。",
        disabledWhen: (settings) => !settings.skills_discovery_enabled,
        icon: <Sparkles className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "skills_discovery_scan_external_tool_dirs",
        label: "兼容 Claude 与 OpenClaw",
        desc: "从用户目录与项目中的 Claude、OpenClaw 技能文件夹加载技能。",
        disabledDesc: "开启自动发现兼容目录后可使用。",
        disabledWhen: (settings) => !settings.skills_discovery_enabled,
        icon: <Sparkles className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "skills_context_char_budget",
        label: "技能注入长度上限",
        desc: "激活技能时注入对话的正文总字符上限；0 表示不限制。",
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 0,
        max: 100000,
      },
    ],
  },
  {
    title: "技能 Hook",
    description: "控制技能包是否可在任务事件中运行外部命令，以及命令的资源边界。",
    icon: <Terminal className="h-4 w-4" />,
    defaultOpen: false,
    items: [
      {
        key: "hooks_command_enabled",
        label: "允许 Hook 外部命令",
        desc: "允许技能包在任务开始、用户提交、工具前后及子代理起止时运行已授权命令。",
        icon: <Terminal className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "hooks_command_timeout_seconds",
        label: "Hook 命令超时",
        desc: "外部命令最长执行时间（秒）；超时会跳过且不影响主流程。",
        disabledDesc: "允许 Hook 外部命令后可调整。",
        disabledWhen: (settings) => !settings.hooks_command_enabled,
        icon: <Timer className="h-4 w-4" />,
        type: "int",
        min: 1,
        max: 300,
      },
      {
        key: "hooks_output_max_chars",
        label: "Hook 输出上限",
        desc: "外部命令返回内容可注入任务的最大字符数。",
        disabledDesc: "允许 Hook 外部命令后可调整。",
        disabledWhen: (settings) => !settings.hooks_command_enabled,
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 1000,
        max: 100000,
      },
    ],
  },
];

function DemoSkillsBanner() {
  const [active, setActive] = useState(isSettingsDemoActive);
  useEffect(() => {
    return onSettingsDemoChange(() => setActive(isSettingsDemoActive()));
  }, []);
  if (!active) return null;
  return (
    <div className="space-y-1.5">
      {DEMO_SKILLS.map((skill) => (
        <SettingsEntityCard
          key={skill.name}
          dashed
          className="settings-tour-demo-item"
          icon={<Package className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />}
          name={skill.name}
          badges={
            <Badge className={`text-[9px] px-1.5 py-0 shrink-0 border-0 ${SOURCE_COLORS[skill.source] || "bg-muted text-muted-foreground"}`} variant="secondary">
              {SOURCE_LABELS[skill.source] || skill.source}
            </Badge>
          }
          description={skill.description}
          meta={<p className="text-[10px] text-muted-foreground">示例技能</p>}
        />
      ))}
    </div>
  );
}

type ImportMethod = "file" | "github" | "manual";
type SourceFilter = "all" | "system" | "user" | "project";

export function SkillsTab() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null);
  const [skillDetails, setSkillDetails] = useState<Record<string, SkillDetail>>({});
  const [editingSkill, setEditingSkill] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");

  const [showImport, setShowImport] = useState(false);
  const [importMethod, setImportMethod] = useState<ImportMethod>("file");
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [filePath, setFilePath] = useState("");
  const [fileOverwrite, setFileOverwrite] = useState(false);
  const [githubUrl, setGithubUrl] = useState("");
  const [githubOverwrite, setGithubOverwrite] = useState(false);
  const [formDraft, setFormDraft] = useState({ name: "", description: "", instructions: "", filePatterns: "" });
  const [jsonDraft, setJsonDraft] = useState("");
  const [editMode, setEditMode] = useState<"form" | "json">("form");

  const filteredSkills = useMemo(() => {
    if (sourceFilter === "all") return skills;
    return skills.filter((s) => s.source === sourceFilter);
  }, [skills, sourceFilter]);

  const sourceCounts = useMemo(() => {
    const counts: Record<string, number> = { all: skills.length, system: 0, user: 0, project: 0 };
    for (const s of skills) counts[s.source] = (counts[s.source] || 0) + 1;
    return counts;
  }, [skills]);

  const fetchSkills = useCallback(async (force = false) => {
    if (!force) {
      const cached = settingsCache.get<SkillSummary[]>("/skills");
      if (cached) { setSkills(cached); return; }
    }
    setLoading(true);
    try {
      const data = await apiGet<SkillSummary[]>("/skills");
      settingsCache.set("/skills", data);
      setSkills(data);
    } catch {
      // 后端未就绪
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchSkills(); }, [fetchSkills]);

  const fetchDetail = async (name: string) => {
    try {
      const data = await apiGet<SkillDetail>(`/skills/${encodeURIComponent(name)}`);
      setSkillDetails((prev) => ({ ...prev, [name]: data }));
    } catch { /* ignore */ }
  };

  const toggleExpand = (name: string) => {
    if (expandedSkill === name) { setExpandedSkill(null); return; }
    setExpandedSkill(name);
    if (!skillDetails[name]) fetchDetail(name);
  };

  const resetImportState = () => {
    setShowImport(false);
    setEditingSkill(null);
    setImportResult(null);
    setFilePath(""); setFileOverwrite(false);
    setGithubUrl(""); setGithubOverwrite(false);
    setFormDraft({ name: "", description: "", instructions: "", filePatterns: "" });
    setJsonDraft("");
  };

  const handleImportFromFile = async () => {
    if (!filePath.trim()) return;
    setSaving(true); setImportResult(null);
    try {
      const result = await apiPost<ImportResult>("/skills/import", { source: "local_path", value: filePath.trim(), overwrite: fileOverwrite });
      setImportResult(result); fetchSkills(true);
    } catch (err) { alert(err instanceof Error ? err.message : "导入失败"); }
    finally { setSaving(false); }
  };

  const handleImportFromGithub = async () => {
    if (!githubUrl.trim()) return;
    setSaving(true); setImportResult(null);
    try {
      const result = await apiPost<ImportResult>("/skills/import", { source: "github_url", value: githubUrl.trim(), overwrite: githubOverwrite });
      setImportResult(result); fetchSkills(true);
    } catch (err) { alert(err instanceof Error ? err.message : "导入失败"); }
    finally { setSaving(false); }
  };

  const handleManualCreate = async () => {
    setSaving(true);
    try {
      const payload: Record<string, unknown> = { description: formDraft.description, instructions: formDraft.instructions };
      if (formDraft.filePatterns.trim()) {
        payload["file-patterns"] = formDraft.filePatterns.split(",").map((s) => s.trim()).filter(Boolean);
      }
      await apiPost("/skills", { name: formDraft.name, payload });
      resetImportState(); fetchSkills(true);
    } catch (err) { alert(err instanceof Error ? err.message : "创建失败"); }
    finally { setSaving(false); }
  };

  const handleEdit = async (name: string) => {
    setSaving(true);
    try {
      if (editMode === "json") {
        await apiPatch(`/skills/${encodeURIComponent(name)}`, { payload: JSON.parse(jsonDraft) });
      } else {
        const payload: Record<string, unknown> = { description: formDraft.description, instructions: formDraft.instructions };
        if (formDraft.filePatterns.trim()) {
          payload["file-patterns"] = formDraft.filePatterns.split(",").map((s) => s.trim()).filter(Boolean);
        }
        await apiPatch(`/skills/${encodeURIComponent(name)}`, { payload });
      }
      setEditingSkill(null); setJsonDraft("");
      setFormDraft({ name: "", description: "", instructions: "", filePatterns: "" });
      delete skillDetails[name]; fetchSkills(true);
    } catch (err) { alert(err instanceof Error ? err.message : "更新失败"); }
    finally { setSaving(false); }
  };

  const handleDelete = async (name: string) => {
    if (!confirm(`确定删除技能 "${name}"？`)) return;
    const snapshot = skills.find((s) => s.name === name);
    setSkills((prev) => prev.filter((s) => s.name !== name));
    settingsCache.delete("/skills");
    try {
      await apiDelete(`/skills/${encodeURIComponent(name)}`);
    } catch (err) {
      if (snapshot) setSkills((prev) => [...prev, snapshot]);
      alert(err instanceof Error ? err.message : "删除失败");
    }
  };

  const startEdit = (skill: SkillSummary) => {
    const detail = skillDetails[skill.name];
    if (detail) {
      setFormDraft({ name: detail.name, description: detail.description, instructions: detail.instructions || "", filePatterns: (detail["file-patterns"] || []).join(", ") });
      setJsonDraft(JSON.stringify(detail, null, 2));
    } else {
      setFormDraft({ name: skill.name, description: skill.description, instructions: "", filePatterns: "" });
    }
    setEditingSkill(skill.name); setShowImport(false); setEditMode("form");
  };

  return (
    <div className="space-y-3">
      <section className="em-plugin-panel space-y-3 rounded-xl border p-3">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-2">
            <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]">
              <Package className="h-3.5 w-3.5" />
            </div>
            <div className="min-w-0">
              <h4 className="text-sm font-medium">技能仓库</h4>
              <p className="mt-0.5 text-[10px] leading-relaxed text-muted-foreground">
                已加载 {skills.length} 个技能包，可按来源筛选
              </p>
            </div>
          </div>
          <Button
            size="sm"
            variant="outline"
            className="h-8 text-xs gap-1.5 shrink-0"
            onClick={() => { resetImportState(); setShowImport(true); setImportMethod("file"); }}
          >
            <Plus className="h-3 w-3" />
            导入技能
          </Button>
        </div>

        <div className="min-w-0 overflow-x-auto scrollbar-none">
          <div className="flex items-center gap-1 sm:gap-1.5">
            {(["all", "system", "user", "project"] as const).map((src) => {
              const isActive = sourceFilter === src;
              const count = sourceCounts[src] || 0;
              if (src !== "all" && count === 0) return null;
              return (
                <button
                  key={src}
                  type="button"
                  className={`inline-flex items-center gap-1 px-2 py-1 sm:py-0.5 rounded-full text-[11px] font-medium transition-colors whitespace-nowrap border flex-shrink-0 ${
                    isActive
                      ? "border-[var(--em-primary)] bg-[var(--em-primary-alpha-10)]" : "border-border text-muted-foreground hover:bg-muted/60"
                  }`}
                  style={isActive ? { color: "var(--em-primary)" } : undefined}
                  onClick={() => setSourceFilter(src)}
                >
                  {SOURCE_LABELS[src]}
                  <span className={`text-[10px] ${isActive ? "opacity-70" : "text-muted-foreground/60"}`}>{count}</span>
                </button>
              );
            })}
          </div>
        </div>
      </section>

      {showImport && !editingSkill && (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          exit={{ opacity: 0, height: 0 }}
          className="rounded-lg border border-dashed border-border p-3 space-y-3 overflow-hidden"
        >
          <div className="flex items-center gap-1 overflow-x-auto scrollbar-none">
            {([
              { key: "file" as const, label: "文件路径", icon: <FolderOpen className="h-3 w-3" /> },
              { key: "github" as const, label: "GitHub", icon: <Github className="h-3 w-3" /> },
              { key: "manual" as const, label: "手动创建", icon: <Pencil className="h-3 w-3" /> },
            ]).map((tab) => {
              const isActive = importMethod === tab.key;
              return (
                <button
                  key={tab.key}
                  type="button"
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors whitespace-nowrap border ${
                    isActive ? "text-white border-transparent" : "border-border text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                  }`}
                  style={isActive ? { backgroundColor: "var(--em-primary)" } : undefined}
                  onClick={() => { setImportMethod(tab.key); setImportResult(null); }}
                >
                  {tab.icon}
                  {tab.label}
                </button>
              );
            })}
          </div>

          {importMethod === "file" && (
            <div className="space-y-2">
              <div>
                <label className="text-xs text-muted-foreground">SKILL.md 文件路径（本地绝对路径）</label>
                <Input value={filePath} onChange={(e) => setFilePath(e.target.value)} className="h-7 text-xs font-mono" placeholder="/path/to/my-skill/SKILL.md" />
              </div>
              <p className="text-[10px] text-muted-foreground">将自动扫描同目录下的附属文件（scripts/、references/ 等）一并导入</p>
              <MiniCheckbox checked={fileOverwrite} onChange={setFileOverwrite} label="覆盖已存在的同名技能" />
            </div>
          )}
          {importMethod === "github" && (
            <div className="space-y-2">
              <div>
                <label className="text-xs text-muted-foreground">GitHub SKILL.md 链接</label>
                <Input value={githubUrl} onChange={(e) => setGithubUrl(e.target.value)} className="h-7 text-xs font-mono" placeholder="https://github.com/org/repo/blob/main/skills/my-skill/SKILL.md" />
              </div>
              <p className="text-[10px] text-muted-foreground">支持 github.com/.../blob/... 和 raw.githubusercontent.com 格式。自动通过 GitHub API 拉取同目录附属文件。</p>
              <MiniCheckbox checked={githubOverwrite} onChange={setGithubOverwrite} label="覆盖已存在的同名技能" />
            </div>
          )}
          {importMethod === "manual" && (
            <div className="space-y-2">
              <div>
                <label className="text-xs text-muted-foreground">名称 *</label>
                <Input value={formDraft.name} onChange={(e) => setFormDraft((d) => ({ ...d, name: e.target.value }))} className="h-7 text-xs" placeholder="skill-name" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">描述 *</label>
                <Input value={formDraft.description} onChange={(e) => setFormDraft((d) => ({ ...d, description: e.target.value }))} className="h-7 text-xs" placeholder="技能描述" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Instructions</label>
                <textarea value={formDraft.instructions} onChange={(e) => setFormDraft((d) => ({ ...d, instructions: e.target.value }))} className="w-full h-20 rounded-md border border-input bg-background px-3 py-1 text-xs font-mono resize-y focus:outline-none focus:ring-1 focus:ring-ring" placeholder="技能指令..." />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">文件模式（逗号分隔）</label>
                <Input value={formDraft.filePatterns} onChange={(e) => setFormDraft((d) => ({ ...d, filePatterns: e.target.value }))} className="h-7 text-xs font-mono" placeholder="*.xlsx, *.csv" />
              </div>
            </div>
          )}

          {importResult && importResult.detail && (
            <div className="rounded-md bg-emerald-500/10 border border-emerald-500/20 p-2.5 space-y-1">
              <div className="flex items-center gap-1.5">
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                <span className="text-xs font-medium text-emerald-700 dark:text-emerald-300">已导入: {importResult.name}</span>
              </div>
              {importResult.detail.description && <p className="text-[10px] text-emerald-600 dark:text-emerald-400">{importResult.detail.description}</p>}
              {importResult.detail.files_copied.length > 0 && (
                <div className="text-[10px] text-emerald-600 dark:text-emerald-400">
                  {importResult.detail.files_copied.length} 个文件：{importResult.detail.files_copied.map((f) => <span key={f} className="font-mono ml-1">{f}</span>)}
                </div>
              )}
            </div>
          )}

          <div className="flex flex-col-reverse sm:flex-row justify-end gap-2">
            <Button size="sm" variant="ghost" className="h-8 sm:h-7 text-xs gap-1" onClick={resetImportState}>
              <X className="h-3 w-3" /> 取消
            </Button>
            {importMethod === "file" && (
              <Button size="sm" className="h-8 sm:h-7 text-xs gap-1 text-white" style={{ backgroundColor: "var(--em-primary)" }} disabled={saving || !filePath.trim()} onClick={handleImportFromFile}>
                {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />} 导入
              </Button>
            )}
            {importMethod === "github" && (
              <Button size="sm" className="h-8 sm:h-7 text-xs gap-1 text-white" style={{ backgroundColor: "var(--em-primary)" }} disabled={saving || !githubUrl.trim()} onClick={handleImportFromGithub}>
                {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />} 导入
              </Button>
            )}
            {importMethod === "manual" && (
              <Button size="sm" className="h-8 sm:h-7 text-xs gap-1 text-white" style={{ backgroundColor: "var(--em-primary)" }} disabled={saving || !formDraft.name || !formDraft.description} onClick={handleManualCreate}>
                {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />} 创建
              </Button>
            )}
          </div>
        </motion.div>
      )}

      {editingSkill && (
        <div className="rounded-lg border border-dashed border-border p-3 space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5 min-w-0">
              <Pencil className="h-3.5 w-3.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
              <span className="text-xs font-medium break-words">编辑: {editingSkill}</span>
            </div>
            <div className="flex gap-1 shrink-0">
              {(["form", "json"] as const).map((mode) => {
                const isActive = editMode === mode;
                return (
                  <button key={mode} type="button" className={`inline-flex items-center px-2.5 py-1 rounded-full text-[11px] font-medium transition-colors border ${isActive ? "text-white border-transparent" : "border-border text-muted-foreground hover:bg-muted/60"}`} style={isActive ? { backgroundColor: "var(--em-primary)" } : undefined} onClick={() => setEditMode(mode)}>
                    {mode === "form" ? "表单" : "JSON"}
                  </button>
                );
              })}
            </div>
          </div>
          {editMode === "form" ? (
            <div className="space-y-2">
              <div>
                <label className="text-xs text-muted-foreground">描述</label>
                <Input value={formDraft.description} onChange={(e) => setFormDraft((d) => ({ ...d, description: e.target.value }))} className="h-7 text-xs" placeholder="技能描述" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Instructions</label>
                <textarea value={formDraft.instructions} onChange={(e) => setFormDraft((d) => ({ ...d, instructions: e.target.value }))} className="w-full h-20 rounded-md border border-input bg-background px-3 py-1 text-xs font-mono resize-y focus:outline-none focus:ring-1 focus:ring-ring" placeholder="技能指令..." />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">文件模式（逗号分隔）</label>
                <Input value={formDraft.filePatterns} onChange={(e) => setFormDraft((d) => ({ ...d, filePatterns: e.target.value }))} className="h-7 text-xs font-mono" placeholder="*.xlsx, *.csv" />
              </div>
            </div>
          ) : (
            <textarea value={jsonDraft} onChange={(e) => setJsonDraft(e.target.value)} className="w-full h-32 rounded-md border border-input bg-background px-3 py-2 text-xs font-mono resize-y focus:outline-none focus:ring-1 focus:ring-ring" placeholder='{"description": "...", "instructions": "..."}' />
          )}
          <div className="flex flex-col-reverse sm:flex-row justify-end gap-2">
            <Button size="sm" variant="ghost" className="h-8 sm:h-7 text-xs gap-1" onClick={() => { setEditingSkill(null); setFormDraft({ name: "", description: "", instructions: "", filePatterns: "" }); setJsonDraft(""); }}>
              <X className="h-3 w-3" /> 取消
            </Button>
            <Button size="sm" className="h-8 sm:h-7 text-xs gap-1 text-white" style={{ backgroundColor: "var(--em-primary)" }} disabled={saving} onClick={() => handleEdit(editingSkill)}>
              {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />} 更新
            </Button>
          </div>
        </div>
      )}

      <section className="em-plugin-panel space-y-2 rounded-xl border p-3" data-coach-id="coach-settings-skills-list">
        <div className="flex items-center justify-between gap-2 px-0.5">
          <div>
            <h4 className="text-sm font-medium">已安装技能</h4>
            <p className="text-[10px] text-muted-foreground">点击卡片查看指令、资源与适用文件</p>
          </div>
          <span className="rounded-full bg-muted px-2 py-1 text-[10px] font-medium text-muted-foreground">
            {filteredSkills.length} 个
          </span>
        </div>
        <DemoSkillsBanner />
        {loading && skills.length === 0 ? (
          <div className="flex items-center justify-center py-10 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : filteredSkills.length === 0 && !isSettingsDemoActive() ? (
          <div className="rounded-xl border border-dashed text-center py-10">
            <Sparkles className="h-8 w-8 mx-auto mb-2 text-muted-foreground/30" />
            <p className="text-xs text-muted-foreground">
              {sourceFilter === "all" ? "暂无已加载的技能包" : `暂无 ${SOURCE_LABELS[sourceFilter]} 类技能`}
            </p>
          </div>
        ) : null}
        {!loading && filteredSkills.map((skill) => {
          const isExpanded = expandedSkill === skill.name;
          return (
            <SettingsEntityCard
              key={skill.name}
              expandable
              expanded={isExpanded}
              onClick={() => toggleExpand(skill.name)}
              icon={<Package className="h-3.5 w-3.5" style={{ color: "var(--em-primary)" }} />}
              name={skill.name}
              badges={
                <>
                  <Badge className={`text-[9px] px-1.5 py-0 shrink-0 border-0 ${SOURCE_COLORS[skill.source] || "bg-muted text-muted-foreground"}`} variant="secondary">
                    {SOURCE_LABELS[skill.source] || skill.source}
                  </Badge>
                  {skill.writable && <Badge variant="outline" className="text-[9px] px-1 py-0 shrink-0">可写</Badge>}
                </>
              }
              description={skill.description}
              actions={skill.writable ? (
                <>
                  <Button variant="ghost" size="icon" className="h-6 w-6" title="编辑" onClick={() => { if (!skillDetails[skill.name]) fetchDetail(skill.name); startEdit(skill); }}>
                    <Pencil className="h-3 w-3" />
                  </Button>
                  <Button variant="ghost" size="icon" className="h-6 w-6 text-destructive" title="删除" onClick={() => handleDelete(skill.name)}>
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </>
              ) : undefined}
            >
              {isExpanded && (
                <div className="border-t border-border/50 px-4 py-3 space-y-2.5 bg-muted/20">
                  {skillDetails[skill.name] ? (
                    <>
                      <div className="flex flex-wrap gap-1.5">
                        {skillDetails[skill.name].version && (
                          <Badge variant="outline" className="text-[10px] gap-0.5"><Code className="h-2.5 w-2.5" />v{skillDetails[skill.name].version}</Badge>
                        )}
                        {(skillDetails[skill.name]["file-patterns"] || []).map((p) => (
                          <Badge key={p} variant="secondary" className="text-[10px] font-mono">{p}</Badge>
                        ))}
                        {Object.keys(skillDetails[skill.name].hooks || {}).length > 0 && (
                          <Badge variant="secondary" className="text-[10px] gap-0.5"><Sparkles className="h-2.5 w-2.5" />{Object.keys(skillDetails[skill.name].hooks).length} hooks</Badge>
                        )}
                      </div>
                      {skillDetails[skill.name].instructions && (
                        <div>
                          <div className="flex items-center gap-1 mb-1"><FileText className="h-3 w-3 text-muted-foreground" /><span className="text-[10px] font-medium text-muted-foreground">Instructions</span></div>
                          <pre className="text-[11px] font-mono bg-background rounded-md p-2.5 max-h-36 overflow-auto whitespace-pre-wrap break-words border border-border/50 leading-relaxed">
                            {skillDetails[skill.name].instructions.slice(0, 500)}
                            {skillDetails[skill.name].instructions.length > 500 && <span className="text-muted-foreground"> ...（共 {skillDetails[skill.name].instructions.length} 字符）</span>}
                          </pre>
                        </div>
                      )}
                      {skillDetails[skill.name].resources.length > 0 && (
                        <div>
                          <div className="flex items-center gap-1 mb-1"><FolderOpen className="h-3 w-3 text-muted-foreground" /><span className="text-[10px] font-medium text-muted-foreground">Resources ({skillDetails[skill.name].resources.length})</span></div>
                          <div className="rounded-md border border-border/50 bg-background divide-y divide-border/30">
                            {skillDetails[skill.name].resources.map((r) => <div key={r} className="px-2.5 py-1.5 text-[11px] font-mono text-muted-foreground truncate">{r}</div>)}
                          </div>
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="flex items-center gap-2 py-3 justify-center">
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                      <span className="text-xs text-muted-foreground">加载详情...</span>
                    </div>
                  )}
                </div>
              )}
            </SettingsEntityCard>
          );
        })}
      </section>

      <RuntimeSettingsPanel groups={SKILL_SETTING_GROUPS} />
    </div>
  );
}
