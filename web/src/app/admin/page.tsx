"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
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
  Search,
  Loader2,
  AlertCircle,
  CheckCircle,
  X,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAuthStore } from "@/stores/auth-store";
import {
  fetchAdminUsers,
  adminUpdateUser,
  adminClearWorkspace,
  adminEnforceQuota,
  type AdminUser,
} from "@/lib/auth-api";

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

type SortField = "email" | "created_at" | "role" | "workspace_size" | "workspace_files";
type SortDir = "asc" | "desc";

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

  const handleAction = async (action: string, payload?: Record<string, unknown>) => {
    setLoading(action);
    try {
      await onAction(user.id, action, payload);
    } finally {
      setLoading(null);
    }
  };

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className={`rounded-xl border transition-colors ${
        !user.is_active ? "border-red-500/20 bg-red-500/5" : "border-border bg-card"
      }`}
    >
      <div className="p-4 flex items-start gap-3">
        {/* Avatar */}
        <div className="flex-shrink-0">
          {user.avatar_url ? (
            <img src={user.avatar_url} alt="" className="h-10 w-10 rounded-full" />
          ) : (
            <span
              className="h-10 w-10 rounded-full flex items-center justify-center text-sm font-medium text-white"
              style={{ backgroundColor: "var(--em-primary)" }}
            >
              {(user.display_name || user.email)[0]?.toUpperCase() || "U"}
            </span>
          )}
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
            className="h-7 w-7"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
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
            <div className="px-4 pb-4 border-t border-border pt-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {/* Token usage */}
                <div className="space-y-2">
                  <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    Token 用量
                  </h4>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <div>
                      <span className="text-muted-foreground text-xs">今日</span>
                      <p className="font-medium">{user.daily_tokens_used.toLocaleString()}</p>
                      {user.daily_token_limit > 0 && (
                        <p className="text-[11px] text-muted-foreground">
                          上限 {user.daily_token_limit.toLocaleString()}
                        </p>
                      )}
                    </div>
                    <div>
                      <span className="text-muted-foreground text-xs">本月</span>
                      <p className="font-medium">{user.monthly_tokens_used.toLocaleString()}</p>
                      {user.monthly_token_limit > 0 && (
                        <p className="text-[11px] text-muted-foreground">
                          上限 {user.monthly_token_limit.toLocaleString()}
                        </p>
                      )}
                    </div>
                  </div>
                </div>

                {/* Workspace files */}
                <div className="space-y-2">
                  <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    工作空间文件
                  </h4>
                  {user.workspace.files.length === 0 ? (
                    <p className="text-xs text-muted-foreground">暂无文件</p>
                  ) : (
                    <div className="space-y-1 max-h-32 overflow-y-auto">
                      {user.workspace.files.map((f) => (
                        <div
                          key={f.path}
                          className="flex items-center gap-2 text-xs py-0.5"
                        >
                          <FileText className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                          <span className="truncate flex-1">{f.name}</span>
                          <span className="text-muted-foreground flex-shrink-0">
                            {formatBytes(f.size)}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* Model permissions */}
              <div className="mt-4 pt-3 border-t border-border">
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    模型权限
                  </h4>
                  {!editingModels ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 text-[11px] gap-1"
                      onClick={() => {
                        setModelDraft(user.allowed_models ?? []);
                        setEditingModels(true);
                      }}
                    >
                      编辑
                    </Button>
                  ) : (
                    <div className="flex gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 text-[11px] gap-1"
                        onClick={() => setEditingModels(false)}
                      >
                        取消
                      </Button>
                      <Button
                        size="sm"
                        className="h-6 text-[11px] gap-1 text-white"
                        style={{ backgroundColor: "var(--em-primary)" }}
                        disabled={loading !== null}
                        onClick={async () => {
                          await handleAction("set_allowed_models", {
                            allowed_models: modelDraft.length > 0 ? modelDraft : [],
                          });
                          setEditingModels(false);
                        }}
                      >
                        {loading === "set_allowed_models" && (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        )}
                        保存
                      </Button>
                    </div>
                  )}
                </div>
                {!editingModels ? (
                  <p className="text-xs text-muted-foreground">
                    {!user.allowed_models || user.allowed_models.length === 0
                      ? "不限制（可使用所有模型）"
                      : `允许: default, ${user.allowed_models.join(", ")}`}
                  </p>
                ) : (
                  <div className="space-y-2">
                    <p className="text-[11px] text-muted-foreground">
                      勾选允许使用的模型（不勾选任何 = 不限制）。default 模型始终可用。
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {allModelNames
                        .filter((n) => n !== "default")
                        .map((name) => (
                          <label
                            key={name}
                            className="inline-flex items-center gap-1.5 text-xs cursor-pointer"
                          >
                            <input
                              type="checkbox"
                              checked={modelDraft.includes(name)}
                              onChange={(e) => {
                                if (e.target.checked) {
                                  setModelDraft((prev) => [...prev, name]);
                                } else {
                                  setModelDraft((prev) => prev.filter((m) => m !== name));
                                }
                              }}
                              className="rounded border-border"
                            />
                            {name}
                          </label>
                        ))}
                      {allModelNames.filter((n) => n !== "default").length === 0 && (
                        <span className="text-[11px] text-muted-foreground">暂无多模型配置</span>
                      )}
                    </div>
                  </div>
                )}
              </div>

              {/* Action buttons */}
              <div className="flex flex-wrap gap-2 mt-4 pt-3 border-t border-border">
                {!isSelf && (
                  <>
                    {user.is_active ? (
                      <Button
                        variant="outline"
                        size="sm"
                        className="text-xs gap-1.5 text-destructive border-destructive/30 hover:bg-destructive/10"
                        disabled={loading !== null}
                        onClick={() => handleAction("toggle_active", { is_active: false })}
                      >
                        {loading === "toggle_active" ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <ShieldOff className="h-3 w-3" />
                        )}
                        禁用账户
                      </Button>
                    ) : (
                      <Button
                        variant="outline"
                        size="sm"
                        className="text-xs gap-1.5 text-green-600 border-green-600/30 hover:bg-green-600/10"
                        disabled={loading !== null}
                        onClick={() => handleAction("toggle_active", { is_active: true })}
                      >
                        {loading === "toggle_active" ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <ShieldCheck className="h-3 w-3" />
                        )}
                        启用账户
                      </Button>
                    )}

                    {user.role !== "admin" ? (
                      <Button
                        variant="outline"
                        size="sm"
                        className="text-xs gap-1.5"
                        disabled={loading !== null}
                        onClick={() => handleAction("set_role", { role: "admin" })}
                      >
                        {loading === "set_role" ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <ShieldCheck className="h-3 w-3" />
                        )}
                        设为管理员
                      </Button>
                    ) : (
                      <Button
                        variant="outline"
                        size="sm"
                        className="text-xs gap-1.5"
                        disabled={loading !== null}
                        onClick={() => handleAction("set_role", { role: "user" })}
                      >
                        {loading === "set_role" ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Shield className="h-3 w-3" />
                        )}
                        设为普通用户
                      </Button>
                    )}
                  </>
                )}

                <Button
                  variant="outline"
                  size="sm"
                  className="text-xs gap-1.5"
                  disabled={loading !== null}
                  onClick={() => handleAction("enforce_quota")}
                >
                  {loading === "enforce_quota" ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <RefreshCw className="h-3 w-3" />
                  )}
                  强制配额
                </Button>

                {user.workspace.file_count > 0 && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-xs gap-1.5 text-destructive border-destructive/30 hover:bg-destructive/10"
                    disabled={loading !== null}
                    onClick={() => {
                      if (window.confirm(`确定清空 ${user.display_name || user.email} 的所有工作空间文件？`)) {
                        handleAction("clear_workspace");
                      }
                    }}
                  >
                    {loading === "clear_workspace" ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Trash2 className="h-3 w-3" />
                    )}
                    清空工作空间
                  </Button>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

