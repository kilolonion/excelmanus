"use client";

import { useEffect, useState, useCallback } from "react";
import { useConnectionStore } from "@/stores/connection-store";
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
  Rocket,
  Server,
  Package,
  Globe,
  History,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  apiGet,
  apiPost,
  cleanupVersionBackups,
  restoreVersionBackup,
  migrateVersionData,
  fetchDeployStatus,
  buildFrontendArtifact,
  executeRemoteDeploy,
  startVersionUpgrade,
} from "@/lib/api";
import type { DeployStatusInfo, DeployResult, VersionManifest, WebUpgradeCapability } from "@/lib/api";
import { fetchVersionManifest } from "@/lib/api";
import { useAuthConfigStore } from "@/stores/auth-config-store";
import { RollbackPanel } from "@/components/settings/RollbackPanel";
import { ProjectLinks } from "@/components/settings/ProjectLinks";
import { DesktopUpdateCard } from "@/components/settings/DesktopUpdateCard";
import { appRefreshBlocker } from "@/lib/app-refresh";

interface VersionInfo {
  current: string;
  latest: string;
  has_update: boolean;
  commits_behind: number;
  release_notes: string;
  check_method: string;
  check_failed?: boolean;
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
  const deployMode = useAuthConfigStore((s) => s.deployMode);

