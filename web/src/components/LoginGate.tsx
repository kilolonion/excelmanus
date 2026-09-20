"use client";

import { useState, type FormEvent } from "react";
import Image from "next/image";
import { ArrowRight, Eye, EyeOff, KeyRound, Loader2, ShieldCheck, UserRound } from "lucide-react";
import { loginToInstance, type AccessStatus } from "@/lib/access-api";

export function LoginGate({ method, onSignedIn }: {
  method: AccessStatus["login_method"];
  onSignedIn: (status: AccessStatus) => Promise<void>;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const tokenMode = method === "token";
  const inputClass = "h-12 w-full rounded-xl border border-[var(--em-line)] bg-[var(--em-panel)] pl-11 pr-12 text-[13px] outline-none transition focus:border-[var(--em-primary-light)] focus:ring-4 focus:ring-[var(--em-primary)]/10 disabled:opacity-60";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const status = await loginToInstance(username.trim(), password);
      setPassword("");
      await onSignedIn(status);
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="em-auth-gate flex min-h-[100dvh] items-center justify-center px-5 py-10 text-[var(--em-ink)]">
      <div className="w-full max-w-[440px]">
        <div className="mb-7 flex items-center justify-center gap-3">
          <Image src="/brand-icon.svg" alt="" width={32} height={32} />
          <span className="text-lg font-semibold tracking-tight">ExcelManus</span>
        </div>
        <form onSubmit={submit} className="em-auth-card rounded-[24px] border p-6 shadow-sm sm:p-9" aria-busy={busy}>
          <div className="mb-6 inline-flex items-center gap-2 rounded-full bg-[var(--em-primary-alpha-08)] px-3 py-1.5 text-xs font-medium text-[var(--em-primary)]">
            <ShieldCheck className="h-3.5 w-3.5" />受保护的工作区
          </div>
          <h1 className="text-[22px] font-semibold tracking-tight">登录工作区</h1>
          <p className="mt-2 text-xs leading-5 text-[var(--em-muted)]">{tokenMode ? "输入管理员提供的访问令牌，继续使用。" : "使用管理员账号登录，继续处理你的文件。"}</p>
          {!tokenMode && <div className="mt-7">
            <label htmlFor="instance-username" className="mb-2 block text-xs font-medium">管理员账号</label>
            <div className="relative">
              <UserRound className="pointer-events-none absolute left-4 top-4 h-4 w-4 text-[var(--em-muted)]" />
              <input id="instance-username" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required maxLength={128} value={username} onChange={(event) => setUsername(event.target.value)} disabled={busy} className={inputClass} placeholder="输入账号" />
            </div>
          </div>}
          <div className={tokenMode ? "mt-7" : "mt-5"}>
            <label htmlFor="instance-password" className="mb-2 block text-xs font-medium">{tokenMode ? "访问令牌" : "密码"}</label>
            <div className="relative">
              <KeyRound className="pointer-events-none absolute left-4 top-4 h-4 w-4 text-[var(--em-muted)]" />
              <input id="instance-password" name="password" type={visible ? "text" : "password"} autoComplete="current-password" required maxLength={4096} value={password} onChange={(event) => setPassword(event.target.value)} disabled={busy} aria-invalid={!!error} aria-describedby={error ? "login-error" : undefined} className={inputClass} placeholder={tokenMode ? "输入访问令牌" : "输入密码"} />
              <button type="button" onClick={() => setVisible(!visible)} aria-label={visible ? "隐藏密码" : "显示密码"} className="absolute right-1 top-1 flex h-10 w-10 items-center justify-center rounded-lg text-[var(--em-muted)] hover:bg-[var(--em-fill)] focus-visible:outline-2 focus-visible:outline-[var(--em-primary)]">
                {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>
          {error && <p id="login-error" role="alert" className="mt-4 rounded-lg bg-destructive/5 p-3 text-sm text-destructive">{error}</p>}
          <button type="submit" disabled={busy} className="mt-7 inline-flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[var(--em-primary)] px-4 text-[13px] font-semibold text-white transition hover:bg-[var(--em-primary-dark)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--em-primary)] disabled:cursor-wait disabled:opacity-60">
            {busy ? <><Loader2 className="h-4 w-4 animate-spin" />正在登录…</> : <>登录<ArrowRight className="h-4 w-4" /></>}
          </button>
        </form>
        <p className="mt-6 text-center text-xs leading-5 text-[var(--em-muted)]">仅限此实例的管理员访问 · 如需帮助，请联系部署者</p>
      </div>
    </main>
  );
}
