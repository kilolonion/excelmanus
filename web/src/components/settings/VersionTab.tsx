"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Loader2,
  RefreshCw,
  Trash2,
  HardDrive,
  FolderArchive,
  MapPin,
  CheckCircle2,
  AlertCircle,
  ArrowUpCircle,
  Sparkles,
  RotateCcw,
  Download,
  Eraser,
  DatabaseBackup,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  apiGet,
  apiPost,
  fetchShortcutInfo,
  createDesktopShortcut,
  removeDesktopShortcut,
  cleanupVersionBackups,
  applyVersionUpdate,
  restoreVersionBackup,
  migrateVersionData,
} from "@/lib/api";
import type { ShortcutInfo, UpdateApplyResult } from "@/lib/api";

interface VersionInfo {
  current: string;
  latest: string;
  has_update: boolean;
  commits_behind: number;
  release_notes: string;
  check_method: string;
  error?: string;
}

interface BackupEntry {
  name: string;
  path: string;
  version: string;
  timestamp: string;
  size_mb: number;
}

interface InstallationEntry {
  path: string;
  version: string;
  installed_at?: string;
  last_seen?: string;
  platform?: string;
}

function formatTimestamp(ts: string): string {
  if (!ts) return "未知";
  // backup timestamp: 20260301_162242
  const match = ts.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/);
  if (match) {
    const [, y, mo, d, h, mi, s] = match;
    return `${y}-${mo}-${d} ${h}:${mi}:${s}`;
  }
  // ISO format
  try {
    return new Date(ts).toLocaleString("zh-CN");
  } catch {
    return ts;
  }
}