  const [version, setVersion] = useState<VersionInfo | null>(null);
  const isDesktopApp = (typeof window !== "undefined" && !!window.excelManusDesktop) || version?.check_method === "desktop_installer";
  const isStandalone = deployMode === "standalone" && !isDesktopApp;
  const [backups, setBackups] = useState<BackupEntry[]>([]);
  const [installations, setInstallations] = useState<InstallationEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [deletingBackup, setDeletingBackup] = useState<string | null>(null);
  const [deletingInstall, setDeletingInstall] = useState<string | null>(null);
  const [updating, setUpdating] = useState(false);
  const [upgradeCapability, setUpgradeCapability] = useState<WebUpgradeCapability | null>(null);
  const [cleaningUp, setCleaningUp] = useState(false);
  const [restoringBackup, setRestoringBackup] = useState<string | null>(null);
  const [migrating, setMigrating] = useState(false);
  const [deployStatus, setDeployStatus] = useState<DeployStatusInfo | null>(null);
  const [deploying, setDeploying] = useState(false);
  const [buildingArtifact, setBuildingArtifact] = useState(false);
  const [deployTarget, setDeployTarget] = useState<"full" | "backend" | "frontend">("full");
  const [deploySkipBuild, setDeploySkipBuild] = useState(false);
  const [deployOutput, setDeployOutput] = useState<string | null>(null);
  const [currentGitCommit, setCurrentGitCommit] = useState<string | null>(null);
  const [lastUpgrade, setLastUpgrade] = useState<VersionManifest["last_upgrade"]>(null);
  const [actionMsg, setActionMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const triggerRestart = useConnectionStore((s) => s.triggerRestart);

  const showMsg = (type: "ok" | "err", text: string) => {
    setActionMsg({ type, text });
    setTimeout(() => setActionMsg(null), 3000);
  };

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setActionMsg(null);
    try {
      const v = await apiGet<VersionInfo>("/version/check");
      setVersion(v);
      // A packaged app has no source checkout, deployment tools or source
      // installation registry. Do not invoke those endpoints from Desktop.
      if (window.excelManusDesktop || v.check_method === "desktop_installer") return;
      const [b, i, ds, manifest, capability] = await Promise.all([
        apiGet<{ backups: BackupEntry[] }>("/version/backups"),
        apiGet<{ installations: InstallationEntry[] }>("/version/installations"),
        fetchDeployStatus().catch(() => null),
        fetchVersionManifest().catch(() => null),
        apiGet<WebUpgradeCapability>("/version/upgrade/capability", { cache: "no-store" }).catch(() => null),
      ]);
      setBackups(b.backups ?? []);
      setInstallations(i.installations ?? []);
      if (ds) setDeployStatus(ds);
      if (manifest?.git_commit) setCurrentGitCommit(manifest.git_commit);
      setLastUpgrade(manifest?.last_upgrade ?? null);
      setUpgradeCapability(capability);
    } catch {
      setActionMsg({ type: "err", text: "版本信息加载失败，请重试。" });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const handleCheckUpdate = async () => {
    setChecking(true);
    try {
      const v = await apiGet<VersionInfo>("/version/check?force=1");
      setVersion(v);
      if (v.check_method === "desktop_installer") {
        showMsg("ok", "桌面版请下载并安装新版 App，用户数据会保留");
      } else if (v.check_failed) {
        showMsg("err", v.error ? `检查更新失败: ${v.error}` : "检查更新失败");
      } else if (v.has_update) {
        const label =
          v.latest && v.latest !== v.current
            ? `发现新版本 ${v.latest}（落后 ${v.commits_behind} 个提交）`
            : `发现 ${v.commits_behind} 个新提交`;
        showMsg("ok", label);
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
    const blocked = appRefreshBlocker();
    if (blocked) { showMsg("err", blocked); return; }
    if (!confirm("更新 ExcelManus 前后端？会先备份应用数据，再更新程序并重建网页。期间短暂断开连接，完成后自动恢复。不会删除、移动或清空工作区和用户文件；设置和会话会保留。源码存在未提交修改时会停止更新。")) return;
    setUpdating(true);
    try {
      const result = await startVersionUpgrade({ useMirror: false });
      if (!result.accepted || !result.request_id) throw new Error(result.error || "服务器未返回更新编号，请检查服务端版本");
      await triggerRestart("网页更新：仅更新 ExcelManus，工作区和用户文件保留原位", { requireVersionChange: true, upgradeRequestId: result.request_id });
    } catch (err) {
      showMsg("err", `更新失败: ${err instanceof Error ? err.message : "未知错误"}`);
    } finally { setUpdating(false); }
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
    if (!confirm(`确定从备份 ${name} 恢复数据？服务会先停止再恢复并重启。`)) return;
    setRestoringBackup(name);
    try {
      await restoreVersionBackup(name);
      showMsg("ok", "已开始停机恢复，正在等待服务重启…");
      void triggerRestart("正在从备份恢复并重启", { requireVersionChange: false });
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

  const handleBuildArtifact = async () => {
    if (!confirm("开始本地构建前端制品？这可能需要几分钟。")) return;
    setBuildingArtifact(true);
    setDeployOutput(null);
    try {
      const res: DeployResult = await buildFrontendArtifact();
      if (res.success) {
        showMsg("ok", `制品构建完成: ${res.artifact_path?.split(/[\\/]/).pop() ?? ""}`);
        const ds = await fetchDeployStatus().catch(() => null);
        if (ds) setDeployStatus(ds);
      } else {
        showMsg("err", `构建失败: ${res.error ?? "未知错误"}`);
      }
    } catch {
      showMsg("err", "构建请求失败");
    } finally {
      setBuildingArtifact(false);
    }
  };

  const handleDeploy = async () => {
    const targetLabel = { full: "完整（前后端）", backend: "仅后端", frontend: "仅前端" }[deployTarget];
    if (!confirm(`确定执行远程部署（${targetLabel}）？`)) return;
    setDeploying(true);
    setDeployOutput(null);
    try {
      const latestArtifact = deployStatus?.artifacts?.[0]?.path ?? "";
      const res: DeployResult = await executeRemoteDeploy({
        target: deployTarget,
        skipBuild: deploySkipBuild && !!latestArtifact,
        artifactPath: deploySkipBuild ? latestArtifact : "",
      });
      if (res.success) {
        showMsg("ok", "远程部署完成！");
      } else {
        showMsg("err", `部署失败: ${res.error ?? "未知错误"}`);
      }
      if (res.deploy_output) setDeployOutput(res.deploy_output);
      const ds = await fetchDeployStatus().catch(() => null);
      if (ds) setDeployStatus(ds);
    } catch {
      showMsg("err", "部署请求失败");
    } finally {
      setDeploying(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-center py-12 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin mr-2" />
          加载版本信息…
        </div>
        <ProjectLinks />
      </div>
    );
  }

  if (isDesktopApp) {
    return <div className="space-y-4">
      <DesktopUpdateCard current={version?.current || process.env.NEXT_PUBLIC_APP_VERSION || "unknown"} />
      <p className="text-xs leading-relaxed text-muted-foreground">备份应用数据时，可从应用菜单「文件 → 打开数据目录」找到数据位置，退出应用后复制该目录。添加在其他位置的工作区文件夹需要单独备份。日志位于「帮助 → 打开日志目录」。</p>
      <ProjectLinks />
      {actionMsg?.type === "err" && <div role="status" className="text-sm text-destructive">
        {actionMsg.text} <button type="button" className="underline" onClick={() => void fetchAll()}>重试</button>
      </div>}
    </div>;
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

      {lastUpgrade && lastUpgrade.ok === false && lastUpgrade.error && (
        <div className="flex items-start gap-2 rounded-lg px-3 py-2 text-sm bg-red-500/10 text-red-700 dark:text-red-400">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
          <div>
            <p className="font-medium">上次停机更新未完成</p>
            <p className="text-[11px] mt-0.5 whitespace-pre-wrap">{lastUpgrade.error}</p>
          </div>
        </div>
      )}
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
                {version?.check_method === "desktop_installer" ? (
                  "桌面版通过新版安装包更新，用户数据会保留"
                ) : version?.has_update ? (
                  <span className="text-amber-600 dark:text-amber-400 flex items-center gap-1">
                    <ArrowUpCircle className="h-3 w-3" />
                    {version.latest && version.latest !== version.current
                      ? `可更新到 v${version.latest}（${version.commits_behind} 个新提交）`
                      : `有 ${version.commits_behind} 个新提交可更新`}
                  </span>
                ) : version?.check_failed ? (
                  <span className="text-amber-600 dark:text-amber-400 flex items-center gap-1">
                    <AlertCircle className="h-3 w-3" />
                    版本检查失败，请点击「检查更新」重试
                  </span>
                ) : (
                  "已是最新版本"
                )}
              </div>
            </div>
          </div>
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-1.5 sm:gap-2 shrink-0 mt-2 sm:mt-0">
            {version?.has_update && upgradeCapability?.supported && (
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
                一键更新前后端
              </Button>
            )}
            {version?.has_update && !upgradeCapability?.supported && (
              <Badge variant="outline" className="text-[10px] h-6 px-2 text-amber-600 dark:text-amber-400">
                此实例暂不能从网页更新
              </Badge>
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
        <div className="mt-3 space-y-1 text-xs leading-relaxed text-muted-foreground">
          <p>网页热更新会更新 ExcelManus 前后端并自动恢复连接，设置和会话继续沿用。不会删除、搬迁或清空工作区、表格、文档及其他用户文件。</p>
          <p>服务器发布新版后，当前页面也会提示刷新；未保存的表格修改和运行中的任务会阻止刷新。</p>
          {!upgradeCapability?.supported && <p className="text-amber-700 dark:text-amber-400">{upgradeCapability?.reason || "当前服务尚未提供网页更新能力，请先升级服务端。"}</p>}
        </div>
        {version?.has_update && version.release_notes && !updating && (
          <div className="mt-3 pt-3 border-t border-border">
            <p className="text-[11px] font-medium text-muted-foreground mb-1">更新日志</p>
            <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap max-h-32 overflow-y-auto font-mono bg-muted/30 rounded-md p-2">
              {version.release_notes}
            </pre>
          </div>
        )}
      </div>

      <Separator />

      <ProjectLinks />

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
          {isStandalone && backups.length > 0 && (
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
                  {isStandalone && (
                    <>
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
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── 安装记录 ── */}
      {isStandalone && <Separator />}
      {isStandalone && <div>
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
                  <div className="text-sm font-medium font-mono break-all">{inst.path}</div>
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
      </div>}

      {/* ── 数据迁移 ── */}
      {isStandalone && <Separator />}
      {isStandalone && <div>
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
      </div>}

      {/* ── 远程部署 ── */}
      {isStandalone && deployStatus?.env_deploy_found && (
        <>
          <Separator />
          <div>
            <div className="flex items-center gap-1.5 mb-3">
              <span style={{ color: "var(--em-primary)" }}>
                <Rocket className="h-3.5 w-3.5" />
              </span>
              <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                远程部署
              </h3>
              {deployStatus.is_deploying && (
                <Badge variant="secondary" className="text-[10px] h-4 px-1.5 gap-1 ml-1">
                  <Loader2 className="h-2.5 w-2.5 animate-spin" />
                  部署中
                </Badge>
              )}
            </div>

            {/* 服务器信息 */}
            <div className="rounded-lg border border-border p-3 space-y-2.5">
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                {deployStatus.servers?.backend && (
                  <span className="flex items-center gap-1">
                    <Server className="h-3 w-3" />
                    后端: <code className="font-mono">{deployStatus.servers.backend}</code>
                  </span>
                )}
                {deployStatus.servers?.frontend && (
                  <span className="flex items-center gap-1">
                    <Globe className="h-3 w-3" />
                    前端: <code className="font-mono">{deployStatus.servers.frontend}</code>
                  </span>
                )}
                {deployStatus.site_urls?.length > 0 && (
                  <span className="flex items-center gap-1">
                    站点: {deployStatus.site_urls.map((u, i) => (
                      <code key={i} className="font-mono">{u}</code>
                    ))}
                  </span>
                )}
              </div>

              {!deployStatus.env_deploy_found && (
                <div className="text-[11px] text-amber-600 dark:text-amber-400 flex items-center gap-1">
                  <AlertCircle className="h-3 w-3" />
                  未找到 deploy/.env.deploy 配置文件，请先配置服务器信息
                </div>
              )}

              {/* 制品列表 */}
              {deployStatus.artifacts?.length > 0 && (
                <div className="text-[11px] text-muted-foreground">
                  <span className="flex items-center gap-1 mb-1">
                    <Package className="h-3 w-3" />
                    已有制品:
                  </span>
                  {deployStatus.artifacts.slice(0, 3).map((a) => (
                    <div key={a.name} className="ml-4 font-mono text-[10px]">
                      {a.name} ({a.size_mb} MB)
                    </div>
                  ))}
                </div>
              )}

              {/* 部署选项 */}
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <select
                  value={deployTarget}
                  onChange={(e) => setDeployTarget(e.target.value as "full" | "backend" | "frontend")}
                  className="h-8 rounded-md border border-input bg-background px-2 text-xs"
                >
                  <option value="full">完整部署（前后端）</option>
                  <option value="backend">仅后端</option>
                  <option value="frontend">仅前端</option>
                </select>
                {deployTarget !== "backend" && deployStatus.artifacts?.length > 0 && (
                  <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground cursor-pointer">
                    <input
                      type="checkbox"
                      checked={deploySkipBuild}
                      onChange={(e) => setDeploySkipBuild(e.target.checked)}
                      className="rounded"
                    />
                    使用已有制品（跳过构建）
                  </label>
                )}
              </div>

              {/* 操作按钮 */}
              <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-1.5 pt-1">
                {deployTarget !== "backend" && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={buildingArtifact || deploying}
                    onClick={handleBuildArtifact}
                    className="gap-1.5 h-8"
                  >
                    {buildingArtifact ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Package className="h-3.5 w-3.5" />
                    )}
                    仅构建制品
                  </Button>
                )}
                <Button
                  variant="default"
                  size="sm"
                  disabled={deploying || buildingArtifact || !deployStatus.env_deploy_found}
                  onClick={handleDeploy}
                  className="gap-1.5 h-8"
                >
                  {deploying ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Rocket className="h-3.5 w-3.5" />
                  )}
                  执行部署
                </Button>
              </div>

              {/* 部署输出 */}
              {deployOutput && (
                <div className="mt-2">
                  <p className="text-[11px] font-medium text-muted-foreground mb-1 flex items-center gap-1">
                    <History className="h-3 w-3" />
                    部署输出
                  </p>
                  <pre className="text-[10px] text-muted-foreground whitespace-pre-wrap max-h-40 overflow-y-auto font-mono bg-muted/30 rounded-md p-2">
                    {deployOutput}
                  </pre>
                </div>
              )}

              {/* 部署历史 */}
              {deployStatus.recent_history?.length > 0 && (
                <div className="mt-1">
                  <p className="text-[11px] font-medium text-muted-foreground mb-1 flex items-center gap-1">
                    <History className="h-3 w-3" />
                    最近部署
                  </p>
                  <div className="text-[10px] font-mono text-muted-foreground space-y-0.5 max-h-24 overflow-y-auto">
                    {deployStatus.recent_history.slice().reverse().map((line, i) => (
                      <div key={i} className={line.includes("SUCCESS") ? "text-green-600 dark:text-green-400" : ""}>
                        {line}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {/* ── 部署回滚 ── */}
      {isStandalone && deployStatus?.env_deploy_found && (
        <>
          <Separator />
          <div>
            <div className="flex items-center gap-1.5 mb-3">
              <span style={{ color: "var(--em-primary)" }}>
                <RotateCcw className="h-3.5 w-3.5" />
              </span>
              <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                部署回滚
              </h3>
            </div>
            <RollbackPanel currentGitCommit={currentGitCommit} />
          </div>
        </>
      )}

      {/* ── 说明 ── */}
      <div className="rounded-lg bg-muted/30 px-3 py-2.5 text-[11px] text-muted-foreground">
        <p>
          <strong>更新备份</strong>：每次更新前自动备份用户数据（主数据库、uploads、outputs 等），
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
        {isStandalone && deployStatus?.env_deploy_found && (
          <>
            <p className="mt-1">
              <strong>远程部署</strong>：本机作为运维控制台，通过 deploy.sh 同步并重启远程服务器。
              需先配置 deploy/.env.deploy。生产 API（server 模式）不能自己部署。
            </p>
            <p className="mt-1">
              <strong>部署回滚</strong>：查看部署历史，回滚到指定 commit（远端 checkout + 重启 + 健康检查）。
            </p>
          </>
        )}
        {deployMode === "server" && (
          <p className="mt-1">
            <strong>服务器部署</strong>：请在运维机运行 <code>./deploy/deploy.sh</code> 同步代码并重启，
            不要从生产 API 升级。
          </p>
        )}
      </div>
    </div>
  );
}
