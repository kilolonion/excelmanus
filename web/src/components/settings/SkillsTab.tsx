"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Plus,
  Trash2,
  Pencil,
  Save,
  X,
  Loader2,
  Package,
  ChevronDown,
  ChevronRight,
  Code,
  FileText,
  FolderOpen,
  Github,
  Download,
  CheckCircle2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { apiGet, apiPost, apiPatch, apiDelete } from "@/lib/api";
import { MiniCheckbox } from "@/components/ui/MiniCheckbox";

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
  system: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
  user: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
  project: "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300",
};

type ImportTab = "file" | "github" | "manual";

export function SkillsTab() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null);
  const [skillDetails, setSkillDetails] = useState<Record<string, SkillDetail>>({});
  const [showCreate, setShowCreate] = useState(false);
  const [editingSkill, setEditingSkill] = useState<string | null>(null);
  const [importTab, setImportTab] = useState<ImportTab>("file");
  const [saving, setSaving] = useState(false);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);

  // Import: file path
  const [filePath, setFilePath] = useState("");
  const [fileOverwrite, setFileOverwrite] = useState(false);

  // Import: GitHub URL
  const [githubUrl, setGithubUrl] = useState("");
  const [githubOverwrite, setGithubOverwrite] = useState(false);

  // Import: manual create
  const [formDraft, setFormDraft] = useState({
    name: "",
    description: "",
    instructions: "",
    filePatterns: "",
  });

  // Edit mode
  const [jsonDraft, setJsonDraft] = useState("");
  const [editMode, setEditMode] = useState<"form" | "json">("form");

  const fetchSkills = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiGet<SkillSummary[]>("/skills");
      setSkills(data);
    } catch {
      // Backend not ready
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  const fetchDetail = async (name: string) => {
    try {
      const data = await apiGet<SkillDetail>(`/skills/${encodeURIComponent(name)}`);
      setSkillDetails((prev) => ({ ...prev, [name]: data }));
    } catch {
      // ignore
    }
  };

  const toggleExpand = (name: string) => {
    if (expandedSkill === name) {
      setExpandedSkill(null);
    } else {
      setExpandedSkill(name);
      if (!skillDetails[name]) {
        fetchDetail(name);
      }
    }
  };

  const resetImportState = () => {
    setShowCreate(false);
    setEditingSkill(null);
    setImportResult(null);
    setFilePath("");
    setFileOverwrite(false);
    setGithubUrl("");
    setGithubOverwrite(false);
    setFormDraft({ name: "", description: "", instructions: "", filePatterns: "" });
    setJsonDraft("");
  };

  const handleImportFromFile = async () => {
    if (!filePath.trim()) return;
    setSaving(true);
    setImportResult(null);
    try {
      const result = await apiPost<ImportResult>("/skills/import", {
        source: "local_path",
        value: filePath.trim(),
        overwrite: fileOverwrite,
      });
      setImportResult(result);
      fetchSkills();
    } catch (err) {
      alert(err instanceof Error ? err.message : "导入失败");
    } finally {
      setSaving(false);
    }
  };

  const handleImportFromGithub = async () => {
    if (!githubUrl.trim()) return;
    setSaving(true);
    setImportResult(null);
    try {
      const result = await apiPost<ImportResult>("/skills/import", {
        source: "github_url",
        value: githubUrl.trim(),
        overwrite: githubOverwrite,
      });
      setImportResult(result);
      fetchSkills();
    } catch (err) {
      alert(err instanceof Error ? err.message : "导入失败");
    } finally {
      setSaving(false);
    }
  };

  const handleManualCreate = async () => {
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        description: formDraft.description,
        instructions: formDraft.instructions,
      };
      if (formDraft.filePatterns.trim()) {
        payload["file-patterns"] = formDraft.filePatterns
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
      }
      await apiPost("/skills", { name: formDraft.name, payload });
      resetImportState();
      fetchSkills();
    } catch (err) {
      alert(err instanceof Error ? err.message : "创建失败");
    } finally {
      setSaving(false);
    }
  };

  const handleEdit = async (name: string) => {
    setSaving(true);
    try {
      if (editMode === "json") {
        const parsed = JSON.parse(jsonDraft);
        await apiPatch(`/skills/${encodeURIComponent(name)}`, { payload: parsed });
      } else {
        const payload: Record<string, unknown> = {
          description: formDraft.description,
          instructions: formDraft.instructions,
        };
        if (formDraft.filePatterns.trim()) {
          payload["file-patterns"] = formDraft.filePatterns
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean);
        }
        await apiPatch(`/skills/${encodeURIComponent(name)}`, { payload });
      }
      setEditingSkill(null);
      setJsonDraft("");
      setFormDraft({ name: "", description: "", instructions: "", filePatterns: "" });
      delete skillDetails[name];
      fetchSkills();
    } catch (err) {
      alert(err instanceof Error ? err.message : "更新失败");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (name: string) => {
    if (!confirm(`确定删除技能 "${name}"？`)) return;
    try {
      await apiDelete(`/skills/${encodeURIComponent(name)}`);
      fetchSkills();
    } catch (err) {
      alert(err instanceof Error ? err.message : "删除失败");
    }
  };

  const startEdit = (skill: SkillSummary) => {
    const detail = skillDetails[skill.name];
    if (detail) {
      setFormDraft({
        name: detail.name,
        description: detail.description,
        instructions: detail.instructions || "",
        filePatterns: (detail["file-patterns"] || []).join(", "),
      });
      setJsonDraft(JSON.stringify(detail, null, 2));
    } else {
      setFormDraft({
        name: skill.name,
        description: skill.description,
        instructions: "",
        filePatterns: "",
      });
    }
    setEditingSkill(skill.name);
    setShowCreate(false);
    setEditMode("form");
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 flex-wrap min-w-0">
          <Badge className={`text-[10px] px-1.5 py-0 ${SOURCE_COLORS.system}`} variant="secondary">system</Badge>
          <Badge className={`text-[10px] px-1.5 py-0 ${SOURCE_COLORS.user}`} variant="secondary">user</Badge>
          <Badge className={`text-[10px] px-1.5 py-0 ${SOURCE_COLORS.project}`} variant="secondary">project</Badge>
          <span className="text-xs text-muted-foreground">{skills.length} 个技能</span>
        </div>
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs gap-1 flex-shrink-0"
          onClick={() => {
            resetImportState();
            setShowCreate(true);
            setImportTab("file");
          }}
        >
          <Plus className="h-3 w-3" />
          导入技能
        </Button>
      </div>

      {/* Import panel */}
      {showCreate && !editingSkill && (
        <div className="rounded-lg border border-dashed border-border p-3 space-y-3">
          <div className="flex flex-col sm:flex-row sm:items-center gap-2">
            <span className="text-xs font-medium">导入技能</span>
            <div className="sm:ml-auto flex gap-1 flex-wrap">
              <Button
                size="sm"
                variant={importTab === "file" ? "default" : "ghost"}
                className="h-7 sm:h-6 text-[10px] px-2 gap-1"
                onClick={() => { setImportTab("file"); setImportResult(null); }}
              >
                <FolderOpen className="h-3 w-3" />
                文件路径
              </Button>
              <Button
                size="sm"
                variant={importTab === "github" ? "default" : "ghost"}
                className="h-7 sm:h-6 text-[10px] px-2 gap-1"
                onClick={() => { setImportTab("github"); setImportResult(null); }}
              >
                <Github className="h-3 w-3" />
                GitHub
              </Button>
              <Button
                size="sm"
                variant={importTab === "manual" ? "default" : "ghost"}
                className="h-7 sm:h-6 text-[10px] px-2 gap-1"
                onClick={() => { setImportTab("manual"); setImportResult(null); }}
              >
                <Pencil className="h-3 w-3" />
                手动创建
              </Button>
            </div>
          </div>

          {/* File path import */}
          {importTab === "file" && (
            <div className="space-y-2">
              <div>
                <label className="text-xs text-muted-foreground">
                  SKILL.md 文件路径（本地绝对路径）
                </label>
                <Input
                  value={filePath}
                  onChange={(e) => setFilePath(e.target.value)}
                  className="h-7 text-xs font-mono"
                  placeholder="/path/to/my-skill/SKILL.md"
                />
              </div>
              <p className="text-[10px] text-muted-foreground">
                将自动扫描同目录下的附属文件（scripts/、references/ 等）一并导入
              </p>
              <MiniCheckbox checked={fileOverwrite} onChange={setFileOverwrite} label="覆盖已存在的同名技能" />
            </div>
          )}

          {/* GitHub URL import */}
          {importTab === "github" && (
            <div className="space-y-2">
              <div>
                <label className="text-xs text-muted-foreground">
                  GitHub SKILL.md 链接
                </label>
                <Input
                  value={githubUrl}
                  onChange={(e) => setGithubUrl(e.target.value)}
                  className="h-7 text-xs font-mono"
                  placeholder="https://github.com/org/repo/blob/main/skills/my-skill/SKILL.md"
                />
              </div>
              <p className="text-[10px] text-muted-foreground">
                支持 github.com/.../blob/... 和 raw.githubusercontent.com 格式。
                自动通过 GitHub API 拉取同目录附属文件。
              </p>
              <MiniCheckbox checked={githubOverwrite} onChange={setGithubOverwrite} label="覆盖已存在的同名技能" />
            </div>
          )}

          {/* Manual create */}
          {importTab === "manual" && (
            <div className="space-y-2">
              <div>
                <label className="text-xs text-muted-foreground">名称 *</label>
                <Input
                  value={formDraft.name}
                  onChange={(e) => setFormDraft((d) => ({ ...d, name: e.target.value }))}
                  className="h-7 text-xs"
                  placeholder="skill-name"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">描述 *</label>
                <Input
                  value={formDraft.description}
                  onChange={(e) => setFormDraft((d) => ({ ...d, description: e.target.value }))}
                  className="h-7 text-xs"
                  placeholder="技能描述"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Instructions</label>
                <textarea
                  value={formDraft.instructions}
                  onChange={(e) => setFormDraft((d) => ({ ...d, instructions: e.target.value }))}
                  className="w-full h-20 rounded-md border border-input bg-background px-3 py-1 text-xs font-mono resize-y"
                  placeholder="技能指令..."
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">
                  文件模式（逗号分隔）
                </label>
                <Input
                  value={formDraft.filePatterns}
                  onChange={(e) =>
                    setFormDraft((d) => ({ ...d, filePatterns: e.target.value }))
                  }
                  className="h-7 text-xs font-mono"
                  placeholder="*.xlsx, *.csv"
                />
              </div>
            </div>
          )}

          {/* Import result */}
          {importResult && importResult.detail && (
            <div className="rounded-md border border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-950 p-2 space-y-1">
              <div className="flex items-center gap-1.5">
                <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />
                <span className="text-xs font-medium text-green-700 dark:text-green-300">
                  已导入: {importResult.name}
                </span>
              </div>
              {importResult.detail.description && (
                <p className="text-[10px] text-green-600 dark:text-green-400">
                  {importResult.detail.description}
                </p>
              )}
              {importResult.detail.files_copied.length > 0 && (
                <div className="text-[10px] text-green-600 dark:text-green-400">
                  {importResult.detail.files_copied.length} 个文件：
                  {importResult.detail.files_copied.map((f) => (
                    <span key={f} className="font-mono ml-1">{f}</span>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Action buttons */}
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs gap-1"
              onClick={resetImportState}
            >
              <X className="h-3 w-3" /> 取消
            </Button>
            {importTab === "file" && (
              <Button
                size="sm"
                className="h-7 text-xs gap-1 text-white"
                style={{ backgroundColor: "var(--em-primary)" }}
                disabled={saving || !filePath.trim()}
                onClick={handleImportFromFile}
              >
                {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
                导入
              </Button>
            )}
            {importTab === "github" && (
              <Button
                size="sm"
                className="h-7 text-xs gap-1 text-white"
                style={{ backgroundColor: "var(--em-primary)" }}
                disabled={saving || !githubUrl.trim()}
                onClick={handleImportFromGithub}
              >
                {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
                导入
              </Button>
            )}
            {importTab === "manual" && (
              <Button
                size="sm"
                className="h-7 text-xs gap-1 text-white"
                style={{ backgroundColor: "var(--em-primary)" }}
                disabled={saving || !formDraft.name || !formDraft.description}
                onClick={handleManualCreate}
              >
                {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                创建
              </Button>
            )}
          </div>
        </div>
      )}

      {/* Edit form */}
      {editingSkill && (
        <div className="rounded-lg border border-dashed border-border p-3 space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-medium">编辑: {editingSkill}</span>
            <div className="ml-auto flex gap-1">
              <Button
                size="sm"
                variant={editMode === "form" ? "default" : "ghost"}
                className="h-6 text-[10px] px-2"
                onClick={() => setEditMode("form")}
              >
                表单
              </Button>
              <Button
                size="sm"
                variant={editMode === "json" ? "default" : "ghost"}
                className="h-6 text-[10px] px-2"
                onClick={() => setEditMode("json")}
              >
                JSON
              </Button>
            </div>
          </div>

          {editMode === "form" ? (
            <div className="space-y-2">
              <div>
                <label className="text-xs text-muted-foreground">描述</label>
                <Input
                  value={formDraft.description}
                  onChange={(e) => setFormDraft((d) => ({ ...d, description: e.target.value }))}
                  className="h-7 text-xs"
                  placeholder="技能描述"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Instructions</label>
                <textarea
                  value={formDraft.instructions}
                  onChange={(e) => setFormDraft((d) => ({ ...d, instructions: e.target.value }))}
                  className="w-full h-20 rounded-md border border-input bg-background px-3 py-1 text-xs font-mono resize-y"
                  placeholder="技能指令..."
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">
                  文件模式（逗号分隔）
                </label>
                <Input
                  value={formDraft.filePatterns}
                  onChange={(e) =>
                    setFormDraft((d) => ({ ...d, filePatterns: e.target.value }))
                  }
                  className="h-7 text-xs font-mono"
                  placeholder="*.xlsx, *.csv"
                />
              </div>
            </div>
          ) : (
            <textarea
              value={jsonDraft}
              onChange={(e) => setJsonDraft(e.target.value)}
              className="w-full h-32 rounded-md border border-input bg-background px-3 py-2 text-xs font-mono resize-y"
              placeholder='{"description": "...", "instructions": "..."}'
            />
          )}

          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs gap-1"
              onClick={() => {
                setEditingSkill(null);
                setFormDraft({ name: "", description: "", instructions: "", filePatterns: "" });
                setJsonDraft("");
              }}
            >
              <X className="h-3 w-3" /> 取消
            </Button>
            <Button
              size="sm"
              className="h-7 text-xs gap-1 text-white"
              style={{ backgroundColor: "var(--em-primary)" }}
              disabled={saving}
              onClick={() => handleEdit(editingSkill)}
            >
              {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
              更新
            </Button>
          </div>
        </div>
      )}

      {/* Skills list */}
      <div className="space-y-2">
          {skills.length === 0 && (
            <p className="text-xs text-muted-foreground text-center py-8">
              暂无已加载的技能包
            </p>
          )}
          {skills.map((skill) => (
            <div key={skill.name} className="rounded-lg border border-border overflow-hidden">
              {/* Card header */}
              <div
                className="px-3 py-3 sm:py-2.5 cursor-pointer hover:bg-muted/50 active:bg-muted/60 transition-colors"
                onClick={() =>
                  setExpandedSkill(expandedSkill === skill.name ? null : skill.name)
                }
              >
                {/* Row 1: icon + name + action buttons */}
                <div className="flex items-center gap-2">
                  <Package className="h-3.5 w-3.5 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
                  <span className="text-sm font-medium truncate flex-1 min-w-0">{skill.name}</span>
                  {skill.writable && (
                    <div className="flex gap-0.5 shrink-0" onClick={(e) => e.stopPropagation()}>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6"
                        onClick={() => {
                          if (!skillDetails[skill.name]) fetchDetail(skill.name);
                          startEdit(skill);
                        }}
                      >
                        <Pencil className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 text-destructive"
                        onClick={() => handleDelete(skill.name)}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  )}
                </div>
                {/* Row 2: source badge + writable + description */}
                <div className="flex items-center gap-1.5 mt-1 ml-[22px]">
                  <Badge
                    className={`text-[10px] px-1.5 py-0 shrink-0 ${SOURCE_COLORS[skill.source] || ""}`}
                    variant="secondary"
                  >
                    {skill.source}
                  </Badge>
                  {skill.writable && (
                    <Badge variant="outline" className="text-[10px] px-1 py-0 shrink-0">
                      可写
                    </Badge>
                  )}
                  <span className="text-[11px] text-muted-foreground truncate">
                    {skill.description}
                  </span>
                </div>
              </div>

              {/* Expanded detail */}
              {expandedSkill === skill.name && (
                <div className="border-t border-border px-3 py-2.5 space-y-2 bg-muted/30">
                  {skillDetails[skill.name] ? (
                    <>
                      <div className="flex flex-wrap gap-1.5">
                        {skillDetails[skill.name].version && (
                          <Badge variant="outline" className="text-[10px]">
                            v{skillDetails[skill.name].version}
                          </Badge>
                        )}
                        {(skillDetails[skill.name]["file-patterns"] || []).map((p) => (
                          <Badge key={p} variant="secondary" className="text-[10px] font-mono">
                            {p}
                          </Badge>
                        ))}
                      </div>
                      {skillDetails[skill.name].instructions && (
                        <div>
                          <div className="flex items-center gap-1 mb-1">
                            <FileText className="h-3 w-3 text-muted-foreground" />
                            <span className="text-[10px] font-medium text-muted-foreground">
                              Instructions
                            </span>
                          </div>
                          <pre className="text-[11px] font-mono bg-background rounded p-2 max-h-32 overflow-auto whitespace-pre-wrap border">
                            {skillDetails[skill.name].instructions.slice(0, 500)}
                            {skillDetails[skill.name].instructions.length > 500 && "..."}
                          </pre>
                        </div>
                      )}
                      {skillDetails[skill.name].resources.length > 0 && (
                        <div>
                          <div className="flex items-center gap-1 mb-1">
                            <Code className="h-3 w-3 text-muted-foreground" />
                            <span className="text-[10px] font-medium text-muted-foreground">
                              Resources ({skillDetails[skill.name].resources.length})
                            </span>
                          </div>
                          <div className="space-y-0.5">
                            {skillDetails[skill.name].resources.map((r) => (
                              <div key={r} className="text-[11px] font-mono text-muted-foreground">
                                {r}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {Object.keys(skillDetails[skill.name].hooks || {}).length > 0 && (
                        <div>
                          <span className="text-[10px] font-medium text-muted-foreground">
                            Hooks: {Object.keys(skillDetails[skill.name].hooks).join(", ")}
                          </span>
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="flex items-center gap-2 py-2">
                      <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                      <span className="text-xs text-muted-foreground">加载详情...</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
      </div>
    </div>
  );
}