export function VersionTab() {
  const [version, setVersion] = useState<VersionInfo | null>(null);
  const [backups, setBackups] = useState<BackupEntry[]>([]);
  const [installations, setInstallations] = useState<InstallationEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [deletingBackup, setDeletingBackup] = useState<string | null>(null);
  const [deletingInstall, setDeletingInstall] = useState<string | null>(null);
  const [shortcut, setShortcut] = useState<ShortcutInfo | null>(null);
  const [shortcutBusy, setShortcutBusy] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [cleaningUp, setCleaningUp] = useState(false);
  const [restoringBackup, setRestoringBackup] = useState<string | null>(null);
  const [migrating, setMigrating] = useState(false);
  const [actionMsg, setActionMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  const showMsg = (type: "ok" | "err", text: string) => {
    setActionMsg({ type, text });
    setTimeout(() => setActionMsg(null), 3000);
  };

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [v, b, i, sc] = await Promise.all([
        apiGet<VersionInfo>("/version/check"),
        apiGet<{ backups: BackupEntry[] }>("/version/backups"),
        apiGet<{ installations: InstallationEntry[] }>("/version/installations"),
        fetchShortcutInfo().catch(() => null),
      ]);
      setVersion(v);
      setBackups(b.backups ?? []);
      setInstallations(i.installations ?? []);
      if (sc) setShortcut(sc);
    } catch {
      // 忽略
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const handleToggleShortcut = async () => {
    setShortcutBusy(true);
    try {
      if (shortcut?.exists) {
        await removeDesktopShortcut();
        setShortcut((prev) => prev ? { ...prev, exists: false, shortcut_path: null } : prev);
        showMsg("ok", "已删除桌面快捷方式");
      } else {
        const res = await createDesktopShortcut();
        setShortcut((prev) => prev ? { ...prev, exists: true, shortcut_path: res.path } : prev);
        showMsg("ok", "桌面快捷方式已创建");
      }
    } catch {
      showMsg("err", shortcut?.exists ? "删除快捷方式失败" : "创建快捷方式失败");
    } finally {
      setShortcutBusy(false);
    }
  };

  const handleCheckUpdate = async () => {
    setChecking(true);
    try {
      const v = await apiGet<VersionInfo>("/version/check");
      setVersion(v);
      if (v.has_update) {
        showMsg("ok", `发现新版本 ${v.latest}（落后 ${v.commits_behind} 个提交）`);
      } else {
        showMsg("ok", "已是最新版本");
      }
    } catch {
      showMsg("err", "检查更新失败");
    } finally {
      setChecking(false);
    }
  };

  const handleDeleteBackup = async (name: string) => {
    setDeletingBackup(name);
    try {
      await apiPost("/version/backups/delete", { backup_name: name });
      setBackups((prev) => prev.filter((b) => b.name !== name));
      showMsg("ok", `已删除备份 ${name}`);
    } catch {
      showMsg("err", "删除备份失败");
    } finally {
      setDeletingBackup(null);
    }
  };

  const handleDeleteInstallation = async (path: string) => {
    setDeletingInstall(path);
    try {
      await apiPost("/version/installations/delete", { path });
      setInstallations((prev) => prev.filter((i) => i.path !== path));
      showMsg("ok", "已移除安装记录");
    } catch {
      showMsg("err", "移除安装记录失败");
    } finally {
      setDeletingInstall(null);
    }
  };

  const handleApplyUpdate = async () => {
    if (!confirm("确定要执行更新？更新前会自动备份数据。")) return;
    setUpdating(true);
    try {
      const result: UpdateApplyResult = await applyVersionUpdate({ useMirror: false });
      if (result.success) {
        showMsg("ok", `更新成功: ${result.old_version} → ${result.new_version}${result.needs_restart ? "，请重启服务" : ""}`);
        await fetchAll();
      } else {
        showMsg("err", `更新失败: ${result.error || "未知错误"}`);
      }
    } catch {
      showMsg("err", "执行更新失败");
    } finally {
      setUpdating(false);
    }
  };

  const handleCleanupBackups = async () => {
    if (!confirm("清理旧备份，仅保留最近 2 个，确定继续？")) return;
    setCleaningUp(true);
    try {
      const res = await cleanupVersionBackups(2);
      showMsg("ok", `已清理 ${res.removed_count} 个旧备份`);
      await fetchAll();
    } catch {
      showMsg("err", "清理备份失败");
    } finally {
      setCleaningUp(false);
    }
  };

  const handleRestoreBackup = async (name: string) => {
    if (!confirm(`确定从备份 ${name} 恢复数据？恢复后需要重启服务。`)) return;
    setRestoringBackup(name);
    try {
      const res = await restoreVersionBackup(name);
      showMsg("ok", res.message || "恢复成功，请重启服务");
    } catch {
      showMsg("err", "恢复备份失败");
    } finally {
      setRestoringBackup(null);
    }
  };

  const handleMigrateData = async () => {
    if (!confirm("将当前安装的数据迁移到集中存储位置，确定继续？")) return;
    setMigrating(true);
    try {
      await migrateVersionData();
      showMsg("ok", "数据迁移完成");
    } catch {
      showMsg("err", "数据迁移失败");
    } finally {
      setMigrating(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin mr-2" />
        加载版本信息…
      </div>
    );
  }

  const totalBackupMB = backups.reduce((s, b) => s + b.size_mb, 0);

  return (
    <div className="space-y-5">
      {/* ── 操作反馈 ── */}
      {actionMsg && (
        <div
          className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${
            actionMsg.type === "ok"
              ? "bg-green-500/10 text-green-700 dark:text-green-400"
              : "bg-red-500/10 text-red-700 dark:text-red-400"
          }`}
        >
          {actionMsg.type === "ok" ? (
            <CheckCircle2 className="h-4 w-4 shrink-0" />
          ) : (
            <AlertCircle className="h-4 w-4 shrink-0" />
          )}
          {actionMsg.text}
        </div>
      )}

      {/* ── 当前版本 ── */}
      <div className="rounded-lg border border-border p-4">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <span style={{ color: "var(--em-primary)" }}>
              <Sparkles className="h-5 w-5" />
            </span>
            <div>
              <div className="text-sm font-semibold flex items-center gap-2">
                ExcelManus
                <Badge variant="secondary" className="text-xs font-mono">
                  v{version?.current || "unknown"}
                </Badge>
              </div>
              <div className="text-[11px] text-muted-foreground mt-0.5">
                {version?.has_update ? (
                  <span className="text-amber-600 dark:text-amber-400 flex items-center gap-1">
                    <ArrowUpCircle className="h-3 w-3" />
                    可更新到 v{version.latest}（{version.commits_behind} 个新提交）
                  </span>
                ) : (
                  "已是最新版本"
                )}
              </div>
            </div>
          </div>
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-1.5 sm:gap-2 shrink-0 mt-2 sm:mt-0">
            {version?.has_update && (
              <Button
                variant="default"
                size="sm"
                disabled={updating}
                onClick={handleApplyUpdate}
                className="gap-1.5 h-8"
              >
                {updating ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Download className="h-3.5 w-3.5" />
                )}
                执行更新
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              disabled={checking}
              onClick={handleCheckUpdate}
              className="gap-1.5 h-8"
            >
              {checking ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              检查更新
            </Button>
          </div>
        </div>
        {version?.has_update && version.release_notes && (
          <div className="mt-3 pt-3 border-t border-border">
            <p className="text-[11px] font-medium text-muted-foreground mb-1">更新日志</p>
            <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap max-h-32 overflow-y-auto font-mono bg-muted/30 rounded-md p-2">
              {version.release_notes}
            </pre>
          </div>
        )}
      </div>

      {/* ── 桌面快捷方式 ── */}
      {shortcut && (
        <>
          <Separator />
          <div className="rounded-lg border border-border p-4">
            <div className="flex items-center justify-between gap-2 sm:gap-3">
              <div className="flex items-center gap-2 sm:gap-2.5 min-w-0">
                <span className="shrink-0" style={{ color: "var(--em-primary)" }}>
                  <MapPin className="h-5 w-5" />
                </span>
                <div className="min-w-0">
                  <div className="text-sm font-semibold">桌面快捷方式</div>
                  <div className="text-[11px] text-muted-foreground mt-0.5 truncate">
                    {shortcut.exists ? (
                      <span className="text-green-600 dark:text-green-400 inline-flex items-center gap-1 max-w-full">
                        <CheckCircle2 className="h-3 w-3 shrink-0" />
                        <span className="truncate">已创建 · {shortcut.shortcut_path}</span>
                      </span>
                    ) : (
                      "未创建 — 点击按钮一键添加"
                    )}
                  </div>
                </div>
              </div>
              <Button
                variant="outline"
                size="sm"
                disabled={shortcutBusy}
                onClick={handleToggleShortcut}
                className="gap-1.5 h-8 shrink-0"
              >
                {shortcutBusy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : shortcut.exists ? (
                  <Trash2 className="h-3.5 w-3.5" />
                ) : (
                  <CheckCircle2 className="h-3.5 w-3.5" />
                )}
                <span className="hidden sm:inline">{shortcut.exists ? "删除" : "创建"}</span>
              </Button>
            </div>
          </div>
        </>
      )}

      <Separator />

      {/* ── 更新备份 ── */}
      <div>
        <div className="flex flex-wrap items-center gap-1.5 mb-3">
          <span style={{ color: "var(--em-primary)" }}>
            <FolderArchive className="h-3.5 w-3.5" />
          </span>
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            更新备份
          </h3>
          <span className="text-[10px] text-muted-foreground ml-auto mr-2">
            {backups.length} 个 · {totalBackupMB.toFixed(1)} MB
          </span>
          {backups.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              disabled={cleaningUp}
              onClick={handleCleanupBackups}
              className="gap-1 h-6 text-[11px] text-muted-foreground hover:text-destructive px-2"
            >
              {cleaningUp ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Eraser className="h-3 w-3" />
              )}
              清理旧备份
            </Button>
          )}
        </div>

        {backups.length === 0 ? (
          <div className="text-center py-6 text-muted-foreground text-sm">
            暂无更新备份
          </div>
        ) : (
          <div className="space-y-2">
            {backups.map((b) => (
              <div
                key={b.name}
                className="flex items-center gap-3 rounded-lg border border-border px-3 py-2.5 group"
              >
                <HardDrive className="h-4 w-4 text-muted-foreground shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">v{b.version}</div>
                  <div className="text-[11px] text-muted-foreground">
                    {formatTimestamp(b.timestamp)} · {b.size_mb.toFixed(1)} MB
                  </div>
                </div>
                <div className="flex items-center gap-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity shrink-0">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground hover:text-foreground"
                    title="从此备份恢复"
                    disabled={restoringBackup === b.name}
                    onClick={() => handleRestoreBackup(b.name)}
                  >
                    {restoringBackup === b.name ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <RotateCcw className="h-3.5 w-3.5" />
                    )}
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground hover:text-destructive"
                    title="删除备份"
                    disabled={deletingBackup === b.name}
                    onClick={() => handleDeleteBackup(b.name)}
                  >
                    {deletingBackup === b.name ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Trash2 className="h-3.5 w-3.5" />
                    )}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <Separator />

      {/* ── 安装记录 ── */}
      <div>
        <div className="flex items-center gap-1.5 mb-3">
          <span style={{ color: "var(--em-primary)" }}>
            <MapPin className="h-3.5 w-3.5" />
          </span>
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            安装记录
          </h3>
          <span className="text-[10px] text-muted-foreground ml-auto">
            {installations.length} 个安装
          </span>
        </div>

        {installations.length === 0 ? (
          <div className="text-center py-6 text-muted-foreground text-sm">
            暂无安装记录
          </div>
        ) : (
          <div className="space-y-2">
            {installations.map((inst) => (
              <div
                key={inst.path}
                className="flex items-center gap-3 rounded-lg border border-border px-3 py-2.5 group"
              >
                <MapPin className="h-4 w-4 text-muted-foreground shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate font-mono">{inst.path}</div>
                  <div className="text-[11px] text-muted-foreground flex items-center gap-2">
                    <Badge variant="outline" className="text-[10px] h-4 px-1">
                      v{inst.version}
                    </Badge>
                    {inst.platform && <span>{inst.platform}</span>}
                    {inst.last_seen && (
                      <span>最后活跃: {formatTimestamp(inst.last_seen)}</span>
                    )}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 text-muted-foreground hover:text-destructive opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity shrink-0"
                  disabled={deletingInstall === inst.path}
                  onClick={() => handleDeleteInstallation(inst.path)}
                >
                  {deletingInstall === inst.path ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Trash2 className="h-3.5 w-3.5" />
                  )}
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      <Separator />

      {/* ── 数据迁移 ── */}
      <div>
        <div className="flex items-center gap-1.5 mb-3">
          <span style={{ color: "var(--em-primary)" }}>
            <DatabaseBackup className="h-3.5 w-3.5" />
          </span>
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            数据迁移
          </h3>
        </div>
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2.5 rounded-lg border border-border px-3 py-2.5">
          <div className="text-sm text-muted-foreground">
            将当前安装的用户数据迁移到集中存储位置
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={migrating}
            onClick={handleMigrateData}
            className="gap-1.5 h-8 shrink-0 w-full sm:w-auto"
          >
            {migrating ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <DatabaseBackup className="h-3.5 w-3.5" />
            )}
            开始迁移
          </Button>
        </div>
      </div>

      {/* ── 说明 ── */}
      <div className="rounded-lg bg-muted/30 px-3 py-2.5 text-[11px] text-muted-foreground">
        <p>
          <strong>更新备份</strong>：每次更新前自动备份用户数据（.env、uploads、outputs 等），
          系统默认保留最近 2 个备份，超出自动清理。可点击恢复按钮从备份还原。
        </p>
        <p className="mt-1">
          <strong>安装记录</strong>：记录本机所有安装路径，便于多版本共存时定位数据。
          删除记录不影响实际安装。
        </p>
        <p className="mt-1">
          <strong>数据迁移</strong>：将项目内的数据文件迁移到系统集中位置，
          方便多版本共存与升级后数据保留。
        </p>
      </div>
    </div>
  );
}
