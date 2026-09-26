"use client";

import { SECURITY_SETTING_GROUPS } from "./settings-catalog";
import { useEffect, useState, type FormEvent } from "react";
import { Loader2, LogOut, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { fetchAccessSettings, logoutFromInstance, saveAccessSettings, type AccessSettings } from "@/lib/access-api";
import { setManageToken } from "@/lib/api";
import { RuntimeSettingsPanel } from "./RuntimeSettingsPanel";
import { SettingsPageLayout, SettingsPagePanel } from "./SettingsPageLayout";



export function AccessTab() {
  const [settings, setSettings] = useState<AccessSettings | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetchAccessSettings().then((data) => {
      if (cancelled) return;
      setSettings(data);
      setEnabled(data.enabled);
      setUsername(data.username);
    }).catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : "无法读取登录设置"); });
    return () => { cancelled = true; };
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError("");
    if (password !== confirm) { setError("两次输入的密码不一致"); return; }
    if (password && password.length < 12) { setError("密码至少需要 12 个字符"); return; }
    setBusy(true);
    try {
      await saveAccessSettings({ enabled, username: username.trim(), password });
      setManageToken("");
      window.location.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败，请重试");
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    setError("");
    try { await logoutFromInstance(); }
    catch (err) { setError(err instanceof Error ? err.message : "退出失败，请重试"); setBusy(false); }
  }

  if (!settings) return <div role="status" className="py-8 text-sm text-muted-foreground">{error || "正在读取登录设置…"}</div>;
  const needsPassword = enabled && !settings.password_configured && !settings.manage_token_configured;
  return (
    <SettingsPageLayout className="space-y-5 pb-2">
    <form onSubmit={save} className="space-y-4" aria-busy={busy}>
      <SettingsPagePanel>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-[13px] font-semibold"><ShieldCheck className="h-4 w-4 text-[var(--em-primary)]" /><label htmlFor="login-protection">登录保护</label></h2>
            <p className="mt-2 text-[11px] leading-[1.65] text-muted-foreground">开启后，需要管理员登录才能使用工作区、文件和设置。</p>
          </div>
          <Switch id="login-protection" checked={enabled} onCheckedChange={setEnabled} disabled={busy} />
        </div>
        {!enabled && <p className="mt-4 rounded-xl bg-amber-500/10 p-3 text-[11px] leading-[1.65] text-amber-800 dark:text-amber-300">关闭后，任何能访问此地址的人都可以直接进入工作区。服务器部署建议保持开启。</p>}
      </SettingsPagePanel>
      <SettingsPagePanel>
        <h2 className="text-[13px] font-semibold">管理员凭据</h2>
        <p className="mt-2 text-[11px] leading-[1.65] text-muted-foreground">此实例共用一个管理员账号。{settings.password_configured ? "密码留空时保留现有密码。" : settings.manage_token_configured ? "当前可使用管理令牌登录，也可以在这里设置账号密码。" : "开启前请先设置密码。"}</p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <label htmlFor="access-username" className="mb-1.5 block text-[11px] font-medium">管理员账号</label>
            <Input className="h-9 text-xs md:text-xs" id="access-username" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required maxLength={128} value={username} onChange={(e) => setUsername(e.target.value)} disabled={busy} />
          </div>
          <div>
            <label htmlFor="access-password" className="mb-1.5 block text-[11px] font-medium">{settings.password_configured ? "新密码" : "密码"}</label>
            <Input className="h-9 text-xs md:text-xs" id="access-password" name="new-password" type="password" autoComplete="new-password" minLength={12} maxLength={4096} required={needsPassword} value={password} onChange={(e) => setPassword(e.target.value)} disabled={busy} placeholder={settings.password_configured ? "留空则不修改" : "至少 12 个字符"} />
          </div>
          <div>
            <label htmlFor="access-confirm" className="mb-1.5 block text-[11px] font-medium">确认密码</label>
            <Input className="h-9 text-xs md:text-xs" id="access-confirm" name="confirm-password" type="password" autoComplete="new-password" maxLength={4096} required={!!password} value={confirm} onChange={(e) => setConfirm(e.target.value)} disabled={busy} placeholder="再次输入密码" />
          </div>
        </div>
        <p className="mt-4 text-[11px] leading-[1.65] text-muted-foreground">登录有效期为 {settings.session_hours} 小时。保存后立即生效；开启保护或修改凭据后，所有浏览器都需要重新登录。</p>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border/60 pt-3">
          {settings.enabled && <Button type="button" variant="outline" size="sm" className="text-xs" onClick={logout} disabled={busy}><LogOut className="h-4 w-4" />退出登录</Button>}
          <Button type="submit" disabled={busy} size="sm" className="ml-auto text-xs">{busy && <Loader2 className="h-4 w-4 animate-spin" />}保存登录设置</Button>
        </div>
      </SettingsPagePanel>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </form>
    <RuntimeSettingsPanel groups={SECURITY_SETTING_GROUPS} />
    </SettingsPageLayout>
  );
}
