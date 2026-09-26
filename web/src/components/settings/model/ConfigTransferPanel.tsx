"use client";

import { useState } from "react";
import {
  Download, Import, X, Lock, Unlock, Dices, Check, CheckCircle2,
  Loader2, ClipboardPaste, Copy,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MiniCheckbox } from "@/components/ui/MiniCheckbox";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { apiPost } from "@/lib/api";
export function ConfigTransferPanel({ onImported }: { onImported?: () => void }) {
  const [mode, setMode] = useState<"idle" | "export" | "import">("idle");
  const [exportMode, setExportMode] = useState<"password" | "simple">("password");
  const [exportSections, setExportSections] = useState<Record<string, boolean>>({
    profiles: true,
  });
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [importToken, setImportToken] = useState("");
  const [importPassword, setImportPassword] = useState("");
  const [resultToken, setResultToken] = useState("");
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [copied, setCopied] = useState(false);
  const [importResult, setImportResult] = useState<{ status: string; imported: Record<string, unknown> } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [needsPassword, setNeedsPassword] = useState<boolean | null>(null);

  const resetState = () => {
    setMode("idle");
    setPassword("");
    setConfirmPassword("");
    setImportToken("");
    setImportPassword("");
    setResultToken("");
    setImportResult(null);
    setError(null);
    setNeedsPassword(null);
    setCopied(false);
  };

  const handleExport = async () => {
    setError(null);
    if (exportMode === "password") {
      if (!password) { setError("请输入加密密码"); return; }
      if (password !== confirmPassword) { setError("两次密码不一致"); return; }
    }
    setExporting(true);
    try {
      const sections = Object.entries(exportSections)
        .filter(([k, v]) => k === "profiles" && v)
        .map(([k]) => k);
      const data = await apiPost<{ token: string }>("/config/export", {
        sections,
        mode: exportMode,
        password: exportMode === "password" ? password : null,
      }, { direct: true });
      setResultToken(data.token);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导出失败");
    } finally {
      setExporting(false);
    }
  };

  const handleDetectToken = async (token: string) => {
    setImportToken(token);
    setNeedsPassword(null);
    setError(null);
    if (!token.trim()) return;
    try {
      const data = await apiPost<{ needs_password: boolean }>("/config/transfer/detect", { token }, { direct: true });
      setNeedsPassword(data.needs_password);
    } catch {
      setNeedsPassword(null);
    }
  };

  const handleImport = async () => {
    setError(null);
    if (!importToken.trim()) { setError("请粘贴配置令牌"); return; }
    if (needsPassword && !importPassword) { setError("此令牌需要密码"); return; }
    setImporting(true);
    try {
      const data = await apiPost<{ status: string; imported: Record<string, unknown>; exported_at: string }>("/config/import", {
        token: importToken,
        password: needsPassword ? importPassword : null,
      }, { direct: true });
      setImportResult(data);
      if (Array.isArray(data.imported.profiles) && data.imported.profiles.length > 0) {
        onImported?.();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败");
      // 导入可能部分成功或响应中断，重新核对已提交的服务端档案。
      onImported?.();
    } finally {
      setImporting(false);
    }
  };

  const handleCopy = async () => {
    await navigator.clipboard.writeText(resultToken);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const sectionLabels: Record<string, string> = {
    profiles: "模型档案",
  };

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-3">
        <div className="min-w-0">
          <h3 className="font-semibold text-sm flex items-center gap-1.5">
            <Download className="h-4 w-4 flex-shrink-0" style={{ color: "var(--em-primary)" }} />
            配置导出 / 导入
          </h3>
          <p className="text-xs text-muted-foreground">
            一键导出全局模型配置（含 Key），加密分享给他人
          </p>
        </div>
        <div className="flex gap-1.5 flex-shrink-0 flex-wrap">
          {mode !== "export" && (
            <Button size="sm" variant="outline" className="h-8 sm:h-7 text-xs gap-1" onClick={() => { resetState(); setMode("export"); }}>
              <Download className="h-3 w-3" /> 导出
            </Button>
          )}
          {mode !== "import" && (
            <Button size="sm" variant="outline" className="h-8 sm:h-7 text-xs gap-1" onClick={() => { resetState(); setMode("import"); }}>
              <Import className="h-3 w-3" /> 导入
            </Button>
          )}
          {mode !== "idle" && (
            <Button size="sm" variant="ghost" className="h-8 sm:h-7 text-xs gap-1" onClick={resetState}>
              <X className="h-3 w-3" /> 关闭
            </Button>
          )}
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 mb-3">
          <p className="text-xs text-destructive">{error}</p>
        </div>
      )}

      {/* Export Panel */}
      {mode === "export" && !resultToken && (
        <div className="rounded-lg border border-dashed border-border p-3 space-y-3">
          <div>
            <p className="text-xs font-medium mb-2">选择导出区块</p>
            <div className="flex flex-wrap gap-x-3 gap-y-1.5">
              {Object.entries(sectionLabels).map(([key, label]) => (
                <MiniCheckbox
                  key={key}
                  checked={exportSections[key]}
                  onChange={(v) => setExportSections((prev) => ({ ...prev, [key]: v }))}
                  label={label}
                />
              ))}
            </div>
          </div>
          <div>
            <p className="text-xs font-medium mb-2">加密模式</p>
            <RadioGroup
              value={exportMode}
              onValueChange={(next) => setExportMode(next as typeof exportMode)}
              className="flex flex-col sm:flex-row gap-2 sm:gap-3"
            >
              <label className="inline-flex items-center gap-1.5 text-xs cursor-pointer">
                <RadioGroupItem value="password" />
                <Lock className="h-3 w-3" /> 口令加密（推荐）
              </label>
              <label className="inline-flex items-center gap-1.5 text-xs cursor-pointer">
                <RadioGroupItem value="simple" />
                <Unlock className="h-3 w-3" /> 简单分享
              </label>
            </RadioGroup>
          </div>
          {exportMode === "password" && (
            <div className="space-y-2">
              <div>
                <div className="flex items-center justify-between mb-0.5">
                  <label className="text-xs text-muted-foreground">设置密码</label>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 text-[11px] hover:underline"
                    style={{ color: "var(--em-primary)" }}
                    onClick={() => {
                      const chars = "ABCDEFGHJKMNPQRSTWXYZabcdefghjkmnpqrstwxyz23456789!@#$&*";
                      const arr = new Uint8Array(16);
                      crypto.getRandomValues(arr);
                      const pw = Array.from(arr, (b) => chars[b % chars.length]).join("");
                      setPassword(pw);
                      setConfirmPassword(pw);
                    }}
                  >
                    <Dices className="h-3 w-3" /> 随机生成
                  </button>
                </div>
                <Input type={password && password === confirmPassword && password.length >= 12 ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} className="h-7 text-xs font-mono" placeholder="输入加密密码..." />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">确认密码</label>
                <Input type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} className="h-7 text-xs font-mono" placeholder="再次输入密码..." />
              </div>
              {password && password === confirmPassword && password.length >= 12 && (
                <p className="text-[11px] text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                  <Check className="h-3 w-3" /> 密码已就绪，请妥善记录后发送给接收方
                </p>
              )}
            </div>
          )}
          {exportMode === "simple" && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400">
              简单分享模式使用内置密钥，不能防止逆向工程。建议仅在信任的环境中使用。
            </p>
          )}
          <div className="flex justify-end">
            <Button
              size="sm"
              className="h-7 text-xs gap-1 text-white"
              style={{ backgroundColor: "var(--em-primary)" }}
              onClick={handleExport}
              disabled={exporting || !exportSections.profiles}
            >
              {exporting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
              {exporting ? "加密中..." : "生成令牌"}
            </Button>
          </div>
        </div>
      )}

      {/* Export Result */}
      {mode === "export" && resultToken && (
        <div className="rounded-lg border border-border p-3 space-y-3">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            <span className="text-sm font-medium">配置导出成功</span>
          </div>
          <div className="relative">
            <Textarea
              readOnly
              value={resultToken}
              className="h-20 resize-none rounded-lg border-border bg-muted/30 px-3 py-2 text-[10px] font-mono"
            />
            <Button
              size="sm"
              variant="outline"
              className="absolute top-2 right-2 h-6 text-[10px] gap-1"
              onClick={handleCopy}
            >
              {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
              {copied ? "已复制" : "复制"}
            </Button>
          </div>
          {exportMode === "password" && (
            <p className="text-[11px] text-muted-foreground">
              请将此令牌和密码一起发送给接收方。没有密码无法解密。
            </p>
          )}
        </div>
      )}

      {/* Import Panel */}
      {mode === "import" && !importResult && (
        <div className="rounded-lg border border-dashed border-border p-3 space-y-3">
          <div>
            <label className="text-xs text-muted-foreground">粘贴配置令牌</label>
            <Textarea
              value={importToken}
              onChange={(e) => handleDetectToken(e.target.value)}
              className="mt-1 h-20 resize-none rounded-lg px-3 py-2 text-[10px] font-mono"
              placeholder="粘贴 EMX1:... 令牌"
            />
          </div>
          {needsPassword === true && (
            <div>
              <label className="text-xs text-muted-foreground flex items-center gap-1">
                <Lock className="h-3 w-3" /> 此令牌需要密码
              </label>
              <Input
                type="password"
                value={importPassword}
                onChange={(e) => setImportPassword(e.target.value)}
                className="h-7 text-xs mt-1"
                placeholder="输入解密密码..."
              />
            </div>
          )}
          {needsPassword === false && (
            <p className="text-[11px] text-muted-foreground flex items-center gap-1">
              <Unlock className="h-3 w-3" /> 简单分享模式，无需密码
            </p>
          )}
          <div className="flex justify-end">
            <Button
              size="sm"
              className="h-7 text-xs gap-1 text-white"
              style={{ backgroundColor: "var(--em-primary)" }}
              onClick={handleImport}
              disabled={importing || !importToken.trim()}
            >
              {importing ? <Loader2 className="h-3 w-3 animate-spin" /> : <ClipboardPaste className="h-3 w-3" />}
              {importing ? "导入中..." : "导入配置"}
            </Button>
          </div>
        </div>
      )}

      {/* Import Result */}
      {mode === "import" && importResult && (
        <div className="rounded-lg border border-border p-3 space-y-2">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            <span className="text-sm font-medium">配置导入成功</span>
          </div>
          <div className="text-xs text-muted-foreground space-y-1">
            {Object.entries(importResult.imported).map(([key, value]) => (
              <p key={key}>
                <span className="font-medium">{sectionLabels[key] || key}</span>：
                {Array.isArray(value) ? value.join(", ") : String(value)}
              </p>
            ))}
          </div>
          <p className="text-[11px] text-amber-600 dark:text-amber-400">
            配置已导入，模型列表已刷新。
          </p>
        </div>
      )}

      {mode === "idle" && (
        <p className="text-xs text-muted-foreground text-center py-3">
          导出配置可加密分享给他人，导入令牌即可一键还原所有模型设置
        </p>
      )}
    </div>
  );
}
