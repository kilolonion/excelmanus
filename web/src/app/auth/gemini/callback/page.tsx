"use client";

import { useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { CheckCircle2, AlertCircle, Loader2 } from "lucide-react";

/**
 * Google Gemini OAuth PKCE 回调页面。
 *
 * Google 授权完成后重定向到此页面，提取 code 和 state 参数，
 * 通过 window.opener.postMessage 将参数传回主窗口（popup 模式）。
 */

function CallbackContent() {
  const searchParams = useSearchParams();
  const code = searchParams.get("code");
  const state = searchParams.get("state");
  const error = searchParams.get("error") || searchParams.get("error_description");

  useEffect(() => {
    if (!window.opener) return;

    if (error) {
      window.opener.postMessage(
        { type: "provider-oauth-callback", provider: "google-gemini", error },
        window.location.origin,
      );
      setTimeout(() => window.close(), 2000);
      return;
    }

    if (code && state) {
      window.opener.postMessage(
        { type: "provider-oauth-callback", provider: "google-gemini", code, state },
        window.location.origin,
      );
      setTimeout(() => window.close(), 1500);
    }
  }, [code, state, error]);

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background px-4">
        <div className="text-center space-y-3">
          <AlertCircle className="h-10 w-10 mx-auto text-destructive" />
          <h2 className="text-lg font-semibold">授权失败</h2>
          <p className="text-sm text-muted-foreground max-w-xs">{error}</p>
          <p className="text-xs text-muted-foreground">此窗口将自动关闭...</p>
        </div>
      </div>
    );
  }

  if (code && state) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background px-4">
        <div className="text-center space-y-3">
          <CheckCircle2 className="h-10 w-10 mx-auto text-green-500" />
          <h2 className="text-lg font-semibold">授权成功</h2>
          <p className="text-sm text-muted-foreground">正在完成连接，此窗口将自动关闭...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="text-center space-y-3">
        <AlertCircle className="h-10 w-10 mx-auto text-amber-500" />
        <h2 className="text-lg font-semibold">参数缺失</h2>
        <p className="text-sm text-muted-foreground">未收到授权码，请关闭此窗口重试。</p>
      </div>
    </div>
  );
}

export default function GeminiOAuthCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-background">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <CallbackContent />
    </Suspense>
  );
}
