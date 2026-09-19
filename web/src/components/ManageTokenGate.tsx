"use client";

import { FormEvent, useState } from "react";
import { ArrowRight, KeyRound, ShieldCheck } from "lucide-react";
import { setManageToken } from "@/lib/api";

export function ManageTokenGate({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const token = value.trim();
    if (token.length < 16) {
      setError("令牌至少 16 个字符");
      return;
    }
    setManageToken(token);
    onSaved();
  };

  return (
    <div className="em-auth-gate flex min-h-screen items-center justify-center p-4 sm:p-6">
      <form
        onSubmit={submit}
        className="em-auth-card w-full max-w-md rounded-[22px] border p-6 shadow-xl sm:p-8"
      >
        <div className="flex items-center gap-3">
          <div className="em-brand-mark" aria-hidden="true">E</div>
          <div>
            <p className="text-sm font-semibold text-[var(--em-ink)]">ExcelManus</p>
            <p className="text-[11px] text-[var(--em-muted)]">安全工作区</p>
          </div>
        </div>
        <div className="mt-8 flex items-start gap-3">
          <div className="em-auth-icon"><ShieldCheck className="h-5 w-5" /></div>
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-[var(--em-ink)]">需要管理令牌</h1>
            <p className="mt-1.5 text-sm leading-6 text-[var(--em-muted)]">
          此实例启用了 EXCELMANUS_MANAGE_TOKEN。请输入令牌后继续。令牌只保存在本标签页的 sessionStorage。
            </p>
          </div>
        </div>
        <label className="mt-6 block text-xs font-medium text-[var(--em-muted)]" htmlFor="manage-token">
          管理令牌
        </label>
        <div className="relative mt-2">
          <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--em-muted)]" />
          <input
          id="manage-token"
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="h-11 w-full rounded-xl border border-[var(--em-line)] bg-card/80 pl-10 pr-3 text-sm outline-none transition focus:border-[var(--em-primary-light)] focus:ring-4 focus:ring-[var(--em-primary)]/10"
          placeholder="管理令牌"
          />
        </div>
        {error ? <p className="mt-2 text-sm text-destructive">{error}</p> : <p className="mt-2 text-[11px] text-[var(--em-muted)]">至少 16 个字符</p>}
        <button
          type="submit"
          className="mt-5 inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-[var(--em-primary)] px-4 text-sm font-semibold text-white shadow-md shadow-[var(--em-primary)]/20 transition hover:bg-[var(--em-primary-dark)] focus:outline-none focus:ring-4 focus:ring-[var(--em-primary)]/15"
        >
          进入工作区
          <ArrowRight className="h-4 w-4" />
        </button>
      </form>
    </div>
  );
}
