"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Users,
  Shield,
  ShieldCheck,
  ShieldOff,
  Eye,
  Trash2,
  RefreshCw,
  HardDrive,
  FileText,
  ChevronDown,
  ChevronUp,
  ArrowUpDown,
  Search,
  Loader2,
  AlertCircle,
  CheckCircle,
  X,
  LogIn,
  Save,
  MessageSquare,
  UserX,
  Info,
  Inbox,
  FolderOpen,
  Sparkles,
  KeyRound,
  Clock,
  Database,
  SearchX,
  UserPlus,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu";
import { useAuthStore } from "@/stores/auth-store";
import {
  fetchAdminUsers,
  adminUpdateUser,
  adminClearWorkspace,
  adminEnforceQuota,
  adminDeleteUser,
  adminListUserSessions,
  adminDeleteUserSessions,
  adminDeleteUserSession,
  type AdminUser,
  type AdminSession,
} from "@/lib/auth-api";
import { resolveAvatarSrc } from "@/lib/api";
import { formatModelIdForDisplay } from "@/lib/model-display";
import LoginConfigTab from "@/components/admin/LoginConfigTab";
import PoolTab from "@/components/admin/PoolTab";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatTokenCount(value: number): string {
  return value.toLocaleString("zh-CN");
}

type SortField = "email" | "created_at" | "role" | "workspace_size" | "workspace_files";
type SortDir = "asc" | "desc";

function AdminAvatar({ url, name, userId }: { url?: string | null; name: string; userId?: string }) {
  const [failed, setFailed] = useState(false);
  const accessToken = useAuthStore((s) => s.accessToken);
  const proxied = resolveAvatarSrc(url, accessToken, { userId, isAdmin: true });
  if (proxied && !failed) {
    return (
      <img
        src={proxied}
        alt=""
        className="h-10 w-10 rounded-full ring-2 ring-border/50 shadow-sm object-cover"
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
      />
    );
  }
  return (
    <span
      className="h-10 w-10 rounded-full flex items-center justify-center text-sm font-semibold text-white shadow-sm"
      style={{ background: "linear-gradient(135deg, var(--em-primary), var(--em-primary-light))" }}
    >
      {name[0]?.toUpperCase() || "U"}
    </span>
  );
}

