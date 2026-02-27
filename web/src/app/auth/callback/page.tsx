"use client";

import { useEffect, useState, useRef, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, AlertCircle, CheckCircle2 } from "lucide-react";
import { handleOAuthCallback, fetchCurrentUser } from "@/lib/auth-api";
import { useAuthStore } from "@/stores/auth-store";
import { Button } from "@/components/ui/button";

type CallbackState = "processing" | "success" | "error";

function CallbackHandler() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [state, setState] = useState<CallbackState>("processing");
  const [error, setError] = useState("");
  const processed = useRef(false);

  useEffect(() => {
    if (processed.current) return;
    processed.current = true;

    const accessToken = searchParams.get("access_token");
    const refreshToken = searchParams.get("refresh_token");
    const errorParam = searchParams.get("error") || searchParams.get("error_description");

    if (errorParam) {
      setError(errorParam);
      setState("error");
      return;
    }

    if (accessToken && refreshToken) {
      const { setTokens } = useAuthStore.getState();
      setTokens(accessToken, refreshToken);
      fetchCurrentUser()
        .then(() => {
          setState("success");
          setTimeout(() => router.replace("/"), 600);
        })
        .catch(() => {
          setState("success");
          setTimeout(() => router.replace("/"), 600);
        });
      return;
    }

    const code = searchParams.get("code");
    if (!code) {
      setError("缺少授权码");
      setState("error");
      return;
    }

    const stateVal = searchParams.get("state") || "";
    const providerHint = searchParams.get("provider") || stateVal.split(":")[0];
    let provider: "github" | "google" | "qq" = "github";
    if (providerHint === "google") provider = "google";
    else if (providerHint === "qq") provider = "qq";

    handleOAuthCallback(provider, code, searchParams.get("state") || undefined)
      .then(() => {
        setState("success");
        setTimeout(() => router.replace("/"), 600);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "OAuth 认证失败");
        setState("error");
      });
  }, [searchParams, router]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-b from-background to-muted/30 px-4">
      <div className="w-full max-w-xs text-center space-y-4">
        {state === "processing" && (
          <>
            <Loader2 className="h-10 w-10 mx-auto animate-spin text-[var(--em-primary)]" />
            <div>
              <h2 className="text-lg font-semibold">正在完成登录</h2>
              <p className="text-sm text-muted-foreground mt-1">请稍候...</p>
            </div>
          </>
        )}

        {state === "success" && (
          <>
            <CheckCircle2 className="h-10 w-10 mx-auto text-green-500" />
            <div>
              <h2 className="text-lg font-semibold">登录成功</h2>
              <p className="text-sm text-muted-foreground mt-1">正在跳转...</p>
            </div>
          </>
        )}

        {state === "error" && (
          <>
            <AlertCircle className="h-10 w-10 mx-auto text-destructive" />
            <div>
              <h2 className="text-lg font-semibold">登录失败</h2>
              <p className="text-sm text-destructive mt-1">{error}</p>
            </div>
            <div className="flex gap-2 justify-center pt-2">
              <Button variant="outline" onClick={() => router.push("/login")}>
                返回登录
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function OAuthCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-background">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <CallbackHandler />
    </Suspense>
  );
}
