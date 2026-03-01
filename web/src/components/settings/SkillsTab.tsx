"use client";

import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Plus,
  Trash2,
  Pencil,
  Save,
  X,
  Loader2,
  Package,
  ChevronRight,
  Code,
  FileText,
  FolderOpen,
  Github,
  Download,
  CheckCircle2,
  Sparkles,
  Store,
  Search,
  RefreshCw,
  ExternalLink,
  Star,
  ChevronLeft,
  Tag,
  User,
  GitBranch,
  Clock,
  TrendingUp,
  BarChart3,
  FileCode,
  Zap,
  Shield,
  ArrowUpCircle,
  AlertCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  apiGet, apiPost, apiPatch, apiDelete,
  clawhubSearch, clawhubInstall, clawhubListInstalled, clawhubCheckUpdates, clawhubUpdate, clawhubSkillDetail,
  type ClawHubSearchResult, type ClawHubInstalled, type ClawHubUpdateInfo, type ClawHubSkillDetail,
} from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";
import { MiniCheckbox } from "@/components/ui/MiniCheckbox";
import { isSettingsDemoActive, onSettingsDemoChange, DEMO_SKILLS } from "@/components/onboarding/demo-settings";

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

function DemoSkillsBanner() {
  const [active, setActive] = useState(false);
  useEffect(() => {
    setActive(isSettingsDemoActive());
    return onSettingsDemoChange(() => setActive(isSettingsDemoActive()));
  }, []);
  if (!active) return null;
  return (
    <div className="space-y-1.5">
      {DEMO_SKILLS.map((skill) => (
        <div
          key={skill.name}
          className="rounded-lg border border-dashed border-[var(--em-primary-alpha-25)] bg-[var(--em-primary-alpha-06)] px-3 py-2.5 sm:py-2 settings-tour-demo-item"
        >
          <div className="flex items-center gap-2">
            <Package className="h-3.5 w-3.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
            <span className="text-sm font-medium">{skill.name}</span>
            <Badge className={`text-[9px] px-1.5 py-0 shrink-0 border-0 ${SOURCE_COLORS[skill.source] || "bg-muted text-muted-foreground"}`} variant="secondary">
              {SOURCE_LABELS[skill.source] || skill.source}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-1 ml-5.5">{skill.description}</p>
          <p className="text-[10px] text-muted-foreground mt-0.5 ml-5.5">示例技能</p>
        </div>
      ))}
    </div>
  );
}

const SUGGESTED_TAGS = [
  { label: "数据分析", icon: BarChart3 },
  { label: "格式化", icon: FileCode },
  { label: "自动化", icon: Zap },
  { label: "图表", icon: TrendingUp },
  { label: "数据清洗", icon: Shield },
];

type TopView = "installed" | "market";
type ImportMethod = "file" | "github" | "manual";
type SourceFilter = "all" | "system" | "user" | "project";

/* ══════════════════════════════════════════════════════════
   Main Component
   ══════════════════════════════════════════════════════════ */