function RoleBadge({ role }: { role: string }) {
  if (role === "admin") {
    return (
      <Badge className="bg-amber-500/15 text-amber-600 border-amber-500/20 gap-1">
        <ShieldCheck className="h-3 w-3" />
        管理员
      </Badge>
    );
  }
  if (role === "readonly") {
    return (
      <Badge variant="secondary" className="gap-1">
        <Eye className="h-3 w-3" />
        只读
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="gap-1">
      <Shield className="h-3 w-3" />
      用户
    </Badge>
  );
}

function UsageBar({ used, max, label }: { used: number; max: number; label: string }) {
  const pct = max > 0 ? Math.min((used / max) * 100, 100) : 0;
  const isOver = used > max && max > 0;
  const color = isOver ? "bg-red-500" : pct > 80 ? "bg-amber-500" : "bg-[var(--em-primary)]";
  return (
    <div className="space-y-0.5">
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>{label}</span>
        <span className={isOver ? "text-red-500 font-medium" : ""}>
          {used} / {max}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function StorageBar({ usedMb, maxMb }: { usedMb: number; maxMb: number }) {
  const pct = maxMb > 0 ? Math.min((usedMb / maxMb) * 100, 100) : 0;
  const isOver = usedMb > maxMb && maxMb > 0;
  const color = isOver ? "bg-red-500" : pct > 80 ? "bg-amber-500" : "bg-[var(--em-primary)]";
  return (
    <div className="space-y-0.5">
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>存储</span>
        <span className={isOver ? "text-red-500 font-medium" : ""}>
          {usedMb.toFixed(1)} / {maxMb} MB
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

interface Toast {
  id: number;
  type: "success" | "error";
  message: string;
}

function QuotaInput({
  label,
  value,
  onChange,
  placeholder,
  suffix,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  placeholder?: string;
  suffix?: string;
}) {
  return (
    <div>
      <label className="block text-[11px] text-muted-foreground mb-0.5">{label}</label>
      <div className="flex items-center gap-1">
        <input
          type="number"
          min={0}
          value={value || ""}
          onChange={(e) => onChange(Number(e.target.value) || 0)}
          placeholder={placeholder || "0 = 默认"}
          className="w-full h-7 rounded-md border border-border bg-background px-2 text-xs focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent placeholder:text-muted-foreground/40 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
        />
        {suffix && <span className="text-[11px] text-muted-foreground flex-shrink-0">{suffix}</span>}
      </div>
    </div>
  );
}

function UserRow({
  user,
  currentUserId,
  onAction,
  allModelNames,
}: {
  user: AdminUser;
  currentUserId: string;
  onAction: (userId: string, action: string, payload?: Record<string, unknown>) => Promise<void>;
  allModelNames: string[];
}) {
  const [loading, setLoading] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const isSelf = user.id === currentUserId;
  const [editingModels, setEditingModels] = useState(false);
  const [modelDraft, setModelDraft] = useState<string[]>(user.allowed_models ?? []);

  // Quota editing state
  const [editingQuota, setEditingQuota] = useState(false);
  const [quotaDraft, setQuotaDraft] = useState({
    max_storage_mb: user.max_storage_mb ?? 0,
    max_files: user.max_files ?? 0,
    daily_token_limit: user.daily_token_limit ?? 0,
    monthly_token_limit: user.monthly_token_limit ?? 0,
  });

  // Inner tab for expanded details
  const [innerTab, setInnerTab] = useState<"usage" | "workspace" | "permissions" | "sessions">("usage");

  // Session management state
  const [sessions, setSessions] = useState<AdminSession[]>([]);
  const [sessionsLoaded, setSessionsLoaded] = useState(false);
  const [showSessions, setShowSessions] = useState(false);
  const llmUsage = user.llm_usage ?? {
    total_calls: 0,
    total_prompt_tokens: 0,
    total_completion_tokens: 0,
    total_tokens: 0,
    providers: [],
  };

  const handleAction = async (action: string, payload?: Record<string, unknown>) => {
    setLoading(action);
    try {
      await onAction(user.id, action, payload);
    } finally {
      setLoading(null);
    }
  };

  const loadSessions = async () => {
    setLoading("load_sessions");
    try {
      const data = await adminListUserSessions(user.id);
      setSessions(data.sessions);
      setSessionsLoaded(true);
      setShowSessions(true);
    } catch {
      // handled by parent
    } finally {
      setLoading(null);
    }
  };

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className={`group/card rounded-xl border transition-all duration-200 hover:shadow-lg hover:shadow-black/[0.04] dark:hover:shadow-black/20 ${
        !user.is_active ? "border-red-500/20 bg-red-500/5" : "border-border bg-card hover:border-[var(--em-primary-alpha-20)]"
      }`}
    >
      <div className="p-4 flex items-start gap-3">
        {/* Avatar */}
        <div className="flex-shrink-0">
          <AdminAvatar url={user.avatar_url} name={user.display_name || user.email} userId={user.id} />
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm truncate">
              {user.display_name || user.email.split("@")[0]}
            </span>
            <RoleBadge role={user.role} />
            {!user.is_active && (
              <Badge variant="destructive" className="gap-1">
                <ShieldOff className="h-3 w-3" />
                已禁用
              </Badge>
            )}
            {isSelf && (
              <Badge variant="secondary" className="text-[10px]">
                当前用户
              </Badge>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-0.5 truncate">{user.email}</p>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            注册于 {formatDate(user.created_at)}
          </p>
        </div>

        {/* Workspace stats compact */}
        <div className="hidden sm:flex flex-col gap-1 w-40 flex-shrink-0">
          <StorageBar usedMb={user.workspace.size_mb} maxMb={user.workspace.max_size_mb} />
          <UsageBar
            used={user.workspace.file_count}
            max={user.workspace.max_files}
            label="文件数"
          />
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1 flex-shrink-0">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 rounded-lg hover:bg-[var(--em-primary-alpha-10)] transition-all"
            onClick={() => setExpanded(!expanded)}
          >
            <ChevronDown className={`h-4 w-4 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`} />
          </Button>
        </div>
      </div>

      {/* Mobile workspace stats */}
      <div className="px-4 pb-3 sm:hidden">
        <div className="grid grid-cols-2 gap-2">
          <StorageBar usedMb={user.workspace.size_mb} maxMb={user.workspace.max_size_mb} />
          <UsageBar
            used={user.workspace.file_count}
            max={user.workspace.max_files}
            label="文件数"
          />
        </div>
      </div>

      {/* Expanded details */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="px-5 pb-4 border-t border-border/60 pt-3 bg-muted/20 dark:bg-muted/10">
              <Tabs value={innerTab} onValueChange={(v) => setInnerTab(v as typeof innerTab)}>
                <TabsList className="w-full grid grid-cols-4 h-8 mb-3">
                  <TabsTrigger value="usage" className="text-[11px] sm:text-xs gap-1 px-1 sm:px-2">
                    <HardDrive className="h-3 w-3 hidden sm:inline-block" />用量
                  </TabsTrigger>
                  <TabsTrigger value="workspace" className="text-[11px] sm:text-xs gap-1 px-1 sm:px-2">
                    <FileText className="h-3 w-3 hidden sm:inline-block" />空间
                  </TabsTrigger>
                  <TabsTrigger value="permissions" className="text-[11px] sm:text-xs gap-1 px-1 sm:px-2">
                    <Shield className="h-3 w-3 hidden sm:inline-block" />权限
                  </TabsTrigger>
                  <TabsTrigger value="sessions" className="text-[11px] sm:text-xs gap-1 px-1 sm:px-2">
                    <MessageSquare className="h-3 w-3 hidden sm:inline-block" />会话
                  </TabsTrigger>
                </TabsList>

                {/* ── Tab: 用量 ── */}
                <TabsContent value="usage" className="mt-0 space-y-3">
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-sm">
                    <div className="rounded-lg border border-border/60 bg-card/60 p-2">
                      <span className="text-muted-foreground text-[11px]">今日 Token</span>
                      <p className="font-semibold text-sm">{user.daily_tokens_used.toLocaleString()}</p>
                      {user.daily_token_limit > 0 && <p className="text-[10px] text-muted-foreground">上限 {user.daily_token_limit.toLocaleString()}</p>}
                    </div>
                    <div className="rounded-lg border border-border/60 bg-card/60 p-2">
                      <span className="text-muted-foreground text-[11px]">本月 Token</span>
                      <p className="font-semibold text-sm">{user.monthly_tokens_used.toLocaleString()}</p>
                      {user.monthly_token_limit > 0 && <p className="text-[10px] text-muted-foreground">上限 {user.monthly_token_limit.toLocaleString()}</p>}
                    </div>
                    <div className="rounded-lg border border-border/60 bg-card/60 p-2">
                      <span className="text-muted-foreground text-[11px]">总调用</span>
                      <p className="font-semibold text-sm">{formatTokenCount(llmUsage.total_calls)}</p>
                    </div>
                    <div className="rounded-lg border border-border/60 bg-card/60 p-2">
                      <span className="text-muted-foreground text-[11px]">总 Token</span>
                      <p className="font-semibold text-sm">{formatTokenCount(llmUsage.total_tokens)}</p>
                    </div>
                  </div>
                  {llmUsage.providers.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-6 px-4 rounded-lg border border-dashed border-border/60 bg-muted/30">
                      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-muted/60 mb-2.5">
                        <Database className="h-5 w-5 text-muted-foreground/60" />
                      </div>
                      <p className="text-sm font-medium text-muted-foreground">暂无模型调用记录</p>
                      <p className="text-[11px] text-muted-foreground/60 mt-1 text-center max-w-[240px]">该用户尚未使用任何 AI 模型，使用后将在此处显示详细的调用统计</p>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {llmUsage.providers.map((provider) => (
                        <div key={provider.provider} className="rounded-lg border border-border/70 bg-card/60 p-2.5">
                          <div className="flex items-center justify-between gap-2 mb-2">
                            <div className="min-w-0">
                              <p className="text-sm font-medium truncate">{provider.display_name}</p>
                              <p className="text-[11px] text-muted-foreground truncate">{provider.provider}</p>
                            </div>
                            <div className="text-right text-[11px] text-muted-foreground whitespace-nowrap">
                              <p>{formatTokenCount(provider.calls)} 次</p>
                              <p>{formatTokenCount(provider.total_tokens)} tokens</p>
                            </div>
                          </div>
                          <div className="overflow-hidden rounded-md border border-border/60">
                            <div className="max-h-44 overflow-auto">
                              <table className="w-full text-xs">
                                <thead className="sticky top-0 bg-muted/85 backdrop-blur supports-[backdrop-filter]:bg-muted/70">
                                  <tr className="text-muted-foreground">
                                    <th className="px-2 py-1.5 text-left font-medium">模型</th>
                                    <th className="px-2 py-1.5 text-right font-medium">调用</th>
                                    <th className="px-2 py-1.5 text-right font-medium hidden sm:table-cell">Prompt</th>
                                    <th className="px-2 py-1.5 text-right font-medium hidden sm:table-cell">Completion</th>
                                    <th className="px-2 py-1.5 text-right font-medium">总计</th>
                                    <th className="px-2 py-1.5 text-right font-medium hidden sm:table-cell">最近使用</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {provider.models.map((model, idx) => {
                                    const displayModel = model.display_name || formatModelIdForDisplay(model.model);
                                    return (
                                      <tr key={`${provider.provider}-${model.model}-${idx}`} className="border-t border-border/40 hover:bg-muted/40 transition-colors">
                                        <td className="px-2 py-1.5 max-w-[140px] sm:max-w-[220px] truncate" title={displayModel}>{displayModel}</td>
                                        <td className="px-2 py-1.5 text-right">{formatTokenCount(model.calls)}</td>
                                        <td className="px-2 py-1.5 text-right hidden sm:table-cell">{formatTokenCount(model.prompt_tokens)}</td>
                                        <td className="px-2 py-1.5 text-right hidden sm:table-cell">{formatTokenCount(model.completion_tokens)}</td>
                                        <td className="px-2 py-1.5 text-right font-medium">{formatTokenCount(model.total_tokens)}</td>
                                        <td className="px-2 py-1.5 text-right text-muted-foreground whitespace-nowrap hidden sm:table-cell">{model.last_used_at ? formatDate(model.last_used_at) : "—"}</td>
                                      </tr>
                                    );
                                  })}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </TabsContent>

                {/* ── Tab: 空间与配额 ── */}
                <TabsContent value="workspace" className="mt-0 space-y-3">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    {/* Workspace files */}
                    <div className="space-y-2">
                      <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">工作空间文件</h4>
                      {user.workspace.files.length === 0 ? (
                        <div className="flex flex-col items-center justify-center py-5 px-3 rounded-lg border border-dashed border-border/60 bg-muted/20">
                          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-muted/60 mb-2">
                            <FolderOpen className="h-4 w-4 text-muted-foreground/60" />
                          </div>
                          <p className="text-xs font-medium text-muted-foreground">工作空间为空</p>
                          <p className="text-[10px] text-muted-foreground/50 mt-0.5 text-center">用户上传文件后将在此处显示</p>
                        </div>
                      ) : (
                        <div className="space-y-1 max-h-40 overflow-y-auto">
                          {user.workspace.files.map((f) => (
                            <div key={f.path} className="flex items-center gap-2 text-xs py-0.5">
                              <FileText className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                              <span className="truncate flex-1">{f.name}</span>
                              <span className="text-muted-foreground flex-shrink-0">{formatBytes(f.size)}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    {/* Quota & limits */}
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">配额与限额</h4>
                        {!editingQuota ? (
                          <Button variant="ghost" size="sm" className="h-6 text-[11px] gap-1" onClick={() => { setQuotaDraft({ max_storage_mb: user.max_storage_mb ?? 0, max_files: user.max_files ?? 0, daily_token_limit: user.daily_token_limit ?? 0, monthly_token_limit: user.monthly_token_limit ?? 0 }); setEditingQuota(true); }}>编辑</Button>
                        ) : (
                          <div className="flex gap-1">
                            <Button variant="ghost" size="sm" className="h-6 text-[11px] gap-1" onClick={() => setEditingQuota(false)}>取消</Button>
                            <Button size="sm" className="h-6 text-[11px] gap-1 text-white" style={{ backgroundColor: "var(--em-primary)" }} disabled={loading !== null} onClick={async () => { await handleAction("update_quota", quotaDraft); setEditingQuota(false); }}>
                              {loading === "update_quota" && <Loader2 className="h-3 w-3 animate-spin" />}
                              <Save className="h-3 w-3" />保存
                            </Button>
                          </div>
                        )}
                      </div>
                      {!editingQuota ? (
                        <div className="grid grid-cols-2 gap-2 text-xs">
                          <div><span className="text-muted-foreground">存储上限</span><p className="font-medium">{(user.max_storage_mb ?? 0) > 0 ? `${user.max_storage_mb} MB` : "默认"}</p></div>
                          <div><span className="text-muted-foreground">文件数上限</span><p className="font-medium">{(user.max_files ?? 0) > 0 ? `${user.max_files}` : "默认"}</p></div>
                          <div><span className="text-muted-foreground">日 Token 限额</span><p className="font-medium">{user.daily_token_limit > 0 ? user.daily_token_limit.toLocaleString() : "不限"}</p></div>
                          <div><span className="text-muted-foreground">月 Token 限额</span><p className="font-medium">{user.monthly_token_limit > 0 ? user.monthly_token_limit.toLocaleString() : "不限"}</p></div>
                        </div>
                      ) : (
                        <div className="space-y-2">
                          <div className="flex items-start gap-2 rounded-md bg-blue-500/5 border border-blue-500/10 px-2.5 py-2">
                            <Info className="h-3.5 w-3.5 text-blue-500 flex-shrink-0 mt-0.5" />
                            <div className="text-[11px] text-blue-600 dark:text-blue-400 leading-relaxed">
                              <p>设为 <strong>0</strong> 表示使用全局默认值（存储/文件数）或不限制（Token）。</p>
                              <p className="mt-0.5 opacity-75">修改后点击「保存」生效，不会自动清理已有文件。</p>
                            </div>
                          </div>
                          <div className="grid grid-cols-2 gap-2">
                            <QuotaInput label="存储上限" value={quotaDraft.max_storage_mb} onChange={(v) => setQuotaDraft((d) => ({ ...d, max_storage_mb: v }))} suffix="MB" />
                            <QuotaInput label="文件数上限" value={quotaDraft.max_files} onChange={(v) => setQuotaDraft((d) => ({ ...d, max_files: v }))} suffix="个" />
                            <QuotaInput label="日 Token 限额" value={quotaDraft.daily_token_limit} onChange={(v) => setQuotaDraft((d) => ({ ...d, daily_token_limit: v }))} />
                            <QuotaInput label="月 Token 限额" value={quotaDraft.monthly_token_limit} onChange={(v) => setQuotaDraft((d) => ({ ...d, monthly_token_limit: v }))} />
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                  {/* Workspace action buttons */}
                  <div className="flex flex-wrap gap-2 pt-3 border-t border-border/40">
                    <Button variant="outline" size="sm" className="text-xs gap-1.5" disabled={loading !== null} onClick={() => handleAction("enforce_quota")}>
                      {loading === "enforce_quota" ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}强制配额
                    </Button>
                    {user.workspace.file_count > 0 && (
                      <Button variant="outline" size="sm" className="text-xs gap-1.5 text-destructive border-destructive/30 hover:bg-destructive/10" disabled={loading !== null} onClick={() => { if (window.confirm(`确定清空 ${user.display_name || user.email} 的所有工作空间文件？`)) handleAction("clear_workspace"); }}>
                        {loading === "clear_workspace" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}清空工作空间
                      </Button>
                    )}
                  </div>
                </TabsContent>

                {/* ── Tab: 权限 ── */}
                <TabsContent value="permissions" className="mt-0 space-y-4">
                  {/* Model permissions */}
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">模型权限</h4>
                      {!editingModels ? (
                        <Button variant="ghost" size="sm" className="h-6 text-[11px] gap-1" onClick={() => { setModelDraft(user.allowed_models ?? []); setEditingModels(true); }}>编辑</Button>
                      ) : (
                        <div className="flex gap-1">
                          <Button variant="ghost" size="sm" className="h-6 text-[11px] gap-1" onClick={() => setEditingModels(false)}>取消</Button>
                          <Button size="sm" className="h-6 text-[11px] gap-1 text-white" style={{ backgroundColor: "var(--em-primary)" }} disabled={loading !== null} onClick={async () => { await handleAction("set_allowed_models", { allowed_models: modelDraft.length > 0 ? modelDraft : [] }); setEditingModels(false); }}>
                            {loading === "set_allowed_models" && <Loader2 className="h-3 w-3 animate-spin" />}保存
                          </Button>
                        </div>
                      )}
                    </div>
                    {!editingModels ? (
                      <div className="text-xs">
                        {!user.allowed_models || user.allowed_models.length === 0 ? (
                          <div className="flex items-center gap-2 py-2 px-3 rounded-md bg-green-500/5 border border-green-500/10">
                            <CheckCircle className="h-3.5 w-3.5 text-green-500 flex-shrink-0" />
                            <span className="text-green-600 dark:text-green-400">不限制 — 可使用所有已配置的模型</span>
                          </div>
                        ) : (
                          <div className="space-y-1.5">
                            <p className="text-muted-foreground">允许使用的模型：</p>
                            <div className="flex flex-wrap gap-1.5">
                              <Badge variant="secondary" className="text-[10px] gap-1"><Sparkles className="h-2.5 w-2.5" />default</Badge>
                              {user.allowed_models.map((m) => (
                                <Badge key={m} variant="outline" className="text-[10px]">{m}</Badge>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="space-y-2">
                        <div className="flex items-start gap-2 rounded-md bg-amber-500/5 border border-amber-500/10 px-2.5 py-2">
                          <KeyRound className="h-3.5 w-3.5 text-amber-500 flex-shrink-0 mt-0.5" />
                          <div className="text-[11px] text-amber-600 dark:text-amber-400 leading-relaxed">
                            <p>勾选允许使用的模型，不勾选任何则表示不限制。</p>
                            <p className="mt-0.5 opacity-75"><strong>default</strong> 模型始终可用，无需单独勾选。</p>
                          </div>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {allModelNames.filter((n) => n !== "default").map((name) => (
                            <label key={name} className="inline-flex items-center gap-1.5 text-xs cursor-pointer">
                              <input type="checkbox" checked={modelDraft.includes(name)} onChange={(e) => { if (e.target.checked) setModelDraft((prev) => [...prev, name]); else setModelDraft((prev) => prev.filter((m) => m !== name)); }} className="rounded border-border" />
                              {name}
                            </label>
                          ))}
                          {allModelNames.filter((n) => n !== "default").length === 0 && (
                            <div className="flex items-center gap-2 py-2 px-3 rounded-md bg-muted/40 border border-dashed border-border/60 w-full">
                              <Sparkles className="h-3.5 w-3.5 text-muted-foreground/50" />
                              <span className="text-[11px] text-muted-foreground">系统仅配置了 default 模型，添加更多模型后可在此分配权限</span>
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                  {/* Role & account actions */}
                  {!isSelf && (
                    <div className="space-y-2 pt-3 border-t border-border/40">
                      <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">账户操作</h4>
                      <div className="flex flex-wrap gap-2">
                        {user.is_active ? (
                          <Button variant="outline" size="sm" className="text-xs gap-1.5 text-destructive border-destructive/30 hover:bg-destructive/10" disabled={loading !== null} onClick={() => handleAction("toggle_active", { is_active: false })}>
                            {loading === "toggle_active" ? <Loader2 className="h-3 w-3 animate-spin" /> : <ShieldOff className="h-3 w-3" />}禁用账户
                          </Button>
                        ) : (
                          <Button variant="outline" size="sm" className="text-xs gap-1.5 text-green-600 border-green-600/30 hover:bg-green-600/10" disabled={loading !== null} onClick={() => handleAction("toggle_active", { is_active: true })}>
                            {loading === "toggle_active" ? <Loader2 className="h-3 w-3 animate-spin" /> : <ShieldCheck className="h-3 w-3" />}启用账户
                          </Button>
                        )}
                        {user.role !== "admin" ? (
                          <Button variant="outline" size="sm" className="text-xs gap-1.5" disabled={loading !== null} onClick={() => handleAction("set_role", { role: "admin" })}>
                            {loading === "set_role" ? <Loader2 className="h-3 w-3 animate-spin" /> : <ShieldCheck className="h-3 w-3" />}设为管理员
                          </Button>
                        ) : (
                          <Button variant="outline" size="sm" className="text-xs gap-1.5" disabled={loading !== null} onClick={() => handleAction("set_role", { role: "user" })}>
                            {loading === "set_role" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Shield className="h-3 w-3" />}设为普通用户
                          </Button>
                        )}
                        <Button variant="outline" size="sm" className="text-xs gap-1.5 text-destructive border-destructive/30 hover:bg-destructive/10" disabled={loading !== null} onClick={() => { if (window.confirm(`确定彻底删除用户 ${user.display_name || user.email}？\n\n此操作将删除该用户的：\n- 所有工作空间文件\n- 所有会话记录\n- 用户账户\n\n此操作不可撤销！`)) handleAction("delete_user"); }}>
                          {loading === "delete_user" ? <Loader2 className="h-3 w-3 animate-spin" /> : <UserX className="h-3 w-3" />}删除用户
                        </Button>
                      </div>
                    </div>
                  )}
                </TabsContent>

                {/* ── Tab: 会话 ── */}
                <TabsContent value="sessions" className="mt-0 space-y-3">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">会话管理</h4>
                    <div className="flex gap-1">
                      {!showSessions ? (
                        <Button variant="ghost" size="sm" className="h-6 text-[11px] gap-1" disabled={loading !== null} onClick={loadSessions}>
                          {loading === "load_sessions" ? <Loader2 className="h-3 w-3 animate-spin" /> : <MessageSquare className="h-3 w-3" />}加载会话
                        </Button>
                      ) : (
                        <>
                          <Button variant="ghost" size="sm" className="h-6 text-[11px] gap-1" onClick={() => setShowSessions(false)}>收起</Button>
                          {sessions.length > 0 && (
                            <Button variant="ghost" size="sm" className="h-6 text-[11px] gap-1 text-destructive" disabled={loading !== null} onClick={() => { if (window.confirm(`确定删除 ${user.display_name || user.email} 的所有 ${sessions.length} 个会话？`)) handleAction("delete_all_sessions").then(() => setSessions([])); }}>
                              {loading === "delete_all_sessions" ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}全部删除
                            </Button>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                  {showSessions && (
                    sessions.length === 0 ? (
                      <div className="flex flex-col items-center justify-center py-6 px-4 rounded-lg border border-dashed border-border/60 bg-muted/20">
                        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-muted/60 mb-2">
                          <Inbox className="h-4.5 w-4.5 text-muted-foreground/60" />
                        </div>
                        <p className="text-xs font-medium text-muted-foreground">暂无会话记录</p>
                        <p className="text-[10px] text-muted-foreground/50 mt-0.5 text-center">该用户还没有创建任何对话会话</p>
                      </div>
                    ) : (
                      <div className="space-y-1 max-h-48 overflow-y-auto">
                        {sessions.map((s) => (
                          <div key={s.id} className="flex items-center gap-2 text-xs py-1 px-2 rounded-md hover:bg-muted/50">
                            <MessageSquare className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                            <span className="truncate flex-1">{s.title || `会话 ${s.id.slice(0, 8)}`}</span>
                            <span className="text-muted-foreground flex-shrink-0">{s.message_count} 条</span>
                            <Button variant="ghost" size="icon" className="h-5 w-5 text-muted-foreground hover:text-destructive flex-shrink-0" disabled={loading !== null} onClick={() => { handleAction("delete_session", { session_id: s.id }).then(() => setSessions((prev) => prev.filter((x) => x.id !== s.id))); }}>
                              {loading === "delete_session" ? <Loader2 className="h-3 w-3 animate-spin" /> : <X className="h-3 w-3" />}
                            </Button>
                          </div>
                        ))}
                      </div>
                    )
                  )}
                  {!showSessions && (
                    <div className="flex items-start gap-3 rounded-lg border border-border/60 bg-gradient-to-br from-muted/30 to-muted/10 px-4 py-3.5">
                      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--em-primary-alpha-10)] flex-shrink-0 mt-0.5">
                        <Clock className="h-4 w-4" style={{ color: 'var(--em-primary)' }} />
                      </div>
                      <div>
                        <p className="text-xs font-medium text-foreground/80">查看用户会话</p>
                        <p className="text-[11px] text-muted-foreground mt-0.5 leading-relaxed">点击上方「加载会话」按钮查看该用户的所有对话记录，可单独或批量删除会话。</p>
                      </div>
                    </div>
                  )}
                </TabsContent>
              </Tabs>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

export default function AdminPage() {
  const currentUser = useAuthStore((s) => s.user);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [sortField, setSortField] = useState<SortField>("created_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [allModelNames, setAllModelNames] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState<"users" | "login" | "pool">("users");
  let toastIdRef = 0;

  const addToast = useCallback((type: "success" | "error", message: string) => {
    const id = ++toastIdRef;
    setToasts((prev) => [...prev, { id, type, message }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 3000);
  }, []);

  const loadUsers = useCallback(async (opts?: { silent?: boolean }) => {
    const silent = opts?.silent === true;
    try {
      if (!silent) {
        setLoading(true);
      }
      const data = await fetchAdminUsers();
      setUsers(data.users);
      setError("");
    } catch (err) {
      if (!silent) {
        setError(err instanceof Error ? err.message : "加载失败");
      }
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    if (currentUser?.role !== "admin") return;
    loadUsers();
    // 获取权限编辑器可用的模型名
    import("@/lib/api").then(({ apiGet }) => {
      apiGet<{ models: { name: string }[] }>("/models")
        .then((data) => setAllModelNames(data.models.map((m) => m.name)))
        .catch(() => {});
    });
  }, [currentUser, loadUsers]);

  const handleAction = useCallback(
    async (userId: string, action: string, payload?: Record<string, unknown>) => {
      const userSnapshot = users.find((u) => u.id === userId);
      const rollbackUser = (snapshot: AdminUser | undefined) => {
        if (!snapshot) return;
        setUsers((prev) => prev.map((u) => (u.id === userId ? snapshot : u)));
      };

      try {
        switch (action) {
          case "toggle_active": {
            const isActive = payload?.is_active === true;
            setUsers((prev) =>
              prev.map((u) =>
                u.id === userId
                  ? {
                      ...u,
                      is_active: isActive,
                    }
                  : u
              )
            );
            await adminUpdateUser(userId, { is_active: payload?.is_active });
            addToast("success", payload?.is_active ? "账户已启用" : "账户已禁用");
            break;
          }
          case "set_role": {
            const role = typeof payload?.role === "string" ? payload.role : "";
            if (role) {
              setUsers((prev) =>
                prev.map((u) =>
                  u.id === userId
                    ? {
                        ...u,
                        role,
                      }
                    : u
                )
              );
            }
            await adminUpdateUser(userId, { role: payload?.role });
            addToast("success", `角色已更新为 ${payload?.role}`);
            break;
          }
          case "enforce_quota": {
            const result = await adminEnforceQuota(userId);
            addToast(
              "success",
              result.deleted.length > 0
                ? `已删除 ${result.deleted.length} 个超额文件`
                : "工作空间在配额内，无需清理",
            );
            break;
          }
          case "clear_workspace": {
            const result = await adminClearWorkspace(userId);
            addToast("success", `已清空 ${result.deleted_files} 个文件`);
            break;
          }
          case "set_allowed_models":
            await adminUpdateUser(userId, { allowed_models: payload?.allowed_models ?? [] });
            addToast("success", "模型权限已更新");
            break;
          case "update_quota": {
            const maxStorageMb =
              typeof payload?.max_storage_mb === "number"
                ? payload.max_storage_mb
                : userSnapshot?.max_storage_mb ?? 0;
            const maxFiles =
              typeof payload?.max_files === "number"
                ? payload.max_files
                : userSnapshot?.max_files ?? 0;
            const dailyTokenLimit =
              typeof payload?.daily_token_limit === "number"
                ? payload.daily_token_limit
                : userSnapshot?.daily_token_limit ?? 0;
            const monthlyTokenLimit =
              typeof payload?.monthly_token_limit === "number"
                ? payload.monthly_token_limit
                : userSnapshot?.monthly_token_limit ?? 0;
            setUsers((prev) =>
              prev.map((u) =>
                u.id === userId
                  ? {
                      ...u,
                      max_storage_mb: maxStorageMb,
                      max_files: maxFiles,
                      daily_token_limit: dailyTokenLimit,
                      monthly_token_limit: monthlyTokenLimit,
                      workspace: {
                        ...u.workspace,
                        max_size_mb: maxStorageMb,
                        max_files: maxFiles,
                      },
                    }
                  : u
              )
            );
            await adminUpdateUser(userId, payload ?? {});
            addToast("success", "配额已更新");
            break;
          }
          case "delete_user": {
            const delResult = await adminDeleteUser(userId);
            addToast(
              "success",
              `用户已删除（${delResult.deleted_files} 文件, ${delResult.deleted_sessions} 会话）`,
            );
            break;
          }
          case "delete_all_sessions": {
            const sessResult = await adminDeleteUserSessions(userId);
            addToast("success", `已删除 ${sessResult.deleted_sessions} 个会话`);
            break;
          }
          case "delete_session": {
            if (payload?.session_id) {
              await adminDeleteUserSession(userId, payload.session_id as string);
              addToast("success", "会话已删除");
            }
            break;
          }
        }
      } catch (err) {
        if (action === "toggle_active" || action === "set_role" || action === "update_quota") {
          rollbackUser(userSnapshot);
        }
        addToast("error", err instanceof Error ? err.message : "操作失败");
      } finally {
        // 后台重拉进行最终校准，避免每次操作都阻塞在整表刷新。
        void loadUsers({ silent: true });
      }
    },
    [users, loadUsers, addToast],
  );

  const filtered = users.filter((u) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      u.email.toLowerCase().includes(q) ||
      u.display_name.toLowerCase().includes(q) ||
      u.role.toLowerCase().includes(q)
    );
  });

  const sorted = [...filtered].sort((a, b) => {
    let cmp = 0;
    switch (sortField) {
      case "email":
        cmp = a.email.localeCompare(b.email);
        break;
      case "created_at":
        cmp = a.created_at.localeCompare(b.created_at);
        break;
      case "role":
        cmp = a.role.localeCompare(b.role);
        break;
      case "workspace_size":
        cmp = a.workspace.total_bytes - b.workspace.total_bytes;
        break;
      case "workspace_files":
        cmp = a.workspace.file_count - b.workspace.file_count;
        break;
    }
    return sortDir === "asc" ? cmp : -cmp;
  });

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  };

  const totalStorage = users.reduce((acc, u) => acc + u.workspace.total_bytes, 0);
  const totalFiles = users.reduce((acc, u) => acc + u.workspace.file_count, 0);
  const activeCount = users.filter((u) => u.is_active).length;

  if (currentUser?.role !== "admin") return null;

  return (
    <div className="h-full">
      <div className="px-5 py-5 space-y-4">
        {/* Tab navigation + refresh */}
        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as "users" | "login" | "pool")}>
          <div className="flex items-center gap-2">
          <TabsList className="flex-1 sm:flex-none w-full sm:w-auto">
            <TabsTrigger value="users" className="gap-1.5 text-xs">
              <Users className="h-3.5 w-3.5" />
              用户管理
            </TabsTrigger>
            <TabsTrigger value="login" className="gap-1.5 text-xs">
              <LogIn className="h-3.5 w-3.5" />
              登录设置
            </TabsTrigger>
            <TabsTrigger value="pool" className="gap-1.5 text-xs">
              <Database className="h-3.5 w-3.5" />
              号池
            </TabsTrigger>
          </TabsList>
          {activeTab === "users" && (
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5 text-xs h-8"
              onClick={() => {
                void loadUsers();
              }}
              disabled={loading}
            >
              {loading ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <RefreshCw className="h-3 w-3" />
              )}
              刷新
            </Button>
          )}
          </div>

          <TabsContent value="login">
            <LoginConfigTab />
          </TabsContent>

          <TabsContent value="pool">
            <PoolTab onToast={(msg, type) => addToast(type, msg)} />
          </TabsContent>

          <TabsContent value="users" className="space-y-4 sm:space-y-6">
        {/* Stats cards */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-4">
          <div className="relative overflow-hidden rounded-xl border border-border bg-card px-2.5 py-2 sm:px-4 sm:py-3 transition-all duration-200 hover:shadow-md hover:border-[var(--em-primary-alpha-20)] group/stat">
            <div className="hidden sm:block absolute -top-6 -right-6 h-20 w-20 rounded-full bg-[var(--em-primary-alpha-06)] group-hover/stat:scale-125 transition-transform duration-500" />
            <div className="relative">
              <div className="hidden sm:flex h-7 w-7 items-center justify-center rounded-md mb-2" style={{ backgroundColor: 'var(--em-primary-alpha-10)' }}>
                <Users className="h-4 w-4" style={{ color: 'var(--em-primary)' }} />
              </div>
              <p className="text-lg sm:text-2xl font-bold tracking-tight">{users.length}</p>
              <p className="text-xs text-muted-foreground mt-0.5">总用户数</p>
              <p className="text-[11px] text-muted-foreground mt-1">
                <span className="text-green-600 dark:text-green-400 font-medium">{activeCount}</span> 活跃
                {" · "}
                {users.length - activeCount} 禁用
              </p>
            </div>
          </div>
          <div className="relative overflow-hidden rounded-xl border border-border bg-card px-2.5 py-2 sm:px-4 sm:py-3 transition-all duration-200 hover:shadow-md hover:border-blue-500/20 group/stat">
            <div className="hidden sm:block absolute -top-6 -right-6 h-20 w-20 rounded-full bg-blue-500/[0.06] group-hover/stat:scale-125 transition-transform duration-500" />
            <div className="relative">
              <div className="hidden sm:flex h-7 w-7 items-center justify-center rounded-md mb-2 bg-blue-500/10">
                <HardDrive className="h-4 w-4 text-blue-500" />
              </div>
              <p className="text-lg sm:text-2xl font-bold tracking-tight">{formatBytes(totalStorage)}</p>
              <p className="text-xs text-muted-foreground mt-0.5">总存储</p>
              <p className="text-[11px] text-muted-foreground mt-1">平均 <span className="text-blue-600 dark:text-blue-400 font-medium">{users.length > 0 ? formatBytes(Math.round(totalStorage / users.length)) : '0 B'}</span> / 用户</p>
            </div>
          </div>
          <div className="relative overflow-hidden rounded-xl border border-border bg-card px-2.5 py-2 sm:px-4 sm:py-3 transition-all duration-200 hover:shadow-md hover:border-amber-500/20 group/stat">
            <div className="hidden sm:block absolute -top-6 -right-6 h-20 w-20 rounded-full bg-amber-500/[0.06] group-hover/stat:scale-125 transition-transform duration-500" />
            <div className="relative">
              <div className="hidden sm:flex h-7 w-7 items-center justify-center rounded-md mb-2 bg-amber-500/10">
                <FileText className="h-4 w-4 text-amber-500" />
              </div>
              <p className="text-lg sm:text-2xl font-bold tracking-tight">{totalFiles}</p>
              <p className="text-xs text-muted-foreground mt-0.5">总文件数</p>
              <p className="text-[11px] text-muted-foreground mt-1">平均 <span className="text-amber-600 dark:text-amber-400 font-medium">{users.length > 0 ? (totalFiles / users.length).toFixed(1) : '0'}</span> 个 / 用户</p>
            </div>
          </div>
          <div className="relative overflow-hidden rounded-xl border border-border bg-card px-2.5 py-2 sm:px-4 sm:py-3 transition-all duration-200 hover:shadow-md hover:border-violet-500/20 group/stat">
            <div className="hidden sm:block absolute -top-6 -right-6 h-20 w-20 rounded-full bg-violet-500/[0.06] group-hover/stat:scale-125 transition-transform duration-500" />
            <div className="relative">
              <div className="hidden sm:flex h-7 w-7 items-center justify-center rounded-md mb-2 bg-violet-500/10">
                <ShieldCheck className="h-4 w-4 text-violet-500" />
              </div>
              <p className="text-lg sm:text-2xl font-bold tracking-tight">
                {users.filter((u) => u.role === "admin").length}
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">管理员</p>
              <p className="text-[11px] text-muted-foreground mt-1">
                <span className="text-violet-600 dark:text-violet-400 font-medium">{users.filter((u) => u.role === "readonly").length}</span> 只读
                {" \u00b7 "}
                {users.filter((u) => u.role === "user").length} 普通
              </p>
            </div>
          </div>
        </div>

        {/* Search & sort */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索用户邮箱或名称..."
            className="w-full h-9 rounded-xl border border-border bg-card pl-9 pr-24 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent placeholder:text-muted-foreground/40 transition-all hover:border-[var(--em-primary-alpha-20)]"
          />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="absolute right-1 top-1/2 -translate-y-1/2 h-7 text-xs gap-1 text-muted-foreground hover:text-foreground"
              >
                <ArrowUpDown className="h-3 w-3" />
                {{ created_at: "注册时间", email: "邮箱", role: "角色", workspace_size: "存储", workspace_files: "文件" }[sortField]}
                {sortDir === "asc" ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-[120px]">
              {(
                [
                  ["created_at", "注册时间"],
                  ["email", "邮箱"],
                  ["role", "角色"],
                  ["workspace_size", "存储"],
                  ["workspace_files", "文件"],
                ] as [SortField, string][]
              ).map(([field, label]) => (
                <DropdownMenuItem
                  key={field}
                  className="text-xs gap-2"
                  onClick={() => toggleSort(field)}
                >
                  <span className="flex-1">{label}</span>
                  {sortField === field &&
                    (sortDir === "asc" ? (
                      <ChevronUp className="h-3 w-3 text-[var(--em-primary)]" />
                    ) : (
                      <ChevronDown className="h-3 w-3 text-[var(--em-primary)]" />
                    ))}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        {/* Error */}
        {error && (
          <div className="rounded-xl bg-red-500/5 border border-red-500/15 px-4 py-3.5 flex items-start gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-red-500/10 flex-shrink-0 mt-0.5">
              <AlertCircle className="h-4 w-4 text-red-500" />
            </div>
            <div>
              <p className="text-sm font-medium text-red-600 dark:text-red-400">加载失败</p>
              <p className="text-xs text-red-500/80 mt-0.5">{error}</p>
              <Button variant="ghost" size="sm" className="h-6 text-[11px] gap-1 text-red-600 dark:text-red-400 hover:bg-red-500/10 mt-1.5 -ml-2" onClick={() => { setError(''); loadUsers(); }}>
                <RefreshCw className="h-3 w-3" />重新加载
              </Button>
            </div>
          </div>
        )}

        {/* User list */}
        {loading && users.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 gap-3">
            <div className="relative">
              <div className="h-12 w-12 rounded-full border-2 border-muted animate-pulse" />
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2" />
            </div>
            <p className="text-sm text-muted-foreground">正在加载用户数据...</p>
          </div>
        ) : sorted.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 px-4">
            {search ? (
              <>
                <div className="flex h-14 w-14 items-center justify-center rounded-full bg-muted/60 mb-3">
                  <SearchX className="h-7 w-7 text-muted-foreground/50" />
                </div>
                <p className="text-sm font-medium text-muted-foreground">未找到匹配的用户</p>
                <p className="text-xs text-muted-foreground/60 mt-1 text-center max-w-[280px]">尝试使用不同的关键词搜索，或清空搜索框查看所有用户</p>
                <Button variant="ghost" size="sm" className="mt-3 text-xs gap-1.5 text-muted-foreground hover:text-foreground" onClick={() => setSearch('')}>
                  <X className="h-3 w-3" />清空搜索
                </Button>
              </>
            ) : (
              <>
                <div className="flex h-14 w-14 items-center justify-center rounded-full bg-[var(--em-primary-alpha-06)] mb-3">
                  <UserPlus className="h-7 w-7" style={{ color: 'var(--em-primary)', opacity: 0.5 }} />
                </div>
                <p className="text-sm font-medium text-muted-foreground">暂无用户</p>
                <p className="text-xs text-muted-foreground/60 mt-1 text-center max-w-[280px]">还没有用户注册，用户通过登录页面注册后将自动显示在此列表中</p>
              </>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {sorted.map((user) => (
              <UserRow
                key={user.id}
                user={user}
                currentUserId={currentUser?.id || ""}
                onAction={handleAction}
                allModelNames={allModelNames}
              />
            ))}
          </div>
        )}
          </TabsContent>
        </Tabs>
      </div>

      {/* Toast notifications */}
      <div className="fixed bottom-[max(1rem,env(safe-area-inset-bottom))] right-4 left-4 sm:left-auto z-50 flex flex-col gap-2">
        <AnimatePresence>
          {toasts.map((t) => (
            <motion.div
              key={t.id}
              initial={{ opacity: 0, y: 20, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -10, scale: 0.95 }}
              className={`flex items-center gap-2 px-4 py-2.5 rounded-lg shadow-lg border text-sm ${
                t.type === "success"
                  ? "bg-green-50 dark:bg-green-950/50 border-green-200 dark:border-green-800 text-green-800 dark:text-green-200"
                  : "bg-red-50 dark:bg-red-950/50 border-red-200 dark:border-red-800 text-red-800 dark:text-red-200"
              }`}
            >
              {t.type === "success" ? (
                <CheckCircle className="h-4 w-4 flex-shrink-0" />
              ) : (
                <AlertCircle className="h-4 w-4 flex-shrink-0" />
              )}
              {t.message}
              <button
                onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
                className="ml-2 opacity-60 hover:opacity-100"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