export default function AdminPage() {
  const router = useRouter();
  const currentUser = useAuthStore((s) => s.user);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [sortField, setSortField] = useState<SortField>("created_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [allModelNames, setAllModelNames] = useState<string[]>([]);
  let toastIdRef = 0;

  const addToast = useCallback((type: "success" | "error", message: string) => {
    const id = ++toastIdRef;
    setToasts((prev) => [...prev, { id, type, message }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 3000);
  }, []);

  const loadUsers = useCallback(async () => {
    try {
      setLoading(true);
      const data = await fetchAdminUsers();
      setUsers(data.users);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (currentUser?.role !== "admin") {
      router.replace("/");
      return;
    }
    loadUsers();
    // Fetch available model names for the permissions editor
    import("@/lib/api").then(({ apiGet }) => {
      apiGet<{ models: { name: string }[] }>("/models")
        .then((data) => setAllModelNames(data.models.map((m) => m.name)))
        .catch(() => {});
    });
  }, [currentUser, router, loadUsers]);

  const handleAction = useCallback(
    async (userId: string, action: string, payload?: Record<string, unknown>) => {
      try {
        switch (action) {
          case "toggle_active":
            await adminUpdateUser(userId, { is_active: payload?.is_active });
            addToast("success", payload?.is_active ? "账户已启用" : "账户已禁用");
            break;
          case "set_role":
            await adminUpdateUser(userId, { role: payload?.role });
            addToast("success", `角色已更新为 ${payload?.role}`);
            break;
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
        }
        await loadUsers();
      } catch (err) {
        addToast("error", err instanceof Error ? err.message : "操作失败");
      }
    },
    [loadUsers, addToast],
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
    <div className="min-h-screen bg-gradient-to-b from-background to-muted/20">
      {/* Header */}
      <div className="sticky top-0 z-30 backdrop-blur-xl bg-background/80 border-b border-border">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-3 flex items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => router.push("/")}
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className="flex items-center gap-2">
            <Users className="h-5 w-5" style={{ color: "var(--em-primary)" }} />
            <h1 className="text-lg font-semibold">用户管理</h1>
          </div>
          <div className="flex-1" />
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5 text-xs"
            onClick={loadUsers}
            disabled={loading}
          >
            {loading ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <RefreshCw className="h-3 w-3" />
            )}
            刷新
          </Button>
        </div>
      </div>

      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-6 space-y-6">
        {/* Stats cards */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="rounded-xl border border-border bg-card p-3">
            <div className="flex items-center gap-2 text-muted-foreground text-xs mb-1">
              <Users className="h-3.5 w-3.5" />
              总用户数
            </div>
            <p className="text-2xl font-bold">{users.length}</p>
            <p className="text-[11px] text-muted-foreground">
              {activeCount} 活跃 / {users.length - activeCount} 禁用
            </p>
          </div>
          <div className="rounded-xl border border-border bg-card p-3">
            <div className="flex items-center gap-2 text-muted-foreground text-xs mb-1">
              <HardDrive className="h-3.5 w-3.5" />
              总存储
            </div>
            <p className="text-2xl font-bold">{formatBytes(totalStorage)}</p>
          </div>
          <div className="rounded-xl border border-border bg-card p-3">
            <div className="flex items-center gap-2 text-muted-foreground text-xs mb-1">
              <FileText className="h-3.5 w-3.5" />
              总文件数
            </div>
            <p className="text-2xl font-bold">{totalFiles}</p>
          </div>
          <div className="rounded-xl border border-border bg-card p-3">
            <div className="flex items-center gap-2 text-muted-foreground text-xs mb-1">
              <ShieldCheck className="h-3.5 w-3.5" />
              管理员
            </div>
            <p className="text-2xl font-bold">
              {users.filter((u) => u.role === "admin").length}
            </p>
          </div>
        </div>

        {/* Search & sort */}
        <div className="flex flex-col sm:flex-row gap-3">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索用户邮箱或名称..."
              className="w-full h-9 rounded-lg border border-border bg-background pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent placeholder:text-muted-foreground/50"
            />
          </div>
          <div className="flex gap-1.5 flex-wrap">
            {(
              [
                ["created_at", "注册时间"],
                ["email", "邮箱"],
                ["role", "角色"],
                ["workspace_size", "存储"],
                ["workspace_files", "文件"],
              ] as [SortField, string][]
            ).map(([field, label]) => (
              <Button
                key={field}
                variant={sortField === field ? "default" : "outline"}
                size="sm"
                className="text-xs gap-1 h-8"
                style={sortField === field ? { backgroundColor: "var(--em-primary)" } : undefined}
                onClick={() => toggleSort(field)}
              >
                {label}
                {sortField === field &&
                  (sortDir === "asc" ? (
                    <ChevronUp className="h-3 w-3" />
                  ) : (
                    <ChevronDown className="h-3 w-3" />
                  ))}
              </Button>
            ))}
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="rounded-lg bg-destructive/10 border border-destructive/20 px-3 py-2.5 text-sm text-destructive flex items-center gap-2">
            <AlertCircle className="h-4 w-4 flex-shrink-0" />
            {error}
          </div>
        )}

        {/* User list */}
        {loading && users.length === 0 ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : sorted.length === 0 ? (
          <div className="text-center py-20 text-muted-foreground">
            {search ? "未找到匹配的用户" : "暂无用户"}
          </div>
        ) : (
          <div className="space-y-2">
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
      </div>

      {/* Toast notifications */}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
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
