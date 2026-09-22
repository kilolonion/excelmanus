"use client";

import { useState } from "react";
import { Copy, ExternalLink, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { useOAuthLogin, usePollingLogin } from "./useSubscriptionLogin";

export function OAuthLoginProgress({ login }: { login: ReturnType<typeof useOAuthLogin> }) {
  const exchanging = login.phase === "exchanging";
  return <div className="space-y-3 rounded-lg border border-border bg-muted/25 p-3">
    <div className="flex items-start gap-2" role="status">
      <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-primary" />
      <div className="space-y-1 text-xs">
        <p className="font-medium">{login.phase === "starting" ? "正在准备登录…" : exchanging ? "正在完成连接…" : "请在登录窗口中完成授权"}</p>
        <p className="leading-relaxed text-muted-foreground">{login.notice || "授权成功后，这里的账号和模型列表会自动更新。"}</p>
      </div>
    </div>
    {!exchanging && login.authorizeUrl && <div className="flex flex-wrap items-center gap-3 text-xs">
      <a href={login.authorizeUrl} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-8 items-center gap-1 font-medium text-primary hover:underline"><ExternalLink className="size-3" />打开登录页</a>
      <button type="button" aria-expanded={login.manual} className="min-h-8 text-muted-foreground hover:text-foreground" onClick={() => login.setManual(!login.manual)}>{login.manual ? "收起回调地址输入" : "已授权但未自动返回？"}</button>
    </div>}
    {!exchanging && login.manual && <form className="space-y-2" onSubmit={(event) => { event.preventDefault(); void login.submit(); }}>
      <label className="block space-y-2 text-xs">
        <span className="text-muted-foreground">授权后，复制地址栏中的完整回调地址。即使页面显示无法访问，也可以复制。</span>
        <Input aria-label="授权回调地址" value={login.pasteUrl} onChange={(event) => login.setPasteUrl(event.target.value)} placeholder="粘贴授权完成后的完整地址" autoComplete="off" spellCheck={false} className="h-9 text-xs" />
      </label>
      <Button type="submit" size="sm" disabled={!login.pasteUrl.trim()} className="h-9 text-xs">完成连接</Button>
    </form>}
    <Button size="sm" variant="ghost" className="h-8 text-xs text-muted-foreground" onClick={login.cancel} disabled={exchanging}>取消登录</Button>
  </div>;
}

export function PollingLoginProgress({ login, title }: { login: ReturnType<typeof usePollingLogin>; title: string }) {
  const [copyMessage, setCopyMessage] = useState("");
  return <div className="space-y-3 rounded-lg border border-border bg-muted/25 p-3">
    <div className="flex items-center gap-2 text-xs" role="status"><Loader2 className="size-4 animate-spin text-primary" /><span>{login.session ? `请在浏览器中完成${title}` : "正在准备登录…"}</span></div>
    {login.session && <>
      {login.session.code && <div className="flex flex-wrap items-center gap-2">
        <code className="select-all rounded-lg border bg-background px-3 py-2 text-lg font-semibold tracking-widest">{login.session.code}</code>
        <Button size="sm" variant="outline" className="h-8 gap-1 text-xs" onClick={async () => {
          try { await navigator.clipboard.writeText(login.session?.code || ""); setCopyMessage("验证码已复制"); }
          catch { setCopyMessage("无法自动复制，请选中验证码手动复制。"); }
        }}><Copy className="size-3" />复制验证码</Button>
        {copyMessage && <span role="status" className="text-xs text-muted-foreground">{copyMessage}</span>}
      </div>}
      <a href={login.session.url} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-8 items-center gap-1 text-xs font-medium text-primary hover:underline"><ExternalLink className="size-3" />打开登录页{login.session.code ? "并输入验证码" : ""}</a>
      <p className="text-xs leading-relaxed text-muted-foreground">若登录页未打开或已关闭，可点击上方链接继续。完成授权后会自动更新。</p>
    </>}
    <Button size="sm" variant="ghost" className="h-8 text-xs text-muted-foreground" onClick={login.cancel}>取消登录</Button>
  </div>;
}
