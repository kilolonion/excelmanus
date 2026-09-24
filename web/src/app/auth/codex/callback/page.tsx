"use client";

import { useEffect, useState, useSyncExternalStore, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { CheckCircle2, AlertCircle, Loader2 } from "lucide-react";

/**
 * Codex OAuth PKCE 回调页面（Path A: 本地 localhost 访问时使用）。
 *
 * OpenAI 授权完成后重定向到此页面，提取 code 和 state 参数，
 * 通过 window.opener.postMessage 将参数传回主窗口（popup 模式）。
 */

const subscribe = () => () => {};

function CallbackContent() {
  const searchParams = useSearchParams();
  const code = searchParams.get("code");
  const state = searchParams.get("state");
  const error = searchParams.get("error") || searchParams.get("error_description");
  const hasOpener = useSyncExternalStore(subscribe, () => Boolean(window.opener), () => false);
  const [copyNotice, setCopyNotice] = useState("");

  useEffect(() => {
    if (!window.opener || !state || (!error && !code)) return;
    try {
      window.opener.postMessage(
        { type: "codex-oauth-callback", ...(error ? { error } : { code }), state },
        window.location.origin,
      );
      const timer = setTimeout(() => window.close(), 2000);
      return () => clearTimeout(timer);
    } catch {
      // The settings window may have closed; manual recovery remains available.
    }
  }, [code, state, error]);

  if (error) {
    return (
      <div className="em-auth-gate min-h-screen flex items-center justify-center px-4">
        <div className="em-auth-card w-full max-w-sm rounded-2xl border p-7 text-center shadow-xl space-y-3">
          <AlertCircle className="h-10 w-10 mx-auto text-destructive" />
          <h2 className="text-lg font-semibold">授权失败</h2>
          <p className="text-sm text-muted-foreground max-w-xs">{error}</p>
          <p className="text-xs text-muted-foreground">请返回设置页重新发起登录。</p>
        </div>
      </div>
    );
  }

  if (code && state) {
    return (
      <div className="em-auth-gate min-h-screen flex items-center justify-center px-4">
        <div className="em-auth-card w-full max-w-sm rounded-2xl border p-7 text-center shadow-xl space-y-3">
          <CheckCircle2 className="h-10 w-10 mx-auto text-green-500" />
          <h2 className="text-lg font-semibold">已收到授权信息</h2>
          <p className="text-sm text-muted-foreground">{hasOpener ? "正在交回授权信息，请返回设置页查看连接结果。" : "请复制完整回调地址，返回设置页粘贴以完成连接。"}</p>
          <button type="button" className="text-sm text-primary underline" onClick={async () => {
            try { await navigator.clipboard.writeText(window.location.href); setCopyNotice("已复制，请返回设置页粘贴。"); }
            catch { setCopyNotice("请手动复制地址栏中的完整地址。"); }
          }}>复制回调地址</button>
          {copyNotice && <p role="status" className="text-xs text-muted-foreground">{copyNotice}</p>}
        </div>
      </div>
    );
  }

  return (
    <div className="em-auth-gate min-h-screen flex items-center justify-center px-4">
      <div className="em-auth-card w-full max-w-sm rounded-2xl border p-7 text-center shadow-xl space-y-3">
        <AlertCircle className="h-10 w-10 mx-auto text-amber-500" />
        <h2 className="text-lg font-semibold">参数缺失</h2>
        <p className="text-sm text-muted-foreground">未收到授权码，请关闭此窗口重试。</p>
      </div>
    </div>
  );
}

export default function CodexOAuthCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="em-auth-gate min-h-screen flex items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <CallbackContent />
    </Suspense>
  );
}