export function SkillsTab() {
  const [topView, setTopView] = useState<TopView>("installed");

  // ── Installed view state ──
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null);
  const [skillDetails, setSkillDetails] = useState<Record<string, SkillDetail>>({});
  const [editingSkill, setEditingSkill] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");

  // ── Import state ──
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

  // ── Market view state ──
  const [chQuery, setChQuery] = useState("");
  const [chResults, setChResults] = useState<ClawHubSearchResult[]>([]);
  const [chSearching, setChSearching] = useState(false);
  const [chInstalling, setChInstalling] = useState<string | null>(null);
  const [chJustInstalled, setChJustInstalled] = useState<string | null>(null);
  const [chDetail, setChDetail] = useState<ClawHubSkillDetail | null>(null);
  const [chError, setChError] = useState<string | null>(null);
  const [chSuccessMsg, setChSuccessMsg] = useState<string | null>(null);
  const [chInstalled, setChInstalled] = useState<ClawHubInstalled[]>([]);
  const [chUpdates, setChUpdates] = useState<ClawHubUpdateInfo[]>([]);
  const [chUpdating, setChUpdating] = useState<string | null>(null);
  const chSearchRef = useRef<HTMLInputElement>(null);

  // Auto-dismiss success
  useEffect(() => {
    if (!chSuccessMsg) return;
    const t = setTimeout(() => setChSuccessMsg(null), 3000);
    return () => clearTimeout(t);
  }, [chSuccessMsg]);

  const filteredSkills = useMemo(() => {
    if (sourceFilter === "all") return skills;
    return skills.filter((s) => s.source === sourceFilter);
  }, [skills, sourceFilter]);

  const sourceCounts = useMemo(() => {
    const counts: Record<string, number> = { all: skills.length, system: 0, user: 0, project: 0 };
    for (const s of skills) counts[s.source] = (counts[s.source] || 0) + 1;
    return counts;
  }, [skills]);

  /* ── Data fetching ── */

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

  /* ── Import handlers ── */

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
    try { await apiDelete(`/skills/${encodeURIComponent(name)}`); fetchSkills(true); }
    catch (err) { alert(err instanceof Error ? err.message : "删除失败"); }
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

  /* ── Market handlers ── */

  const handleChSearch = useCallback(async (q?: string) => {
    const sq = (q ?? chQuery).trim();
    if (!sq) return;
    setChSearching(true); setChError(null);
    try {
      const res = await clawhubSearch(sq);
      setChResults(res.results || []);
    } catch (e: unknown) { setChError(e instanceof Error ? e.message : "搜索失败"); }
    finally { setChSearching(false); }
  }, [chQuery]);

  const handleChInstall = useCallback(async (slug: string) => {
    setChInstalling(slug); setChError(null); setChSuccessMsg(null);
    try {
      await clawhubInstall({ slug });
      setChJustInstalled(slug);
      setTimeout(() => setChJustInstalled(null), 2000);
      setChSuccessMsg(`已安装 ${slug}`);
      fetchSkills(true);
    } catch (e: unknown) { setChError(e instanceof Error ? e.message : "安装失败"); }
    finally { setChInstalling(null); }
  }, [fetchSkills]);

  const handleChShowDetail = useCallback(async (slug: string) => {
    try { const d = await clawhubSkillDetail(slug); setChDetail(d); } catch { /* ignore */ }
  }, []);

  const handleChLoadInstalled = useCallback(async () => {
    setChSearching(true); setChError(null);
    try {
      const [instRes, updRes] = await Promise.all([clawhubListInstalled(), clawhubCheckUpdates()]);
      setChInstalled(instRes.installed || []); setChUpdates(updRes.updates || []);
    } catch (e: unknown) { setChError(e instanceof Error ? e.message : "加载失败"); }
    finally { setChSearching(false); }
  }, []);

  const handleChUpdate = useCallback(async (slug: string) => {
    setChUpdating(slug); setChError(null); setChSuccessMsg(null);
    try { await clawhubUpdate({ slug }); setChSuccessMsg(`已更新 ${slug}`); handleChLoadInstalled(); }
    catch (e: unknown) { setChError(e instanceof Error ? e.message : "更新失败"); }
    finally { setChUpdating(null); }
  }, [handleChLoadInstalled]);

  const handleChUpdateAll = useCallback(async () => {
    setChUpdating("__all__"); setChError(null); setChSuccessMsg(null);
    try { await clawhubUpdate({ all: true }); setChSuccessMsg("已更新所有技能"); handleChLoadInstalled(); }
    catch (e: unknown) { setChError(e instanceof Error ? e.message : "批量更新失败"); }
    finally { setChUpdating(null); }
  }, [handleChLoadInstalled]);

  /* ── Render ── */

  if (loading && skills.length === 0) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* ══ Top Navigation: Installed / Market + Import button ══ */}
      <div className="flex items-center gap-1.5 sm:gap-2">
        <div className="flex items-center gap-1 flex-shrink-0">
          {([
            { key: "installed" as const, label: "已安装", icon: Package, count: skills.length },
            { key: "market" as const, label: "市场", icon: Sparkles },
          ]).map(({ key, label, icon: Icon, count }) => {
            const isActive = topView === key;
            return (
              <button
                key={key}
                type="button"
                className={`relative inline-flex items-center gap-1 sm:gap-1.5 px-2.5 sm:px-3 py-2 sm:py-1.5 rounded-full text-xs font-medium transition-colors whitespace-nowrap ${
                  isActive ? "text-white" : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                }`}
                style={isActive ? { backgroundColor: "var(--em-primary)" } : undefined}
                onClick={() => {
                  setTopView(key);
                  if (key === "market" && chInstalled.length === 0) handleChLoadInstalled();
                }}
              >
                <Icon className="h-3.5 w-3.5" />
                {label}
                {typeof count === "number" && (
                  <span className={`text-[10px] ${isActive ? "text-white/70" : "text-muted-foreground/60"}`}>
                    {count}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        {topView === "installed" && (
          <>
            <Button
              size="sm"
              variant="outline"
              className="h-8 sm:h-7 text-xs gap-1 flex-shrink-0"
              onClick={() => { resetImportState(); setShowImport(true); setImportMethod("file"); }}
            >
              <Plus className="h-3 w-3" />
              <span className="hidden sm:inline">导入</span>
            </Button>
            <div className="h-4 w-px bg-border/60 flex-shrink-0 hidden sm:block" />
            <div className="flex-1 min-w-0 overflow-x-auto scrollbar-none">
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
          </>
        )}
      </div>

      {/* ══ Installed View ══ */}
      {topView === "installed" && (
        <>

          {/* Import panel */}
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

          {/* Edit form */}
          {editingSkill && (
            <div className="rounded-lg border border-dashed border-border p-3 space-y-3">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 min-w-0">
                  <Pencil className="h-3.5 w-3.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
                  <span className="text-xs font-medium truncate">编辑: {editingSkill}</span>
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

          {/* Skills list */}
          <div className="space-y-1.5" data-coach-id="coach-settings-skills-list">
            <DemoSkillsBanner />
            {filteredSkills.length === 0 && !isSettingsDemoActive() && (
              <div className="text-center py-8">
                <Sparkles className="h-8 w-8 mx-auto mb-2 text-muted-foreground/30" />
                <p className="text-xs text-muted-foreground">
                  {sourceFilter === "all" ? "暂无已加载的技能包" : `暂无 ${SOURCE_LABELS[sourceFilter]} 类技能`}
                </p>
              </div>
            )}
            {filteredSkills.map((skill) => {
              const isExpanded = expandedSkill === skill.name;
              return (
                <div key={skill.name} className="rounded-lg border border-border overflow-hidden">
                  <div
                    role="button" tabIndex={0}
                    className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-muted/30 transition-colors overflow-hidden cursor-pointer"
                    onClick={() => toggleExpand(skill.name)}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleExpand(skill.name); } }}
                  >
                    <span className="text-muted-foreground transition-transform flex-shrink-0" style={{ transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)" }}>
                      <ChevronRight className="h-3.5 w-3.5" />
                    </span>
                    <Package className="h-3.5 w-3.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
                    <span className="text-sm font-medium truncate min-w-0">{skill.name}</span>
                    <Badge className={`text-[9px] px-1.5 py-0 shrink-0 border-0 ${SOURCE_COLORS[skill.source] || "bg-muted text-muted-foreground"}`} variant="secondary">
                      {SOURCE_LABELS[skill.source] || skill.source}
                    </Badge>
                    {skill.writable && <Badge variant="outline" className="text-[9px] px-1 py-0 shrink-0 hidden sm:inline-flex">可写</Badge>}
                    <span className="text-[11px] text-muted-foreground truncate hidden sm:inline ml-auto">{skill.description}</span>
                    {skill.writable && (
                      <div className="flex gap-0.5 shrink-0 ml-auto sm:ml-0" onClick={(e) => e.stopPropagation()}>
                        <Button variant="ghost" size="icon" className="h-6 w-6" title="编辑" onClick={() => { if (!skillDetails[skill.name]) fetchDetail(skill.name); startEdit(skill); }}>
                          <Pencil className="h-3 w-3" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-6 w-6 text-destructive" title="删除" onClick={() => handleDelete(skill.name)}>
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </div>
                    )}
                  </div>
                  {isExpanded && (
                    <div className="border-t border-border/50 px-4 py-3 space-y-2.5 bg-muted/20">
                      <p className="text-[11px] text-muted-foreground sm:hidden">{skill.description}</p>
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
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* ══ Market View ══ */}
      {topView === "market" && (
        <div className="space-y-3">
          {/* Messages */}
          <AnimatePresence>
            {chError && (
              <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
                <div className="flex items-start gap-2 px-3 py-2 text-xs rounded-lg bg-destructive/10 text-destructive border border-destructive/20">
                  <AlertCircle className="h-3.5 w-3.5 flex-shrink-0 mt-0.5" />
                  <span className="break-words flex-1">{chError}</span>
                  <button onClick={() => setChError(null)} className="flex-shrink-0 opacity-60 hover:opacity-100 transition-opacity"><X className="h-3 w-3" /></button>
                </div>
              </motion.div>
            )}
            {chSuccessMsg && (
              <motion.div initial={{ opacity: 0, height: 0, scale: 0.95 }} animate={{ opacity: 1, height: "auto", scale: 1 }} exit={{ opacity: 0, height: 0, scale: 0.95 }} className="overflow-hidden">
                <div className="flex items-center gap-2 px-3 py-2 text-xs rounded-lg bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20">
                  <CheckCircle2 className="h-3.5 w-3.5 flex-shrink-0" />
                  <span className="flex-1">{chSuccessMsg}</span>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <AnimatePresence mode="wait">
            <motion.div
              key={chDetail ? "detail" : "list"}
              initial={{ opacity: 0, x: 8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -8 }}
              transition={{ duration: 0.18, ease: "easeOut" }}
            >
              {chDetail ? (
                <MarketDetailView detail={chDetail} onBack={() => setChDetail(null)} onInstall={handleChInstall} installing={chInstalling} />
              ) : (
                <MarketSearchView
                  query={chQuery} setQuery={setChQuery} onSearch={handleChSearch}
                  results={chResults} loading={chSearching}
                  installing={chInstalling} justInstalled={chJustInstalled}
                  onInstall={handleChInstall} onDetail={handleChShowDetail}
                  searchInputRef={chSearchRef}
                  installed={chInstalled} updatesMap={new Map(chUpdates.map(u => [u.slug, u]))}
                  updating={chUpdating} onUpdate={handleChUpdate} onUpdateAll={handleChUpdateAll}
                  onDelete={async (slug: string) => { if (!confirm(`确定卸载技能「${slug}」？`)) return; try { await apiDelete(`/skills/${encodeURIComponent(slug)}`); fetchSkills(true); handleChLoadInstalled(); } catch (e) { setChError(e instanceof Error ? e.message : "卸载失败"); } }}
                />
              )}
            </motion.div>
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}

/* ══════════════════════════════════════════════════════════
   Market Sub-components
   ══════════════════════════════════════════════════════════ */

function MarketSearchView({
  query, setQuery, onSearch, results, loading,
  installing, justInstalled, onInstall, onDetail, searchInputRef,
  installed, updatesMap, updating, onUpdate, onUpdateAll, onDelete,
}: {
  query: string; setQuery: (q: string) => void; onSearch: (q?: string) => void;
  results: ClawHubSearchResult[]; loading: boolean;
  installing: string | null; justInstalled: string | null;
  onInstall: (slug: string) => void; onDetail: (slug: string) => void;
  searchInputRef: React.RefObject<HTMLInputElement | null>;
  installed: ClawHubInstalled[]; updatesMap: Map<string, ClawHubUpdateInfo>;
  updating: string | null; onUpdate: (slug: string) => void; onUpdateAll: () => void;
  onDelete: (slug: string) => void;
}) {
  const hasUpdates = Array.from(updatesMap.values()).some(u => u.update_available);
  const updatableCount = Array.from(updatesMap.values()).filter(u => u.update_available).length;

  return (
    <div className="space-y-4">
      {/* Search bar */}
      <div className="flex gap-2">
        <div className="flex-1 relative group">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground/60 group-focus-within:text-[var(--em-primary)] transition-colors pointer-events-none" />
          <Input
            ref={searchInputRef}
            value={query} onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onSearch()}
            placeholder="搜索技能包..."
            className="h-9 text-xs pl-9 pr-8 rounded-xl bg-muted/30 border-transparent hover:bg-muted/50 focus:bg-background focus:border-[var(--em-primary-alpha-30)] focus:ring-1 focus:ring-[var(--em-primary-alpha-15)] transition-all placeholder:text-muted-foreground/40"
          />
          {query && (
            <button onClick={() => { setQuery(""); searchInputRef.current?.focus(); }} className="absolute right-2.5 top-1/2 -translate-y-1/2 p-0.5 rounded-full text-muted-foreground/40 hover:text-muted-foreground hover:bg-muted/60 transition-all">
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
        <Button
          size="sm"
          className="h-9 text-xs gap-1.5 text-white px-3 sm:px-4 rounded-xl font-medium clawhub-search-btn"
          style={{ backgroundColor: "var(--em-primary)" }}
          disabled={loading || !query.trim()} onClick={() => onSearch()}
        >
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
          <span className="hidden sm:inline">搜索</span>
        </Button>
      </div>

      {/* Empty state with suggestions */}
      {results.length === 0 && !loading && !query && (
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4, delay: 0.1, ease: "easeOut" }} className="py-4 sm:py-6">
          <div className="text-center mb-6">
            <div className="relative inline-block">
              <div className="animate-empty-float">
                <div className="w-16 h-16 rounded-2xl mx-auto flex items-center justify-center clawhub-empty-icon" style={{ backgroundColor: "var(--em-primary-alpha-08)" }}>
                  <Store className="h-8 w-8" style={{ color: "var(--em-primary)", opacity: 0.8 }} />
                </div>
              </div>
              <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-10 h-1.5 rounded-full bg-black/5 dark:bg-white/5 blur-sm" />
            </div>
            <h3 className="text-sm sm:text-[15px] font-semibold mt-3 sm:mt-4 mb-1 sm:mb-1.5">探索技能市场</h3>
            <p className="text-[11px] text-muted-foreground/60 max-w-[220px] mx-auto leading-relaxed">搜索关键词发现社区技能包，扩展你的 AI 工作流</p>
          </div>
          <div className="space-y-2.5">
            <p className="text-[10px] font-medium text-muted-foreground/50 uppercase tracking-widest px-1">热门分类</p>
            <div className="flex flex-wrap gap-2">
              {SUGGESTED_TAGS.map(({ label, icon: Icon }) => (
                <button key={label} onClick={() => { setQuery(label); onSearch(label); }} className="clawhub-tag inline-flex items-center gap-1.5 px-3 py-2 sm:py-2 rounded-xl text-[11px] font-medium bg-muted/40 text-muted-foreground hover:text-foreground hover:bg-[var(--em-primary-alpha-08)] active:scale-95 transition-all">
                  <Icon className="h-3.5 w-3.5 opacity-60" />{label}
                </button>
              ))}
            </div>
          </div>
          <p className="text-[10px] text-muted-foreground/30 mt-6 text-center">
            来自 <a href="https://clawhub.ai" target="_blank" rel="noopener noreferrer" className="underline decoration-dotted underline-offset-2 hover:text-muted-foreground/50 transition-colors">clawhub.ai</a>
          </p>
        </motion.div>
      )}

      {/* No results */}
      {results.length === 0 && !loading && query && (
        <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} transition={{ duration: 0.3 }} className="text-center py-10">
          <div className="w-14 h-14 rounded-2xl mx-auto mb-4 flex items-center justify-center bg-muted/40">
            <Search className="h-6 w-6 text-muted-foreground/25" />
          </div>
          <p className="text-[13px] text-muted-foreground font-medium">未找到「{query}」相关技能</p>
          <p className="text-[11px] text-muted-foreground/40 mt-1.5">尝试不同的关键词或浏览热门分类</p>
        </motion.div>
      )}

      {/* Loading skeletons */}
      {loading && (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <motion.div key={i} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.1, duration: 0.3 }}>
              <div className="rounded-2xl border border-border/40 p-3 sm:p-4">
                <div className="flex items-start gap-3 sm:gap-3.5">
                  <div className="w-10 h-10 sm:w-11 sm:h-11 rounded-xl clawhub-skeleton flex-shrink-0" />
                  <div className="flex-1 space-y-2 sm:space-y-2.5 pt-0.5">
                    <div className="h-4 w-3/5 clawhub-skeleton rounded-lg" />
                    <div className="h-3 w-full clawhub-skeleton rounded-lg" />
                    <div className="h-3 w-2/5 clawhub-skeleton rounded-lg" />
                  </div>
                  <div className="w-16 h-8 clawhub-skeleton rounded-xl flex-shrink-0 hidden sm:block" />
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      )}

      {/* Result count */}
      {results.length > 0 && !loading && (
        <div className="flex items-center gap-3 px-1">
          <div className="h-px flex-1 bg-gradient-to-r from-transparent via-border/60 to-transparent" />
          <span className="text-[10px] text-muted-foreground/40 font-medium tabular-nums">{results.length} 个结果</span>
          <div className="h-px flex-1 bg-gradient-to-r from-transparent via-border/60 to-transparent" />
        </div>
      )}

      {/* Results */}
      <div className="space-y-2.5">
        {results.map((r, i) => (
          <motion.div key={r.slug} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25, delay: i * 0.05, ease: "easeOut" }}>
            <MarketSkillCard
              slug={r.slug} name={r.display_name} summary={r.summary} version={r.version} score={r.score}
              onInstall={() => onInstall(r.slug)} onDetail={() => onDetail(r.slug)}
              installing={installing === r.slug} justInstalled={justInstalled === r.slug} action="install"
            />
          </motion.div>
        ))}
      </div>

      {/* Installed from ClawHub section */}
      {installed.length > 0 && !query && results.length === 0 && !loading && (
        <div className="space-y-2.5 pt-3">
          <div className="flex items-center gap-3 px-1">
            <div className="h-px flex-1 bg-gradient-to-r from-transparent via-border/60 to-transparent" />
            <span className="inline-flex items-center gap-1.5 text-[10px] text-muted-foreground/40 font-medium">
              <CheckCircle2 className="h-3 w-3" />
              已从市场安装 {installed.length} 个
            </span>
            <div className="h-px flex-1 bg-gradient-to-r from-transparent via-border/60 to-transparent" />
          </div>
          {hasUpdates && (
            <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}>
              <Button variant="outline" size="sm" className="w-full h-10 sm:h-9 text-xs gap-1.5 rounded-xl border-amber-500/25 text-amber-600 dark:text-amber-400 hover:bg-amber-500/8 hover:border-amber-500/40 active:scale-[0.98] transition-all font-medium" disabled={updating !== null} onClick={onUpdateAll}>
                {updating === "__all__" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowUpCircle className="h-3.5 w-3.5" />}
                全部更新 ({updatableCount})
              </Button>
            </motion.div>
          )}
          {installed.map((item, i) => {
            const upd = updatesMap.get(item.slug);
            return (
              <motion.div key={item.slug} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25, delay: i * 0.05 }}>
                <MarketSkillCard
                  slug={item.slug} name={item.slug} version={item.version}
                  onInstall={() => onUpdate(item.slug)} onDetail={() => onDetail(item.slug)}
                  installing={updating === item.slug}
                  action={upd?.update_available ? "update" : undefined}
                  updateVersion={upd?.latest_version ?? undefined}
                  isInstalled
                  onDelete={() => onDelete(item.slug)}
                />
              </motion.div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function MarketSkillCard({
  slug, name, summary, version, score,
  onInstall, onDetail, installing, justInstalled,
  action, updateVersion, isInstalled, onDelete,
}: {
  slug: string; name: string; summary?: string; version?: string | null; score?: number;
  onInstall: () => void; onDetail: () => void;
  installing: boolean; justInstalled?: boolean;
  action?: "install" | "update"; updateVersion?: string; isInstalled?: boolean;
  onDelete?: () => void;
}) {
  return (
    <div className={`clawhub-card rounded-2xl border overflow-hidden ${justInstalled ? "clawhub-install-success border-emerald-500/30 bg-emerald-500/[0.02]" : "border-border/50 hover:border-[var(--em-primary-alpha-20)]"}`}>
      <div className="flex items-start gap-2.5 sm:gap-3.5 p-3 sm:p-3.5">
        <button onClick={onDetail} className="flex items-center justify-center w-10 h-10 sm:w-11 sm:h-11 rounded-xl flex-shrink-0 transition-all hover:scale-105 active:scale-95" style={{ backgroundColor: isInstalled ? "var(--em-primary-alpha-12)" : "var(--em-primary-alpha-08)", boxShadow: isInstalled ? "0 2px 12px var(--em-primary-alpha-08)" : undefined }}>
          <Package className="h-4.5 w-4.5 sm:h-5 sm:w-5" style={{ color: "var(--em-primary)", opacity: 0.85 }} />
        </button>
        <div className="flex-1 min-w-0 pt-0.5">
          <div className="flex items-center gap-1.5 sm:gap-2 flex-wrap">
            <button onClick={onDetail} className="text-[12px] sm:text-[13px] font-semibold hover:underline underline-offset-2 truncate transition-colors leading-tight" style={{ color: "var(--em-primary)" }}>
              {name || slug}
            </button>
            {version && <Badge variant="secondary" className="text-[9px] px-1.5 py-0 font-mono h-[18px] rounded-lg bg-muted/60">v{version}</Badge>}
            {updateVersion && <Badge className="text-[9px] px-1.5 py-0 h-[18px] border-0 rounded-lg bg-amber-500/12 text-amber-600 dark:text-amber-400 font-medium">→ v{updateVersion}</Badge>}
          </div>
          {summary && <p className="text-[11px] text-muted-foreground/70 mt-1 sm:mt-1.5 line-clamp-2 leading-relaxed">{summary}</p>}
          <div className="flex items-center gap-2 sm:gap-2.5 mt-1.5 sm:mt-2">
            <p className="text-[10px] text-muted-foreground/35 font-mono truncate">{slug}</p>
            {typeof score === "number" && score > 0 && (
              <span className="inline-flex items-center gap-0.5 text-[9px] text-amber-500/80 font-medium"><Star className="h-2.5 w-2.5 fill-current" />{score.toFixed(1)}</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1 sm:gap-1.5 flex-shrink-0 pt-0.5">
          {action && (
            <Button
              size="sm"
              variant={action === "install" ? "default" : "outline"}
              className={`h-8 text-[11px] gap-1 rounded-xl font-medium active:scale-95 transition-all ${action === "update" ? "border-amber-500/25 text-amber-600 dark:text-amber-400 hover:bg-amber-500/8 hover:border-amber-500/40" : "text-white"}`}
              style={action === "install" ? { backgroundColor: "var(--em-primary)" } : undefined}
              disabled={installing} onClick={(e) => { e.stopPropagation(); onInstall(); }}
            >
              {installing ? <Loader2 className="h-3 w-3 animate-spin" /> : action === "update" ? <RefreshCw className="h-3 w-3" /> : <Download className="h-3 w-3" />}
              {action === "update" ? "更新" : "安装"}
            </Button>
          )}
          {isInstalled && !action && (
            <Badge variant="secondary" className="text-[9px] px-1.5 sm:px-2 py-0.5 rounded-lg text-emerald-600 dark:text-emerald-400 bg-emerald-500/8 border-0 gap-0.5">
              <CheckCircle2 className="h-2.5 w-2.5" />已安装
            </Badge>
          )}
          {isInstalled && onDelete && (
            <Button variant="ghost" size="icon" className="h-8 w-8 sm:h-7 sm:w-7 rounded-lg text-muted-foreground/30 hover:text-destructive hover:bg-destructive/8 active:scale-90 transition-all" title="卸载" onClick={(e) => { e.stopPropagation(); onDelete(); }}>
              <Trash2 className="h-3 w-3" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

function MarketDetailView({
  detail, onBack, onInstall, installing,
}: {
  detail: ClawHubSkillDetail; onBack: () => void;
  onInstall: (slug: string) => void; installing: string | null;
}) {
  const updatedAt = detail.updated_at
    ? new Date(detail.updated_at * 1000).toLocaleDateString("zh-CN", { year: "numeric", month: "short", day: "numeric" })
    : null;
  const hasStats = detail.stats && Object.keys(detail.stats).length > 0;

  return (
    <div className="space-y-4">
      <button onClick={onBack} className="inline-flex items-center gap-1 text-xs text-muted-foreground/60 hover:text-foreground -ml-0.5 py-2 sm:py-1 transition-colors group active:scale-95">
        <ChevronLeft className="h-3.5 w-3.5 transition-transform group-hover:-translate-x-0.5" />返回列表
      </button>

      {/* Hero */}
      <div className="clawhub-hero-gradient rounded-2xl p-4 sm:p-5">
        <div className="flex items-start gap-3 sm:gap-4">
          <div className="flex items-center justify-center w-12 h-12 sm:w-14 sm:h-14 rounded-xl sm:rounded-2xl flex-shrink-0" style={{ backgroundColor: "var(--em-primary-alpha-12)", boxShadow: "0 4px 20px var(--em-primary-alpha-10)" }}>
            <Package className="h-6 w-6 sm:h-7 sm:w-7" style={{ color: "var(--em-primary)", opacity: 0.9 }} />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-[15px] sm:text-[16px] font-bold leading-tight">{detail.display_name}</h3>
            <p className="text-[10px] text-muted-foreground/40 font-mono mt-0.5 sm:mt-1 truncate">{detail.slug}</p>
            <div className="flex items-center gap-2 sm:gap-3 mt-2 sm:mt-2.5 flex-wrap">
              {detail.owner_handle && (
                <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground/70 bg-muted/40 px-1.5 sm:px-2 py-0.5 rounded-lg">
                  <User className="h-3 w-3 opacity-60" />{detail.owner_display_name || detail.owner_handle}
                </span>
              )}
              {detail.latest_version && (
                <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground/70 bg-muted/40 px-1.5 sm:px-2 py-0.5 rounded-lg font-mono">
                  <GitBranch className="h-3 w-3 opacity-60" />v{detail.latest_version}
                </span>
              )}
              {updatedAt && (
                <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground/70 bg-muted/40 px-1.5 sm:px-2 py-0.5 rounded-lg">
                  <Clock className="h-3 w-3 opacity-60" />{updatedAt}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {detail.summary && <p className="text-[11px] sm:text-[12px] text-muted-foreground/80 leading-relaxed px-0.5 sm:px-1">{detail.summary}</p>}

      {hasStats && (
        <div className="flex flex-wrap gap-2 px-1">
          {Object.entries(detail.stats).map(([key, val]) => (
            <div key={key} className="clawhub-stat-badge inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-[10px] bg-muted/30 text-muted-foreground border border-border/30">
              <TrendingUp className="h-2.5 w-2.5 opacity-50" /><span className="font-semibold">{String(val)}</span><span className="text-muted-foreground/50">{key}</span>
            </div>
          ))}
        </div>
      )}

      {detail.tags && detail.tags.length > 0 && (
        <div className="px-1">
          <div className="flex items-center gap-1.5 mb-2.5"><Tag className="h-3 w-3 text-muted-foreground/40" /><span className="text-[10px] font-medium text-muted-foreground/40 uppercase tracking-widest">标签</span></div>
          <div className="flex flex-wrap gap-1.5">
            {detail.tags.map((tag, i) => <Badge key={i} variant="outline" className="text-[10px] px-2.5 py-0.5 font-normal rounded-xl border-border/40 text-muted-foreground/70 hover:bg-muted/30 transition-colors">{String(tag)}</Badge>)}
          </div>
        </div>
      )}

      {detail.latest_changelog && (
        <div className="px-1">
          <div className="flex items-center gap-1.5 mb-2.5"><GitBranch className="h-3 w-3 text-muted-foreground/40" /><span className="text-[10px] font-medium text-muted-foreground/40 uppercase tracking-widest">更新日志</span></div>
          <pre className="text-[11px] font-mono bg-muted/20 rounded-xl p-3 sm:p-3.5 max-h-28 sm:max-h-36 overflow-auto whitespace-pre-wrap break-words border border-border/30 leading-relaxed text-muted-foreground/80">{detail.latest_changelog}</pre>
        </div>
      )}

      <div className="h-px mx-1 bg-gradient-to-r from-transparent via-border/50 to-transparent" />

      <div className="flex flex-col sm:flex-row gap-2 sm:gap-2.5 px-0.5 sm:px-1">
        <Button size="sm" className="h-11 sm:h-10 text-xs gap-1.5 text-white flex-1 rounded-xl font-medium active:scale-[0.98] transition-all" style={{ backgroundColor: "var(--em-primary)" }} disabled={installing === detail.slug} onClick={() => onInstall(detail.slug)}>
          {installing === detail.slug ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
          安装技能
        </Button>
        <Button variant="outline" size="sm" className="h-11 sm:h-10 text-xs gap-1.5 rounded-xl border-border/50 hover:bg-muted/30 active:scale-[0.98] transition-all" asChild>
          <a href={`https://clawhub.ai/skills/${detail.slug}`} target="_blank" rel="noopener noreferrer">
            <ExternalLink className="h-3.5 w-3.5" />在线查看
          </a>
        </Button>
      </div>
    </div>
  );
}
