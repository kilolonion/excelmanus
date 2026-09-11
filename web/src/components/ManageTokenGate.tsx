"use client";

import { FormEvent, useState } from "react";
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
    <div className="flex min-h-screen items-center justify-center bg-background p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-md rounded-xl border border-border bg-card p-6 shadow-sm"
      >
        <h1 className="text-lg font-semibold">需要管理令牌</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          此实例启用了 EXCELMANUS_MANAGE_TOKEN。请输入令牌后继续。令牌只保存在本标签页的 sessionStorage。
        </p>
        <input
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="mt-4 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          placeholder="管理令牌"
        />
        {error ? <p className="mt-2 text-sm text-destructive">{error}</p> : null}
        <button
          type="submit"
          className="mt-4 inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
        >
          继续
        </button>
      </form>
    </div>
  );
}
