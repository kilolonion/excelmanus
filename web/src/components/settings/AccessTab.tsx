"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Gauge, Loader2, LogOut, Shield, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { fetchAccessSettings, logoutFromInstance, saveAccessSettings, type AccessSettings } from "@/lib/access-api";
import { setManageToken } from "@/lib/api";
import { RuntimeSettingsPanel, type RuntimeSettingGroup } from "./RuntimeSettingsPanel";

const SECURITY_SETTING_GROUPS: RuntimeSettingGroup[] = [
  {
    title: "代码执行与工具校验",
    description: "设置 Agent 执行代码和调用工具时的风险分级、自动放行范围与参数检查。",
    icon: <Shield className="h-4 w-4" />,
    items: [
      {
        key: "code_policy_enabled",
        label: "代码风险分级",
        desc: "按绿 / 黄 / 红等级判断代码是自动运行还是先请求确认；新任务生效。",
        icon: <Shield className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "code_policy_green_auto_approve",
        label: "绿区自动执行",
        desc: "自动执行低风险代码，并继续限制网络、子进程和工作区外写入。",
        disabledDesc: "开启代码风险分级后可设置。",
        disabledWhen: (settings) => !settings.code_policy_enabled,
        icon: <Shield className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "code_policy_yellow_auto_approve",
        label: "黄区自动执行",
        desc: "自动执行中风险代码；仍不会自动批准文件系统写入。",
        disabledDesc: "开启代码风险分级后可设置。",
        disabledWhen: (settings) => !settings.code_policy_enabled,
        icon: <Shield className="h-4 w-4" />,
        type: "bool",
      },
      {
        key: "tool_schema_validation_mode",
        label: "工具参数校验",
        desc: "影子模式只记录问题；强制模式会拒绝不合规参数。新任务生效。",
        icon: <Shield className="h-4 w-4" />,
        type: "select",
        options: [
          { value: "off", label: "关闭" },
          { value: "shadow", label: "仅记录" },
          { value: "enforce", label: "强制" },
        ],
      },
      {
        key: "tool_schema_validation_canary_percent",
        label: "强制校验比例",
        desc: "强制模式下实际拦截的请求比例；其他请求按仅记录处理。",
        disabledDesc: "选择强制校验后可调整。",
        disabledWhen: (settings) => settings.tool_schema_validation_mode !== "enforce",
        icon: <Gauge className="h-4 w-4" />,
        type: "int",
        min: 0,
        max: 100,
      },
      {
        key: "tool_schema_strict_path",
        label: "严格路径校验",
        desc: "拒绝工具参数中的绝对路径和上级目录穿越。",
        disabledDesc: "开启工具参数校验后可设置。",
        disabledWhen: (settings) => settings.tool_schema_validation_mode === "off",
        icon: <Shield className="h-4 w-4" />,
        type: "bool",
      },
    ],
  },
];

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
    <div className="space-y-5 pb-2">
    <form onSubmit={save} className="space-y-4" aria-busy={busy}>
      <div className="rounded-xl border border-[var(--em-line)] bg-[var(--em-panel)] p-4">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-[13px] font-semibold"><ShieldCheck className="h-4 w-4 text-[var(--em-primary)]" /><label htmlFor="login-protection">登录保护</label></h2>
            <p className="mt-2 text-[11px] leading-[1.65] text-muted-foreground">开启后，需要管理员登录才能使用工作区、文件和设置。</p>
          </div>
          <Switch id="login-protection" checked={enabled} onCheckedChange={setEnabled} disabled={busy} />
        </div>
        {!enabled && <p className="mt-4 rounded-xl bg-amber-500/10 p-3 text-[11px] leading-[1.65] text-amber-800 dark:text-amber-300">关闭后，任何能访问此地址的人都可以直接进入工作区。服务器部署建议保持开启。</p>}
      </div>
      <div className="rounded-xl border border-[var(--em-line)] bg-[var(--em-panel)] p-4">
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
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="flex flex-wrap items-center justify-between gap-3">
        {settings.enabled && <Button type="button" variant="outline" size="sm" className="text-xs" onClick={logout} disabled={busy}><LogOut className="h-4 w-4" />退出登录</Button>}
        <Button type="submit" disabled={busy} size="sm" className="ml-auto text-xs">{busy && <Loader2 className="h-4 w-4 animate-spin" />}保存登录设置</Button>
      </div>
    </form>
    <RuntimeSettingsPanel groups={SECURITY_SETTING_GROUPS} />
    </div>
  );
}
